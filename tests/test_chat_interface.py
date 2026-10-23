"""Tests for the user-facing Agent Chat Protocol (ACP) interface."""

import pytest
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
)
from freightpact.chat_interface import (
    create_chat_acknowledgement,
    create_text_chat_message,
    format_recovery_response,
    parse_incident_fallback,
    validate_or_request_missing_fields,
)
from freightpact.models import CargoClass, CarrierOffer, RecoveryRequest, RecoveryResult
from freightpact.states import RunContext, WorkflowState


def test_create_chat_acknowledgement():
    msg = create_text_chat_message("Hello FreightPact")
    ack = create_chat_acknowledgement(msg.msg_id)
    assert isinstance(ack, ChatAcknowledgement)
    assert ack.acknowledged_msg_id == msg.msg_id
    assert ack.metadata is not None
    assert ack.metadata["status"] == "received"


def test_create_text_chat_message():
    msg = create_text_chat_message("Test message")
    assert isinstance(msg, ChatMessage)
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], TextContent)
    assert msg.text() == "Test message"


def test_parse_incident_fallback_full_parameters():
    text = (
        "Emergency! Reefer unit breakdown on shipment VAX-8821 carrying Vaccines "
        "at Sector 18 Gurugram. Temperature rising fast! Target band: 2°C to 8°C. "
        "Budget ceiling ₹48,000, deadline 45 minutes, capacity 1000kg."
    )
    data = parse_incident_fallback(text)
    assert data["shipment_id"] == "VAX-8821"
    assert data["temp_min_c"] == 2.0
    assert data["temp_max_c"] == 8.0
    assert data["max_budget"] == 48000.0
    assert data["deadline_minutes"] == 45
    assert data["cargo_class"] == CargoClass.VACCINES
    assert data["required_capacity_kg"] == 1000.0

    req, prompt = validate_or_request_missing_fields(data)
    assert prompt is None
    assert req is not None
    assert req.shipment_id == "VAX-8821"
    assert req.temp_min_c == 2.0
    assert req.temp_max_c == 8.0
    assert req.max_budget == 48000.0
    assert req.deadline_minutes == 45


def test_validate_or_request_missing_fields_prompts_when_parameters_missing():
    # Only shipment ID provided
    text = "Shipment VAX-8821 has broken down near Jaipur"
    data = parse_incident_fallback(text)

    req, prompt = validate_or_request_missing_fields(data)
    assert req is None
    assert prompt is not None
    assert "Target Temperature Band" in prompt
    assert "Maximum Budget Ceiling" in prompt
    assert "Recovery Deadline" in prompt
    assert "FreightPact Cold-Chain Incident Intake" in prompt


def test_format_recovery_response_includes_failover_and_disclosure():
    req = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Gurugram",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )
    run_ctx = RunContext(req)
    # Add simulated offer & rejections
    run_ctx.offers = [
        CarrierOffer(
            offer_id="off_1",
            run_id=req.run_id,
            carrier_id="carrier_beta",
            carrier_name="Beta Rapid Logistics",
            price=38000.0,
            eta_minutes=32,
            capacity_kg=1200.0,
            supported_temp_min_c=-5.0,
            supported_temp_max_c=10.0,
            is_simulated=True,
        ),
        CarrierOffer(
            offer_id="off_2",
            run_id=req.run_id,
            carrier_id="carrier_alpha",
            carrier_name="Alpha Cold Pharma Transport",
            price=41500.0,
            eta_minutes=38,
            capacity_kg=1500.0,
            supported_temp_min_c=-20.0,
            supported_temp_max_c=15.0,
            is_simulated=True,
        ),
    ]
    run_ctx.record_rejected_candidate(
        "carrier_beta",
        "Temperature non-compliant: recorded 13.8°C is outside required safe range [2.0°C to 8.0°C]",
    )
    run_ctx.selected_offer = run_ctx.offers[1]
    run_ctx.transition_to(WorkflowState.VALIDATED)
    run_ctx.transition_to(WorkflowState.COLLECTING_QUOTES)
    run_ctx.transition_to(WorkflowState.RANKING)
    run_ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    run_ctx.transition_to(WorkflowState.RECOMMENDED_FOR_CONFIRMATION)

    terminal_result = run_ctx.build_terminal_result()
    report = format_recovery_response(req, run_ctx, terminal_result)

    assert "FreightPact Cold-Chain Recovery Report" in report
    assert "VAX-8821" in report
    assert "Beta Rapid Logistics" in report
    assert "Alpha Cold Pharma Transport" in report
    assert "REJECTED `carrier_beta`" in report
    assert "VERIFIED `carrier_alpha`" in report
    assert "13.8°C" in report
    assert "Simulation Disclosure" in report
    assert "SIMULATION — awaiting user confirmation" in report
    assert "actual physical vehicle dispatch" in report
    assert "[!NOTE]" not in report


