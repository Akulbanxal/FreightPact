"""Tests for ASI:One Interactive Cards specification, validation, schemas, and action dispatch."""

import json
import pytest
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent

from freightpact.cards import (
    CardValidationError,
    MAX_NESTING_DEPTH,
    MAX_PAYLOAD_BYTES,
    build_audit_failure_detail_card,
    build_card_metadata_envelope,
    build_dispatch_confirmation_card,
    build_dispatch_declination_card,
    build_final_recommendation_review_card,
    build_incident_intake_form_card,
    build_quote_comparison_carousel_card,
    parse_card_user_response,
)
from freightpact.chat_interface import create_interactive_chat_message
from freightpact.models import CargoClass, CarrierOffer, RecoveryRequest


@pytest.fixture
def sample_request() -> RecoveryRequest:
    return RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Gurugram",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
        required_capacity_kg=1000.0,
    )


@pytest.fixture
def sample_offers(sample_request: RecoveryRequest) -> list[CarrierOffer]:
    return [
        CarrierOffer(
            offer_id="off_beta",
            run_id=sample_request.run_id,
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
            run_id=sample_request.run_id,
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


# -------------------------------------------------------------------------
# 1. Envelope & Limits Tests
# -------------------------------------------------------------------------

def test_envelope_conforms_to_official_acp_spec():
    payload = {"title": "Test Card", "body": "Hello world"}
    card = build_card_metadata_envelope(
        "detail",
        payload,
        requires_interaction=True,
        is_terminal=False,
        drawer_width_px=450,
    )
    assert isinstance(card, MetadataContent)
    assert isinstance(card.metadata, dict)
    # Check string types on all metadata fields
    for k, v in card.metadata.items():
        assert isinstance(k, str)
        assert isinstance(v, str)

    assert card.metadata["card_protocol_version"] == "1"
    assert card.metadata["requires_card_interaction"] == "true"
    assert card.metadata["card_kind"] == "detail"
    assert card.metadata["preferred_drawer_width_px"] == "450"

    # Verify JSON deserialization of card_payload
    decoded = json.loads(card.metadata["card_payload"])
    assert decoded["title"] == "Test Card"


def test_invalid_card_kind_rejected():
    with pytest.raises(CardValidationError, match="Invalid card_kind 'unsupported_kind'"):
        build_card_metadata_envelope("unsupported_kind", {"title": "Test"})


def test_payload_exceeding_max_bytes_rejected():
    # 64 KB limit = 65,536 bytes
    large_string = "X" * (MAX_PAYLOAD_BYTES + 100)
    with pytest.raises(CardValidationError, match="exceeds maximum limit"):
        build_card_metadata_envelope("detail", {"huge_field": large_string})


def test_payload_exceeding_max_depth_rejected():
    # Nesting depth > 8
    nested: dict = {"depth": 1}
    curr = nested
    for i in range(2, 11):
        curr["child"] = {"depth": i}
        curr = curr["child"]

    with pytest.raises(CardValidationError, match="nesting depth .* exceeds max limit"):
        build_card_metadata_envelope("detail", nested)


# -------------------------------------------------------------------------
# 2. Predefined Schema Builders
# -------------------------------------------------------------------------

def test_incident_intake_form_card_shape():
    partial = {"shipment_id": "VAX-8821", "max_budget": 48000.0}
    card = build_incident_intake_form_card(partial)
    assert card.metadata["card_kind"] == "form"
    assert card.metadata["requires_card_interaction"] == "true"

    payload = json.loads(card.metadata["card_payload"])
    assert payload["title"] == "Cold-Chain Incident Intake"
    assert len(payload["fields"]) >= 4
    field_names = [f["name"] for f in payload["fields"]]
    assert "shipment_id" in field_names
    assert "temp_range" in field_names
    assert "max_budget" in field_names
    assert "deadline_minutes" in field_names
    assert payload["submit_cta"]["selection"]["action"] == "submit_incident_intake"


def test_quote_comparison_carousel_card_shape(sample_request, sample_offers):
    card = build_quote_comparison_carousel_card(sample_request, sample_offers)
    assert card.metadata["card_kind"] == "carousel"
    payload = json.loads(card.metadata["card_payload"])
    assert "Candidate Carrier Comparison" in payload["title"]
    assert len(payload["items"]) == 2
    assert payload["items"][0]["id"] == "carrier_beta"
    assert payload["items"][1]["id"] == "carrier_alpha"
    assert "badges" in payload["items"][0]
    assert payload["items"][0]["primary_cta"]["label"] == "Inspect Evidence"
    assert payload["items"][0]["primary_cta"]["selection"]["action"] == "inspect_telemetry"
    assert payload["items"][0]["primary_cta"]["selection"]["carrier_id"] == "carrier_beta"


def test_incident_overview_card_shape(sample_request):
    from freightpact.cards import build_incident_overview_card
    from freightpact.models import RecoveryResult
    from freightpact.states import RunContext

    run_ctx = RunContext(sample_request)
    terminal_result = RecoveryResult(
        run_id=sample_request.run_id,
        shipment_id=sample_request.shipment_id,
        terminal_state="RECOMMENDED_FOR_CONFIRMATION",
        selected_carrier_id="carrier_alpha",
    )
    card = build_incident_overview_card(sample_request, run_ctx, terminal_result)
    assert card.metadata["card_kind"] == "detail"
    payload = json.loads(card.metadata["card_payload"])
    assert f"Incident Recovery Overview: {sample_request.shipment_id}" in payload["title"]
    labels = [r["label"] for r in payload["summary_rows"]]
    assert "Shipment ID" in labels
    assert "Safe Temperature Band" in labels
    assert "Budget Ceiling" in labels
    assert "Current Status" in labels
    assert payload["ctas"][0]["selection"]["action"] == "view_carrier_comparison"


def test_telemetry_evidence_card_shape(sample_request, sample_offers):
    from freightpact.cards import build_telemetry_evidence_card
    from freightpact.models import AuditVerdict
    from freightpact.states import RunContext

    run_ctx = RunContext(sample_request)
    run_ctx.offers = sample_offers
    run_ctx.audit_verdicts["carrier_alpha"] = AuditVerdict(
        candidate_id="carrier_alpha",
        run_id=sample_request.run_id,
        nonce="fresh_nonce_123",
        passed=True,
        reason="Cryptographically compliant telemetry",
        recorded_temp_c=4.2,
        compressor_active=True,
        signature_valid=True,
        timestamp_fresh=True,
    )
    run_ctx.rejection_reasons["carrier_beta"] = "Temperature non-compliant: recorded 13.8°C"

    # View C for Alpha (Verified)
    card_alpha = build_telemetry_evidence_card(sample_request, "carrier_alpha", run_ctx)
    assert card_alpha.metadata["card_kind"] == "detail"
    payload_alpha = json.loads(card_alpha.metadata["card_payload"])
    assert "Alpha Cold Pharma Transport" in payload_alpha["title"]
    labels_alpha = [r["label"] for r in payload_alpha["summary_rows"]]
    assert "Required Safe Range" in labels_alpha
    assert "Recorded Reefer Temp" in labels_alpha
    assert "HMAC-SHA256 Signature" in labels_alpha
    assert payload_alpha["ctas"][0]["selection"]["action"] == "view_carrier_comparison"

    # View C for Beta (Rejected)
    card_beta = build_telemetry_evidence_card(sample_request, "carrier_beta", run_ctx)
    payload_beta = json.loads(card_beta.metadata["card_payload"])
    vals_beta = {r["label"]: r["value"] for r in payload_beta["summary_rows"]}
    assert "13.8°C" in vals_beta["Recorded Reefer Temp"]
    assert "Excluded" in vals_beta["Audit Decision"]


def test_audit_failure_detail_card_shape(sample_request):
    card = build_audit_failure_detail_card(
        request=sample_request,
        candidate_id="carrier_beta",
        candidate_name="Beta Rapid Logistics",
        rejection_reason="Recorded 13.8°C exceeds range [2.0°C..8.0°C] and compressor is inactive",
        recorded_temp_c=13.8,
        compressor_active=False,
        next_candidate_id="carrier_alpha",
    )
    assert card.metadata["card_kind"] == "detail"
    payload = json.loads(card.metadata["card_payload"])
    assert "Beta Rapid Logistics" in payload["title"]
    labels = [r["label"] for r in payload["summary_rows"]]
    assert "Audit Status" in labels
    assert "Recorded Temp" in labels
    assert "Compressor State" in labels
    assert payload["ctas"][0]["selection"]["action"] == "acknowledge_failover"
    assert payload["ctas"][0]["selection"]["rejected_candidate"] == "carrier_beta"


def test_final_recommendation_review_card_shape(sample_request, sample_offers):
    selected_offer = sample_offers[1]  # Alpha
    card = build_final_recommendation_review_card(
        request=sample_request,
        selected_offer=selected_offer,
        audit_verdict_reason="HMAC valid, sensor 4.2°C compliant, compressor active",
        recorded_temp_c=4.2,
    )
    assert card.metadata["card_kind"] == "review"
    payload = json.loads(card.metadata["card_payload"])
    assert "Review Simulated Carrier Award" in payload["title"]
    assert payload["approve_cta"]["label"] == "Confirm Simulated Award"
    assert payload["approve_cta"]["selection"]["action"] == "confirm_award"
    assert payload["approve_cta"]["selection"]["carrier_id"] == "carrier_alpha"
    assert payload["reject_cta"]["label"] == "Decline Recommendation"
    assert payload["reject_cta"]["selection"]["action"] == "reject_award"


# -------------------------------------------------------------------------
# 3. User Response Parsing & Action Validation
# -------------------------------------------------------------------------

def test_parse_card_user_response_direct_json():
    raw_json = json.dumps({
        "action": "confirm_award",
        "carrier_id": "carrier_alpha",
        "run_id": "run_test_123",
    })
    action, data = parse_card_user_response(raw_json)
    assert action == "confirm_award"
    assert data["carrier_id"] == "carrier_alpha"
    assert data["run_id"] == "run_test_123"


def test_parse_card_user_response_navigation_actions():
    # Direct JSON navigation
    nav_json = json.dumps({"action": "inspect_telemetry", "carrier_id": "carrier_beta", "run_id": "run_999"})
    action, data = parse_card_user_response(nav_json)
    assert action == "inspect_telemetry"
    assert data["carrier_id"] == "carrier_beta"

    # Prose navigation
    action_inspect, d_inspect = parse_card_user_response("Please inspect telemetry for Beta")
    assert action_inspect == "inspect_telemetry"
    assert d_inspect["carrier_id"] == "carrier_beta"

    action_quotes, _ = parse_card_user_response("Show me all candidate carrier quotes")
    assert action_quotes == "view_carrier_comparison"

    action_overview, _ = parse_card_user_response("Can I see the incident overview?")
    assert action_overview == "view_incident_overview"


def test_parse_card_user_response_malformed_json_fallback():
    malformed = '{"action": "confirm_award", "incomplete": '
    action, data = parse_card_user_response(malformed)
    # Malformed JSON should not crash; it falls back to prose inspection or None
    assert action == "confirm_award"  # caught by keyword "confirm"
    assert data["action"] == "confirm_award"


def test_parse_card_user_response_planner_prose():
    prose_approve = "The user has confirmed the recommendation to dispatch carrier alpha."
    action, _ = parse_card_user_response(prose_approve)
    assert action == "confirm_award"

    prose_reject = "Operator chose to decline and cancel the recommendation."
    action, _ = parse_card_user_response(prose_reject)
    assert action == "reject_award"

    prose_failover = "Please proceed to next candidate."
    action, _ = parse_card_user_response(prose_failover)
    assert action == "acknowledge_failover"

    regular_chat = "Where is my shipment right now?"
    action, _ = parse_card_user_response(regular_chat)
    assert action is None


# -------------------------------------------------------------------------
# 4. Fallback Behavior & Chat Integration
# -------------------------------------------------------------------------

def test_interactive_chat_message_fallback_without_card():
    msg = create_interactive_chat_message("Standard markdown response text", metadata_card=None)
    assert isinstance(msg, ChatMessage)
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], TextContent)
    assert msg.text() == "Standard markdown response text"


