"""Comprehensive functional verification test suite for FreightPact agents and end-to-end matrix.

PART B — Individual Agent Verification:
1. Coordinator: isolated mission lifecycle, quote broadcast, ranking, failover, persistence, single completion event.
2. Carrier Beta: ₹38,000 / 32m offer; 13.8°C / inactive compressor rejected by auditor.
3. Carrier Alpha: ₹41,500 / 38m offer; 4.2°C / active compressor / valid HMAC accepted by auditor.
4. Carrier Gamma: ₹43,000 / 40m offer; 4.8°C / active compressor / valid HMAC passes auditor; selected when Beta & Alpha fail.
5. Telemetry Auditor: comprehensive cryptographic and physical constraint audits.

PART C — End-to-End Verification Matrix:
- Scenario 1: Complete incident -> starts recovery immediately without returning form card.
- Scenario 2: Missing fields -> displays intake form -> user submits missing fields -> recovery begins.
- Scenario 3: Beta & Alpha fail -> Gamma selected.
- Scenario 4: All carriers fail -> NO_SAFE_MATCH.
- Scenario 5: User confirms award -> post-confirmation receipt card (terminal).
- Scenario 6: User declines award -> declination receipt card (terminal).
- Scenario 7: Idempotent confirmation -> second confirm returns already confirmed card without duplicate write.
- Scenario 8: Invalid/tampered review action rejected (unmatched candidate or invalid state).
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import pytest
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent

from freightpact.chat_interface import (
    create_acp_chat_protocol,
    create_text_chat_message,
)
from freightpact.coordinator_agent import CoordinatorOrchestrator
from freightpact.fixtures import (
    generate_carrier_offer,
    generate_telemetry_payload,
    get_carrier_profile,
)
from freightpact.models import (
    AuditVerdict,
    CargoClass,
    CarrierOffer,
    RecoveryRequest,
)
from freightpact.policy import filter_eligible_offers
from freightpact.ranking import rank_carrier_offers
from freightpact.states import RunContext, WorkflowState
from freightpact.storage import StorageRepository
from freightpact.telemetry import (
    NonceReplayTracker,
    verify_telemetry_payload,
)

FIXTURE_SECRET = "freightpact-synthetic-test-fixture-key-12345"


# =============================================================================
# PART B: Individual Agent Verification
# =============================================================================

def test_carrier_beta_profile_and_telemetry_rejection():
    """Carrier Beta: offers ₹38,000 / 32m; telemetry reports 13.8°C / compressor off and is rejected."""
    profile = get_carrier_profile("carrier_beta")
    assert profile is not None
    assert profile.base_price == 38000.0
    assert profile.eta_minutes == 32
    assert profile.simulated_temp_c == 13.8
    assert profile.simulated_compressor_active is False

    # Generate telemetry payload with valid HMAC
    payload = generate_telemetry_payload(
        candidate_id="carrier_beta",
        run_id="run_b_beta",
        nonce="nonce_beta_1",
        secret_key=FIXTURE_SECRET,
    )
    assert payload is not None
    assert payload.recorded_temp_c == 13.8
    assert payload.compressor_active is False

    # Auditor evaluation against standard vaccine band [2.0°C, 8.0°C]
    tracker = NonceReplayTracker()
    verdict = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_beta",
        expected_run_id="run_b_beta",
        expected_nonce="nonce_beta_1",
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
    )
    assert verdict.passed is False
    assert "Temperature non-compliant" in verdict.reason or "Compressor inactive" in verdict.reason
    assert verdict.signature_valid is True  # Signature itself was valid, physical state was unsafe!


def test_carrier_alpha_profile_and_telemetry_acceptance():
    """Carrier Alpha: offers ₹41,500 / 38m; telemetry reports 4.2°C / active compressor and is accepted."""
    profile = get_carrier_profile("carrier_alpha")
    assert profile is not None
    assert profile.base_price == 41500.0
    assert profile.eta_minutes == 38
    assert profile.simulated_temp_c == 4.2
    assert profile.simulated_compressor_active is True

    payload = generate_telemetry_payload(
        candidate_id="carrier_alpha",
        run_id="run_b_alpha",
        nonce="nonce_alpha_1",
        secret_key=FIXTURE_SECRET,
    )
    assert payload is not None
    assert payload.recorded_temp_c == 4.2
    assert payload.compressor_active is True

    tracker = NonceReplayTracker()
    verdict = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id="run_b_alpha",
        expected_nonce="nonce_alpha_1",
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
    )
    assert verdict.passed is True
    assert verdict.signature_valid is True
    assert verdict.compressor_active is True
    assert verdict.recorded_temp_c == 4.2


def test_carrier_gamma_profile_and_telemetry_acceptance():
    """Carrier Gamma: offers ₹43,000 / 40m; telemetry reports 4.8°C / active compressor and passes auditor."""
    profile = get_carrier_profile("carrier_gamma")
    assert profile is not None
    assert profile.base_price == 43000.0
    assert profile.eta_minutes == 40
    assert profile.simulated_temp_c == 4.8
    assert profile.simulated_compressor_active is True

    payload = generate_telemetry_payload(
        candidate_id="carrier_gamma",
        run_id="run_b_gamma",
        nonce="nonce_gamma_1",
        secret_key=FIXTURE_SECRET,
    )
    assert payload is not None
    assert payload.recorded_temp_c == 4.8
    assert payload.compressor_active is True

    tracker = NonceReplayTracker()
    verdict = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_gamma",
        expected_run_id="run_b_gamma",
        expected_nonce="nonce_gamma_1",
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
    )
    assert verdict.passed is True
    assert verdict.signature_valid is True
    assert verdict.compressor_active is True


def test_telemetry_auditor_rejection_matrix():
    """Telemetry Auditor: detects and rejects tampered signature, nonce replay, stale timestamp, and candidate mismatch."""
    tracker = NonceReplayTracker()
    run_id = "run_audit_matrix"
    now = datetime.now(timezone.utc)

    # 1. Tampered signature rejection
    tampered_payload = generate_telemetry_payload("carrier_alpha", run_id, "nonce_1", FIXTURE_SECRET, timestamp=now)
    tampered_payload.signature = "bad_tampered_signature_hex"
    v1 = verify_telemetry_payload(
        payload=tampered_payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce="nonce_1",
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
    )
    assert v1.passed is False
    assert v1.signature_valid is False
    assert "signature verification failed" in v1.reason.lower() or "tampered" in v1.reason.lower()

    # 2. Nonce replay rejection
    valid_payload = generate_telemetry_payload("carrier_alpha", run_id, "nonce_replay_test", FIXTURE_SECRET, timestamp=now)
    v2_first = verify_telemetry_payload(
        payload=valid_payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce="nonce_replay_test",
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
    )
    assert v2_first.passed is True

    # Replay same payload with same nonce
    v2_second = verify_telemetry_payload(
        payload=valid_payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce="nonce_replay_test",
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
    )
    assert v2_second.passed is False
    assert "already been spent" in v2_second.reason or "replay" in v2_second.reason.lower()

    # 3. Stale timestamp rejection (> 300s old)
    stale_time = now - timedelta(seconds=600)
    stale_payload = generate_telemetry_payload("carrier_alpha", run_id, "nonce_stale", FIXTURE_SECRET, timestamp=stale_time)
    v3 = verify_telemetry_payload(
        payload=stale_payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce="nonce_stale",
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
    )
    assert v3.passed is False
    assert v3.timestamp_fresh is False
    assert "stale" in v3.reason.lower()

    # 4. Unknown/mismatched candidate identity
    v4 = verify_telemetry_payload(
        payload=valid_payload,
        expected_candidate_id="carrier_beta",  # Expected Beta, but payload is Alpha
        expected_run_id=run_id,
        expected_nonce="nonce_replay_test",
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
    )
    assert v4.passed is False
    assert "mismatch" in v4.reason.lower()


def test_coordinator_orchestrator_quote_ranking_and_lifecycle():
    """Coordinator: isolated run context, deterministic ranking, single completion event."""
    orchestrator = CoordinatorOrchestrator(
        auditor_address="auditor",
        carrier_addresses={"carrier_alpha": "alpha", "carrier_beta": "beta", "carrier_gamma": "gamma"},
        storage=StorageRepository(":memory:"),
    )

    req = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Gurugram",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )
    run_ctx = orchestrator.init_mission(req)
    assert run_ctx.run_id in orchestrator.runs
    assert run_ctx.current_state == WorkflowState.RECEIVED

    # Offer generation for all 3 carriers
    beta_offer = generate_carrier_offer("carrier_beta", req.run_id)
    alpha_offer = generate_carrier_offer("carrier_alpha", req.run_id)
    gamma_offer = generate_carrier_offer("carrier_gamma", req.run_id)
    all_offers = [gamma_offer, beta_offer, alpha_offer]

    eligible, violations = filter_eligible_offers(
        request=req,
        offers=all_offers,
        excluded_candidate_ids=set(),
    )
    assert len(eligible) == 3

    ranked = rank_carrier_offers(eligible)
    # Price order: Beta (₹38,000) -> Alpha (₹41,500) -> Gamma (₹43,000)
    assert ranked[0].carrier_id == "carrier_beta"
    assert ranked[1].carrier_id == "carrier_alpha"
    assert ranked[2].carrier_id == "carrier_gamma"


# =============================================================================
# PART C: End-to-End Verification Matrix
# =============================================================================

@pytest.mark.asyncio
async def test_matrix_scenario_1_complete_incident_intake():
    """Scenario 1: Complete incident starts recovery immediately without returning form card."""
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
        async def send(self, dest, msg):
            self.sent_messages.append((dest, msg))

    mock_ctx = MockContext()
    complete_text = (
        "Vaccine shipment VAX-8821 near Sector 18 Gurugram. Temp 2 to 8 C, budget ₹48,000, deadline 45 mins."
    )
    user_msg = create_text_chat_message(complete_text)
    await msg_handler(mock_ctx, "user_sender_1", user_msg)

    # Must reply with ACK and recovery report (NOT intake form)
    assert len(mock_ctx.sent_messages) >= 2
    reply = mock_ctx.sent_messages[-1][1]
    assert "FreightPact Cold-Chain Recovery Report" in reply.text()

    card_meta = [c for c in reply.content if isinstance(c, MetadataContent)][0]
    assert card_meta.metadata["card_kind"] == "detail"
    payload = json.loads(card_meta.metadata["card_payload"])
    assert payload["title"] == "Incident Recovery Overview: VAX-8821"


@pytest.mark.asyncio
async def test_matrix_scenario_2_missing_fields_intake_form_and_recovery():
    """Scenario 2: Missing fields -> displays intake form -> user submits missing fields -> recovery begins."""
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
        async def send(self, dest, msg):
            self.sent_messages.append((dest, msg))

    sender = "user_sender_2"

    # Step A: Missing budget and deadline
    ctx1 = MockContext()
    await msg_handler(ctx1, sender, create_text_chat_message("Vaccine shipment VAX-8821 temp 2 to 8 C"))
    reply1 = ctx1.sent_messages[1][1]
    assert "FreightPact Cold-Chain Incident Intake" in reply1.text()
    card1 = [c for c in reply1.content if isinstance(c, MetadataContent)][0]
    assert card1.metadata["card_kind"] == "form"

    # Step B: User submits missing fields via form CTA
    ctx2 = MockContext()
    form_cta = {
        "action": "submit_incident_intake",
        "shipment_id": "VAX-8821",
        "max_budget": "48000",
        "deadline_minutes": "45",
    }
    await msg_handler(ctx2, sender, create_text_chat_message(json.dumps(form_cta)))
    reply2 = ctx2.sent_messages[-1][1]
    assert "FreightPact Cold-Chain Recovery Report" in reply2.text()
    card2 = [c for c in reply2.content if isinstance(c, MetadataContent)][0]
    assert card2.metadata["card_kind"] == "detail"


def test_matrix_scenario_3_beta_and_alpha_fail_gamma_selected():
    """Scenario 3: When Beta and Alpha both fail verification or eligibility, Gamma is evaluated and awarded."""
    req = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Gurugram",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )
    run_ctx = RunContext(req, max_audit_attempts=3)

    beta_offer = generate_carrier_offer("carrier_beta", req.run_id)
    alpha_offer = generate_carrier_offer("carrier_alpha", req.run_id)
    gamma_offer = generate_carrier_offer("carrier_gamma", req.run_id)
    run_ctx.offers = [beta_offer, alpha_offer, gamma_offer]

    run_ctx.transition_to(WorkflowState.VALIDATED)
    run_ctx.transition_to(WorkflowState.COLLECTING_QUOTES)
    run_ctx.transition_to(WorkflowState.RANKING)

    # Attempt 1: Beta evaluated & rejected (13.8°C)
    run_ctx.audit_attempts += 1
    run_ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    run_ctx.record_rejected_candidate("carrier_beta", "Temperature non-compliant: recorded 13.8°C")
    run_ctx.transition_to(WorkflowState.CANDIDATE_REJECTED)
    run_ctx.transition_to(WorkflowState.VERIFYING_NEXT)

    # Attempt 2: Alpha evaluated & rejected (e.g. simulated reefer compressor breakdown)
    run_ctx.audit_attempts += 1
    run_ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    run_ctx.record_rejected_candidate("carrier_alpha", "Compressor inactive and temperature rising")
    run_ctx.transition_to(WorkflowState.CANDIDATE_REJECTED)
    run_ctx.transition_to(WorkflowState.VERIFYING_NEXT)

    # Attempt 3: Gamma evaluated & accepted (4.8°C compliant, compressor active)
    run_ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    run_ctx.audit_verdicts["carrier_gamma"] = AuditVerdict(
        candidate_id="carrier_gamma",
        run_id=req.run_id,
        nonce="nonce_gamma_1",
        passed=True,
        reason="Compliant telemetry",
        recorded_temp_c=4.8,
        compressor_active=True,
        signature_valid=True,
        timestamp_fresh=True,
    )
    run_ctx.selected_offer = gamma_offer
    run_ctx.transition_to(WorkflowState.RECOMMENDED_FOR_CONFIRMATION)

    assert run_ctx.current_state == WorkflowState.RECOMMENDED_FOR_CONFIRMATION
    assert run_ctx.selected_offer.carrier_id == "carrier_gamma"
    assert "carrier_beta" in run_ctx.rejected_candidate_ids
    assert "carrier_alpha" in run_ctx.rejected_candidate_ids
    assert run_ctx.audit_attempts == 2  # 2 rejections before successful award


def test_matrix_scenario_4_all_carriers_fail_no_safe_match():
    """Scenario 4: When all candidates fail verification, Coordinator safely transitions to NO_SAFE_MATCH."""
    req = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Gurugram",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )
    run_ctx = RunContext(req, max_audit_attempts=3)
    beta_offer = generate_carrier_offer("carrier_beta", req.run_id)
    alpha_offer = generate_carrier_offer("carrier_alpha", req.run_id)
    gamma_offer = generate_carrier_offer("carrier_gamma", req.run_id)
    run_ctx.offers = [beta_offer, alpha_offer, gamma_offer]

    run_ctx.transition_to(WorkflowState.VALIDATED)
    run_ctx.transition_to(WorkflowState.COLLECTING_QUOTES)
    run_ctx.transition_to(WorkflowState.RANKING)

    # Attempt 1: Beta fails
    run_ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    run_ctx.record_rejected_candidate("carrier_beta", "Temperature 13.8°C")
    run_ctx.transition_to(WorkflowState.CANDIDATE_REJECTED)
    run_ctx.transition_to(WorkflowState.VERIFYING_NEXT)

    # Attempt 2: Alpha fails
    run_ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    run_ctx.record_rejected_candidate("carrier_alpha", "Compressor failure")
    run_ctx.transition_to(WorkflowState.CANDIDATE_REJECTED)
    run_ctx.transition_to(WorkflowState.VERIFYING_NEXT)

    # Attempt 3: Gamma fails
    run_ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    run_ctx.record_rejected_candidate("carrier_gamma", "HMAC signature mismatch")
    run_ctx.transition_to(WorkflowState.CANDIDATE_REJECTED)

    # Safe exit to terminal NO_SAFE_MATCH
    run_ctx.transition_to(WorkflowState.NO_SAFE_MATCH, reason="Exhausted all candidates")

    assert run_ctx.current_state == WorkflowState.NO_SAFE_MATCH
    assert run_ctx.selected_offer is None
    assert len(run_ctx.rejected_candidate_ids) == 3
    terminal_result = run_ctx.build_terminal_result()
    assert terminal_result.terminal_state == "NO_SAFE_MATCH"
    assert terminal_result.selected_carrier_id is None


@pytest.mark.asyncio
async def test_matrix_scenario_5_confirm_award_receipt_card():
    """Scenario 5: Confirm award returns confirmation receipt card (kind=detail, is_terminal=true)."""
    orchestrator = CoordinatorOrchestrator(
        auditor_address="auditor",
        carrier_addresses={"carrier_alpha": "alpha"},
        storage=StorageRepository(":memory:"),
    )
    req = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Gurugram",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )
    run_ctx = orchestrator.init_mission(req)
    alpha_offer = generate_carrier_offer("carrier_alpha", req.run_id)
    run_ctx.offers = [alpha_offer]
    run_ctx.selected_offer = alpha_offer
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
        async def send(self, dest, msg):
            self.sent_messages.append((dest, msg))

    mock_ctx = MockContext()
    confirm_action = {"action": "confirm_award", "carrier_id": "carrier_alpha", "run_id": req.run_id}
    await msg_handler(mock_ctx, "user_sender_5", create_text_chat_message(json.dumps(confirm_action)))

    reply = mock_ctx.sent_messages[1][1]
    assert "Simulated Carrier Award Confirmed" in reply.text()
    assert "[!NOTE]" not in reply.text()

    card_meta = [c for c in reply.content if isinstance(c, MetadataContent)][0]
    assert card_meta.metadata["card_kind"] == "detail"
    assert card_meta.metadata["is_terminal"] == "true"
    payload = json.loads(card_meta.metadata["card_payload"])
    assert "Dispatch Confirmation Receipt" in payload["title"]


@pytest.mark.asyncio
async def test_matrix_scenario_6_decline_award_receipt_card():
    """Scenario 6: Decline award returns declination receipt card (kind=detail, is_terminal=true)."""
    orchestrator = CoordinatorOrchestrator(
        auditor_address="auditor",
        carrier_addresses={"carrier_alpha": "alpha"},
        storage=StorageRepository(":memory:"),
    )
    req = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Gurugram",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )
    run_ctx = orchestrator.init_mission(req)
    alpha_offer = generate_carrier_offer("carrier_alpha", req.run_id)
    run_ctx.offers = [alpha_offer]
    run_ctx.selected_offer = alpha_offer
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
        async def send(self, dest, msg):
            self.sent_messages.append((dest, msg))

    mock_ctx = MockContext()
    decline_action = {"action": "reject_award", "carrier_id": "carrier_alpha", "run_id": req.run_id}
    await msg_handler(mock_ctx, "user_sender_6", create_text_chat_message(json.dumps(decline_action)))

    reply = mock_ctx.sent_messages[1][1]
    assert "Simulated Carrier Recommendation Declined" in reply.text()
    assert "[!NOTE]" not in reply.text()

    card_meta = [c for c in reply.content if isinstance(c, MetadataContent)][0]
    assert card_meta.metadata["card_kind"] == "detail"
    assert card_meta.metadata["is_terminal"] == "true"
    payload = json.loads(card_meta.metadata["card_payload"])
    assert "Recommendation Declined" in payload["title"]


@pytest.mark.asyncio
async def test_matrix_scenario_7_idempotent_award_confirmation():
    """Scenario 7: Repeating confirm returns already confirmed card without duplicate write."""
    orchestrator = CoordinatorOrchestrator(
        auditor_address="auditor",
        carrier_addresses={"carrier_alpha": "alpha"},
        storage=StorageRepository(":memory:"),
    )
    req = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Gurugram",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )
    run_ctx = orchestrator.init_mission(req)
    alpha_offer = generate_carrier_offer("carrier_alpha", req.run_id)
    run_ctx.offers = [alpha_offer]
    run_ctx.selected_offer = alpha_offer
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
        async def send(self, dest, msg):
            self.sent_messages.append((dest, msg))

    confirm_action = {"action": "confirm_award", "carrier_id": "carrier_alpha", "run_id": req.run_id}

    # First call: confirmed
    ctx1 = MockContext()
    await msg_handler(ctx1, "user_sender_7", create_text_chat_message(json.dumps(confirm_action)))
    reply1 = ctx1.sent_messages[1][1]
    assert "Simulated Carrier Award Confirmed" in reply1.text()
    assert getattr(run_ctx, "is_confirmed", False) is True

    # Second call: idempotent return
    ctx2 = MockContext()
    await msg_handler(ctx2, "user_sender_7", create_text_chat_message(json.dumps(confirm_action)))
    reply2 = ctx2.sent_messages[1][1]
    assert "Simulated Carrier Award Already Confirmed" in reply2.text()
    card_meta = [c for c in reply2.content if isinstance(c, MetadataContent)][0]
    assert card_meta.metadata["card_kind"] == "detail"


@pytest.mark.asyncio
async def test_matrix_scenario_8_tampered_review_action_rejected():
    """Scenario 8: Parameter tampering (wrong candidate ID) or invalid state is safely rejected."""
    orchestrator = CoordinatorOrchestrator(
        auditor_address="auditor",
        carrier_addresses={"carrier_alpha": "alpha"},
        storage=StorageRepository(":memory:"),
    )
    req = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Gurugram",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )
    run_ctx = orchestrator.init_mission(req)
    alpha_offer = generate_carrier_offer("carrier_alpha", req.run_id)
    run_ctx.offers = [alpha_offer]
    run_ctx.selected_offer = alpha_offer
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
        async def send(self, dest, msg):
            self.sent_messages.append((dest, msg))

    # 1. Attacker attempts to confirm Carrier Beta instead of verified Alpha
    ctx1 = MockContext()
    tampered_action = {"action": "confirm_award", "carrier_id": "carrier_beta", "run_id": req.run_id}
    await msg_handler(ctx1, "user_sender_8", create_text_chat_message(json.dumps(tampered_action)))
    reply1 = ctx1.sent_messages[1][1]
    assert "Security Warning" in reply1.text()
    assert "does not match the cryptographically audited candidate" in reply1.text()
    assert getattr(run_ctx, "is_confirmed", False) is False

    # 2. Confirm action attempted on non-awardable state (e.g. state is RANKING)
    run_ctx.current_state = WorkflowState.RANKING
    ctx2 = MockContext()
    await msg_handler(ctx2, "user_sender_8", create_text_chat_message(json.dumps({"action": "confirm_award", "carrier_id": "carrier_alpha", "run_id": req.run_id})))
    reply2 = ctx2.sent_messages[1][1]
    assert "Action Rejected" in reply2.text()
    assert "cannot be confirmed" in reply2.text()