@pytest.mark.asyncio
async def test_acp_chat_handler_missing_fields_emits_ack_and_prompt():
    from freightpact.chat_interface import create_acp_chat_protocol
    from freightpact.coordinator_agent import CoordinatorOrchestrator

    orchestrator = CoordinatorOrchestrator(
        auditor_address="auditor",
        carrier_addresses={"carrier_alpha": "alpha"},
    )
    proto = create_acp_chat_protocol(orchestrator)
    # Get ChatMessage handler
    msg_handler = proto._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)]

    class MockContext:
        def __init__(self):
            self.logger = type("L", (), {"info": lambda *a: None, "warning": lambda *a: None, "error": lambda *a: None})()
            self.sent_messages = []

        async def send(self, destination, message):
            self.sent_messages.append((destination, message))

    mock_ctx = MockContext()
    user_msg = create_text_chat_message("Shipment VAX-8821 needs help")
    sender_addr = "agent1qtestsenderuseraddress12345"

    await msg_handler(mock_ctx, sender_addr, user_msg)

    # Must have sent ChatAcknowledgement promptly, then prompt for missing fields
    assert len(mock_ctx.sent_messages) == 2
    dest1, ack = mock_ctx.sent_messages[0]
    assert dest1 == sender_addr
    assert isinstance(ack, ChatAcknowledgement)
    assert ack.acknowledged_msg_id == user_msg.msg_id

    dest2, reply = mock_ctx.sent_messages[1]
    assert dest2 == sender_addr
    assert isinstance(reply, ChatMessage)
    assert "FreightPact Cold-Chain Incident Intake" in reply.text()
    assert "Target Temperature Band" in reply.text()


@pytest.mark.asyncio
async def test_acp_chat_handler_full_incident_without_explicit_cargo_class_regression():
    """Regression test ensuring chat handler does not raise AttributeError when cargo_class keyword is absent."""
    from freightpact.chat_interface import create_acp_chat_protocol
    from freightpact.coordinator_agent import CoordinatorOrchestrator
    from freightpact.storage import StorageRepository

    orchestrator = CoordinatorOrchestrator(
        auditor_address="auditor",
        carrier_addresses={"carrier_alpha": "alpha"},
        storage=StorageRepository(":memory:"),
        quote_timeout_seconds=0.1,
    )
    proto = create_acp_chat_protocol(orchestrator)
    msg_handler = proto._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)]

    class MockContext:
        def __init__(self):
            self.logger = type("L", (), {"info": lambda *a: None, "warning": lambda *a: None, "error": lambda *a: None})()
            self.sent_messages = []

        async def send(self, destination, message):
            self.sent_messages.append((destination, message))

    mock_ctx = MockContext()
    # Message provides temperature, budget, deadline, shipment_id but NO explicit cargo keyword
    user_msg = create_text_chat_message("Shipment VAX-8821 temp 2 to 8 budget 48000 deadline 45 mins")
    sender_addr = "agent1qtestsenderuseraddress12345"

    # Must complete without AttributeError on CargoClass.PHARMACEUTICALS
    await msg_handler(mock_ctx, sender_addr, user_msg)
    assert len(mock_ctx.sent_messages) >= 2
    assert isinstance(mock_ctx.sent_messages[0][1], ChatAcknowledgement)
    assert isinstance(mock_ctx.sent_messages[-1][1], ChatMessage)
    assert "FreightPact Cold-Chain Recovery Report" in mock_ctx.sent_messages[-1][1].text()