def test_interactive_chat_message_with_card():
    form_card = build_incident_intake_form_card({"shipment_id": "VAX-8821"})
    msg = create_interactive_chat_message("Please provide incident constraints:", metadata_card=form_card)
    assert isinstance(msg, ChatMessage)
    assert len(msg.content) == 2
    assert isinstance(msg.content[0], TextContent)
    assert isinstance(msg.content[1], MetadataContent)
    assert msg.content[1].metadata["card_kind"] == "form"

def test_observed_incident_message_not_misclassified_as_card_action():
    """Regression test: User query containing 'carriers' and 'recommend' must NOT be hijacked as a card action."""
    observed_text = (
        "Hi, I need an urgent replacement for vaccine shipment VAX-8821 near Sector 18, Gurugram. "
        "The required temperature range is 2–8°C, the maximum budget is ₹48,000, and the deadline is 45 minutes. "
        "Please evaluate available carriers and recommend a safe replacement."
    )
    action, data = parse_card_user_response(observed_text)
    assert action is None
    assert data["raw_text"] == observed_text

    # With leading agent mention
    mentioned_text = "@agent1q2jv8khzjg4ycngm5zk5e8mkad774ptja3ufjfwjujwu9x5scexu6pu82ja " + observed_text
    action_m, _ = parse_card_user_response(mentioned_text)
    assert action_m is None


def test_dispatch_confirmation_card_shape(sample_request, sample_offers):
    from freightpact.states import RunContext, WorkflowState
    run_ctx = RunContext(sample_request)
    run_ctx.offers = sample_offers
    run_ctx.selected_offer = sample_offers[0]  # carrier_beta
    run_ctx.transition_to(WorkflowState.VALIDATED)
    run_ctx.transition_to(WorkflowState.COLLECTING_QUOTES)
    run_ctx.transition_to(WorkflowState.RANKING)
    run_ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    run_ctx.transition_to(WorkflowState.RECOMMENDED_FOR_CONFIRMATION)

    card = build_dispatch_confirmation_card(run_ctx)
    assert isinstance(card, MetadataContent)
    assert card.metadata["card_kind"] == "detail"
    assert card.metadata["is_terminal"] == "true"
    assert card.metadata["requires_card_interaction"] == "true"

    payload = json.loads(card.metadata["card_payload"])
    assert "is_terminal" not in payload
    assert "title" in payload
    assert "Dispatched" in payload["title"] or "Receipt" in payload["title"]
    assert "summary_rows" in payload
    labels = {r["label"]: r["value"] for r in payload["summary_rows"]}
    assert "Shipment ID" in labels
    assert "VAX-8821" in labels["Shipment ID"]
    assert "Awarded Carrier" in labels
    assert "Simulation Disclosure" in labels
    assert "SIMULATION" in labels["Simulation Disclosure"]


def test_dispatch_declination_card_shape(sample_request, sample_offers):
    from freightpact.states import RunContext
    run_ctx = RunContext(sample_request)
    run_ctx.offers = sample_offers
    run_ctx.selected_offer = sample_offers[1]  # carrier_alpha

    card = build_dispatch_declination_card(run_ctx, carrier_id="carrier_alpha")
    assert isinstance(card, MetadataContent)
    assert card.metadata["card_kind"] == "detail"
    assert card.metadata["is_terminal"] == "true"
    assert card.metadata["requires_card_interaction"] == "true"

    payload = json.loads(card.metadata["card_payload"])
    assert "is_terminal" not in payload
    assert "Declined" in payload["title"]
    assert "summary_rows" in payload
    labels = {r["label"]: r["value"] for r in payload["summary_rows"]}
    assert "Shipment ID" in labels
    assert "VAX-8821" in labels["Shipment ID"]
    assert "Declined Carrier" in labels
    assert "carrier_alpha" in labels["Declined Carrier"]
    assert "Status" in labels
    assert "Simulation Disclosure" in labels