@pytest.mark.asyncio
async def test_acp_chat_handler_navigation_actions_round_trip():
    """Validates that navigation actions return correct cards without restarting recovery runs."""
    import json
    from freightpact.chat_interface import create_acp_chat_protocol
    from freightpact.coordinator_agent import CoordinatorOrchestrator
    from freightpact.models import AuditVerdict
    from freightpact.states import WorkflowState

    req = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Gurugram",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )

    orchestrator = CoordinatorOrchestrator(
        auditor_address="auditor",
        carrier_addresses={"carrier_alpha": "alpha", "carrier_beta": "beta"},
    )
    run_ctx = orchestrator.init_mission(req)
    run_ctx.offers = [
        CarrierOffer(
            offer_id="off_beta",
            run_id=req.run_id,
            carrier_id="carrier_beta",
            carrier_name="Beta Rapid Logistics",
            price=38000.0,
            eta_minutes=32,
            capacity_kg=1200.0,
            supported_temp_min_c=-5.0,
            supported_temp_max_c=10.0,
            is_simulated=True,
        ),
        CarrierOffer(
            offer_id="off_alpha",
            run_id=req.run_id,
            carrier_id="carrier_alpha",
            carrier_name="Alpha Cold Pharma Transport",
            price=41500.0,
            eta_minutes=38,
            capacity_kg=1500.0,
            supported_temp_min_c=-20.0,
            supported_temp_max_c=15.0,
            is_simulated=True,
        ),
    ]
    run_ctx.selected_offer = run_ctx.offers[1]
    run_ctx.rejection_reasons["carrier_beta"] = "Temperature non-compliant: recorded 13.8°C"
    run_ctx.audit_verdicts["carrier_alpha"] = AuditVerdict(
        candidate_id="carrier_alpha",
        run_id=req.run_id,
        nonce="test_nonce_999",
        passed=True,
        reason="Cryptographically compliant telemetry",
        recorded_temp_c=4.2,
        compressor_active=True,
        signature_valid=True,
        timestamp_fresh=True,
    )
    run_ctx.transition_to(WorkflowState.VALIDATED)
    run_ctx.transition_to(WorkflowState.COLLECTING_QUOTES)
    run_ctx.transition_to(WorkflowState.RANKING)
    run_ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    run_ctx.transition_to(WorkflowState.RECOMMENDED_FOR_CONFIRMATION)

    proto = create_acp_chat_protocol(orchestrator)
    msg_handler = proto._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)]

    class MockContext:
        def __init__(self):
            self.logger = type("L", (), {"info": lambda *a: None, "warning": lambda *a: None, "error": lambda *a: None})()
            self.sent_messages = []

        async def send(self, destination, message):
            self.sent_messages.append((destination, message))

    # Test View A: Incident Overview navigation
    mock_ctx = MockContext()
    nav_msg = create_text_chat_message(json.dumps({"action": "view_incident_overview", "run_id": req.run_id}))
    await msg_handler(mock_ctx, "user", nav_msg)
    assert len(mock_ctx.sent_messages) == 2
    reply_a = mock_ctx.sent_messages[1][1]
    assert "Incident Overview" in reply_a.text()
    assert len(reply_a.content) == 2
    assert reply_a.content[1].metadata["card_kind"] == "detail"

    # Test View B: Carrier Comparison navigation
    mock_ctx = MockContext()
    nav_msg = create_text_chat_message(json.dumps({"action": "view_carrier_comparison", "run_id": req.run_id}))
    await msg_handler(mock_ctx, "user", nav_msg)
    reply_b = mock_ctx.sent_messages[1][1]
    assert "Candidate Carrier Comparison" in reply_b.text()
    assert reply_b.content[1].metadata["card_kind"] == "carousel"

    # Test View C: Inspect Telemetry (Beta - Rejected)
    mock_ctx = MockContext()
    nav_msg = create_text_chat_message(json.dumps({"action": "inspect_telemetry", "carrier_id": "carrier_beta", "run_id": req.run_id}))
    await msg_handler(mock_ctx, "user", nav_msg)
    reply_c = mock_ctx.sent_messages[1][1]
    assert "Beta Rapid Logistics" in reply_c.text()
    assert reply_c.content[1].metadata["card_kind"] == "detail"
    payload_c = json.loads(reply_c.content[1].metadata["card_payload"])
    vals_c = {r["label"]: r["value"] for r in payload_c["summary_rows"]}
    assert "13.8°C" in vals_c["Recorded Reefer Temp"]

    # Test View D: Final Award Review navigation
    mock_ctx = MockContext()
    nav_msg = create_text_chat_message(json.dumps({"action": "view_award_review", "run_id": req.run_id}))
    await msg_handler(mock_ctx, "user", nav_msg)
    reply_d = mock_ctx.sent_messages[1][1]
    assert "Final Dispatch Review" in reply_d.text()
    assert reply_d.content[1].metadata["card_kind"] == "review"

    # Test Confirm Award & Idempotency
    mock_ctx = MockContext()
    confirm_msg = create_text_chat_message(json.dumps({"action": "confirm_award", "carrier_id": "carrier_alpha", "run_id": req.run_id}))
    await msg_handler(mock_ctx, "user", confirm_msg)
    reply_conf = mock_ctx.sent_messages[1][1]
    assert "Simulated Carrier Award Confirmed" in reply_conf.text()
    assert "[!NOTE]" not in reply_conf.text()
    assert getattr(run_ctx, "is_confirmed", False) is True
    # Verify receipt card is attached
    assert len(reply_conf.content) == 2
    assert reply_conf.content[1].metadata["card_kind"] == "detail"
    assert reply_conf.content[1].metadata["is_terminal"] == "true"
    assert reply_conf.content[1].metadata["requires_card_interaction"] == "true"

    # Repeat Confirm Award (Idempotent call)
    mock_ctx2 = MockContext()
    await msg_handler(mock_ctx2, "user", confirm_msg)
    reply_conf2 = mock_ctx2.sent_messages[1][1]
    assert "Simulated Carrier Award Already Confirmed" in reply_conf2.text()
    assert "[!NOTE]" not in reply_conf2.text()
    assert len(reply_conf2.content) == 2
    assert reply_conf2.content[1].metadata["card_kind"] == "detail"
    assert reply_conf2.content[1].metadata["requires_card_interaction"] == "true"

    # Test Reject Award & Declination Receipt Card
    mock_ctx3 = MockContext()
    reject_msg = create_text_chat_message(json.dumps({"action": "reject_award", "carrier_id": "carrier_alpha", "run_id": req.run_id}))
    await msg_handler(mock_ctx3, "user", reject_msg)
    reply_decl = mock_ctx3.sent_messages[1][1]
    assert "Simulated Carrier Recommendation Declined" in reply_decl.text()
    assert "[!NOTE]" not in reply_decl.text()
    assert len(reply_decl.content) == 2
    assert reply_decl.content[1].metadata["card_kind"] == "detail"
    assert reply_decl.content[1].metadata["is_terminal"] == "true"
    assert reply_decl.content[1].metadata["requires_card_interaction"] == "true"

    # Test View Mission Overview from Confirmation Receipt (Requirement 5)
    # Ensure it returns the overview once without recursive review loop action
    mock_ctx4 = MockContext()
    overview_msg = create_text_chat_message(json.dumps({"action": "view_incident_overview", "run_id": req.run_id}))
    await msg_handler(mock_ctx4, "user", overview_msg)
    reply_ov = mock_ctx4.sent_messages[1][1]
    assert "Incident Overview" in reply_ov.text()
    assert len(reply_ov.content) == 2
    assert reply_ov.content[1].metadata["card_kind"] == "detail"
    payload_ov = json.loads(reply_ov.content[1].metadata["card_payload"])
    ov_actions = [cta["selection"]["action"] for cta in payload_ov.get("ctas", [])]
    assert "view_award_review" not in ov_actions
    assert "view_incident_overview" not in ov_actions
    assert "view_carrier_comparison" in ov_actions
    ov_labels = {r["label"]: r["value"] for r in payload_ov["summary_rows"]}
    assert "Simulated Award Confirmed" in ov_labels["Current Status"]

def test_observed_incident_message_parsing_and_field_extraction():
    """Regression test: Verifies exact field extraction for the user's reported live incident message."""
    raw_text = (
        "Hi, I need an urgent replacement for vaccine shipment VAX-8821 near Sector 18, Gurugram. "
        "The required temperature range is 2–8°C, the maximum budget is ₹48,000, and the deadline is 45 minutes. "
        "Please evaluate available carriers and recommend a safe replacement."
    )
    parsed = parse_incident_fallback(raw_text)
    assert parsed.get("shipment_id") == "VAX-8821"
    assert parsed.get("cargo_class") == CargoClass.VACCINES
    assert parsed.get("temp_min_c") == 2.0
    assert parsed.get("temp_max_c") == 8.0
    assert parsed.get("max_budget") == 48000.0
    assert parsed.get("deadline_minutes") == 45
    assert parsed.get("origin_location") == "Sector 18, Gurugram"

    req, prompt = validate_or_request_missing_fields(parsed)
    assert prompt is None
    assert req is not None
    assert req.shipment_id == "VAX-8821"
    assert req.temp_min_c == 2.0
    assert req.temp_max_c == 8.0
    assert req.max_budget == 48000.0
    assert req.deadline_minutes == 45
    assert req.origin_location == "Sector 18, Gurugram"

    # Test with leading Agentverse mention
    mentioned_text = "@agent1q2jv8khzjg4ycngm5zk5e8mkad774ptja3ufjfwjujwu9x5scexu6pu82ja " + raw_text
    parsed_m = parse_incident_fallback(mentioned_text)
    assert parsed_m.get("shipment_id") == "VAX-8821"
    assert parsed_m.get("deadline_minutes") == 45
    assert parsed_m.get("max_budget") == 48000.0
    assert parsed_m.get("temp_min_c") == 2.0
    assert parsed_m.get("temp_max_c") == 8.0


@pytest.mark.asyncio
async def test_observed_incident_message_triggers_recovery_not_mission_not_found():
    """Regression test: Verifies that receiving the user's incident with no active missions initiates recovery."""
    from freightpact.chat_interface import create_acp_chat_protocol
    from freightpact.coordinator_agent import CoordinatorOrchestrator
    from freightpact.storage import StorageRepository
    from uagents_core.contrib.protocols.chat import MetadataContent

    orchestrator = CoordinatorOrchestrator(
        auditor_address="auditor",
        carrier_addresses={"carrier_alpha": "alpha"},
        storage=StorageRepository(":memory:"),
        quote_timeout_seconds=0.1,
    )
    # Ensure initially empty runs
    assert len(orchestrator.runs) == 0

    proto = create_acp_chat_protocol(orchestrator)
    msg_handler = proto._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)]

    class MockContext:
        def __init__(self):
            self.logger = type("L", (), {"info": lambda *a: None, "warning": lambda *a: None, "error": lambda *a: None})()
            self.sent_messages = []

        async def send(self, destination, message):
            self.sent_messages.append((destination, message))

    mock_ctx = MockContext()
    raw_text = (
        "Hi, I need an urgent replacement for vaccine shipment VAX-8821 near Sector 18, Gurugram. "
        "The required temperature range is 2–8°C, the maximum budget is ₹48,000, and the deadline is 45 minutes. "
        "Please evaluate available carriers and recommend a safe replacement."
    )
    user_msg = create_text_chat_message(raw_text)
    await msg_handler(mock_ctx, "user_address_123", user_msg)

    # Must NOT reply with 'Mission Not Found'
    assert len(mock_ctx.sent_messages) >= 2
    reply = mock_ctx.sent_messages[-1][1]
    assert isinstance(reply, ChatMessage)
    assert "Mission Not Found" not in reply.text()
    assert "FreightPact Cold-Chain Recovery Report" in reply.text()
    assert "VAX-8821" in reply.text()

    # Must attach overview card
    card_contents = [c for c in reply.content if isinstance(c, MetadataContent)]
    assert len(card_contents) == 1
    assert card_contents[0].metadata["card_kind"] == "detail"

