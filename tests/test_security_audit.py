"""Security, boundary, and claim audit tests for FreightPact.

Verifies:
1. Replay attack rejection: A spent nonce cannot be reused within the same run.
2. Tamper resistance: Mutating any field (temperature, compressor state, nonce, timestamp) invalidates HMAC signature.
3. Timestamp freshness enforcement: Stale payloads outside the validity window are rejected.
4. Input sanitization in audit logs: Secrets, seeds, and tokens are scrubbed from log outputs.
5. Deterministic fallback: Unknown carrier IDs return None and never default to Carrier Alpha.
6. Honest simulation claims: Mission reports and review cards explicitly disclose simulation status and do not claim live dispatch or hardware attestation.
"""

from datetime import datetime, timedelta, timezone
import json
import pytest
from uagents_core.contrib.protocols.chat import ChatMessage

from freightpact.audit_log import sanitize_log_message
from freightpact.cards import build_final_recommendation_review_card
from freightpact.chat_interface import format_recovery_response
from freightpact.fixtures import (
    generate_carrier_offer,
    generate_telemetry_payload,
    get_carrier_profile,
)
from freightpact.models import CargoClass, CarrierOffer, RecoveryRequest
from freightpact.states import RunContext, WorkflowState
from freightpact.telemetry import (
    DEFAULT_FRESHNESS_WINDOW_SECONDS,
    NonceReplayTracker,
    compute_telemetry_signature,
    verify_telemetry_payload,
)

FIXTURE_SECRET = "freightpact-audit-test-secret-12345"


# -------------------------------------------------------------------------
# 1. Cryptographic Signature & Tamper Resistance
# -------------------------------------------------------------------------

def test_tampering_temperature_invalidates_signature():
    run_id = "run_audit_01"
    nonce = "nonce_audit_01"
    now = datetime.now(timezone.utc)

    # Valid payload for Alpha
    payload = generate_telemetry_payload(
        candidate_id="carrier_alpha",
        run_id=run_id,
        nonce=nonce,
        secret_key=FIXTURE_SECRET,
        timestamp=now,
    )
    assert payload is not None

    # Tamper with temperature directly on payload object
    payload.recorded_temp_c = 1.0  # modified from 4.2°C

    verdict = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
    )
    assert verdict.passed is False
    assert "Cryptographic signature verification failed" in verdict.reason
    assert verdict.signature_valid is False


def test_tampering_compressor_state_invalidates_signature():
    run_id = "run_audit_02"
    nonce = "nonce_audit_02"
    now = datetime.now(timezone.utc)

    payload = generate_telemetry_payload(
        candidate_id="carrier_alpha",
        run_id=run_id,
        nonce=nonce,
        secret_key=FIXTURE_SECRET,
        timestamp=now,
    )
    assert payload is not None

    # Tamper with compressor status
    payload.compressor_active = False

    verdict = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
    )
    assert verdict.passed is False
    assert "Cryptographic signature verification failed" in verdict.reason


# -------------------------------------------------------------------------
# 2. Replay Protection & Freshness Window
# -------------------------------------------------------------------------

def test_nonce_replay_attack_strictly_rejected():
    tracker = NonceReplayTracker()
    run_id = "run_audit_03"
    nonce = "nonce_single_use_999"
    now = datetime.now(timezone.utc)

    payload = generate_telemetry_payload(
        candidate_id="carrier_alpha",
        run_id=run_id,
        nonce=nonce,
        secret_key=FIXTURE_SECRET,
        timestamp=now,
    )

    # First verification pass: should pass and mark nonce spent
    verdict1 = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
        current_time=now,
    )
    assert verdict1.passed is True
    assert tracker.is_nonce_spent(run_id, nonce) is True

    # Replay attack: verify identical payload again with same tracker
    verdict2 = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
        current_time=now,
    )
    assert verdict2.passed is False
    assert "Replay detected" in verdict2.reason


def test_stale_telemetry_timestamp_rejected():
    run_id = "run_audit_04"
    nonce = "nonce_stale_111"
    now = datetime.now(timezone.utc)
    # Stale by 10 minutes (exceeds default 300s window)
    stale_time = now - timedelta(minutes=10)

    payload = generate_telemetry_payload(
        candidate_id="carrier_alpha",
        run_id=run_id,
        nonce=nonce,
        secret_key=FIXTURE_SECRET,
        timestamp=stale_time,
    )

    verdict = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=2.0,
        target_temp_max_c=8.0,
        secret_key=FIXTURE_SECRET,
        current_time=now,
        freshness_window_seconds=DEFAULT_FRESHNESS_WINDOW_SECONDS,
    )
    assert verdict.passed is False
    assert "Stale telemetry" in verdict.reason
    assert verdict.timestamp_fresh is False


# -------------------------------------------------------------------------
# 3. Privacy & Secret Scrubbing in Logs
# -------------------------------------------------------------------------

def test_audit_log_scrubber_masks_secrets_and_seeds():
    log_sample = (
        'Connection established. seed="secret_seed_phrase_demo" '
        'and secret="super_secret_hmac_key" with token="bearer_ey12345" '
        'and key="private_api_key_value"'
    )
    sanitized = sanitize_log_message(log_sample)
    assert "secret_seed_phrase_demo" not in sanitized
    assert "super_secret_hmac_key" not in sanitized
    assert "bearer_ey12345" not in sanitized
    assert "private_api_key_value" not in sanitized
    assert "***REDACTED***" in sanitized


# -------------------------------------------------------------------------
# 4. Unknown Candidates & Blind Fallback Prevention
# -------------------------------------------------------------------------

def test_unknown_candidate_never_defaults_to_valid_fixture():
    assert get_carrier_profile("unknown_rogue_carrier") is None
    assert generate_carrier_offer("unknown_rogue_carrier", "run_test") is None
    assert (
        generate_telemetry_payload("unknown_rogue_carrier", "run_test", "nonce", FIXTURE_SECRET)
        is None
    )


# -------------------------------------------------------------------------
# 5. Claim Integrity & Simulation Disclosures
# -------------------------------------------------------------------------

def test_mission_report_and_cards_disclose_simulation_truthfully():
    req = RecoveryRequest(
        shipment_id="VAX-AUDIT-DISCLOSURE",
        cargo_class=CargoClass.VACCINES,
        origin_location="Delhi Hub",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )
    run_ctx = RunContext(req)
    selected_offer = CarrierOffer(
        offer_id="off_test_alpha",
        run_id=req.run_id,
        carrier_id="carrier_alpha",
        carrier_name="Alpha PharmaCold Fleet",
        price=41500.0,
        eta_minutes=38,
        capacity_kg=2000.0,
        supported_temp_min_c=-5.0,
        supported_temp_max_c=12.0,
        is_simulated=True,
    )
    run_ctx.offers = [selected_offer]
    run_ctx.selected_offer = selected_offer
    run_ctx.transition_to(WorkflowState.VALIDATED)
    run_ctx.transition_to(WorkflowState.COLLECTING_QUOTES)
    run_ctx.transition_to(WorkflowState.RANKING)
    run_ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    run_ctx.transition_to(WorkflowState.RECOMMENDED_FOR_CONFIRMATION)

    terminal_result = run_ctx.build_terminal_result()
    report = format_recovery_response(req, run_ctx, terminal_result)

    # Must contain explicit simulation text
    assert "SIMULATION — awaiting user confirmation" in report
    assert "synthetic fixtures generated for reproducible hackathon demonstration" in report
    assert "does not perform actual physical vehicle dispatch" in report

    # Test card envelope
    card = build_final_recommendation_review_card(
        request=req,
        selected_offer=selected_offer,
        audit_verdict_reason="Compliant sensor reading",
        recorded_temp_c=4.2,
    )
    payload = json.loads(card.metadata["card_payload"])
    assert "SIMULATION — awaiting user confirmation" in payload["title"]
    labels = {r["label"]: r["value"] for r in payload["summary_rows"]}
    assert "Simulation Notice" in labels
    assert "SIMULATION — awaiting user confirmation" in labels["Simulation Notice"]


# -------------------------------------------------------------------------
# 6. Server-Side Untrusted Action Validation
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirm_award_rejects_tampered_carrier_id():
    from freightpact.chat_interface import create_acp_chat_protocol, create_text_chat_message
    from freightpact.coordinator_agent import CoordinatorOrchestrator

    req = RecoveryRequest(
        shipment_id="VAX-ACTION-SEC",
        cargo_class=CargoClass.VACCINES,
        origin_location="Delhi Hub",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )
    orchestrator = CoordinatorOrchestrator("auditor", {"carrier_alpha": "alpha"})
    run_ctx = orchestrator.init_mission(req)
    # Set selected offer as Alpha
    selected_offer = CarrierOffer(
        offer_id="off_alpha",
        run_id=req.run_id,
        carrier_id="carrier_alpha",
        carrier_name="Alpha PharmaCold Fleet",
        price=41500.0,
        eta_minutes=38,
        capacity_kg=2000.0,
        supported_temp_min_c=-5.0,
        supported_temp_max_c=12.0,
        is_simulated=True,
    )
    run_ctx.offers = [selected_offer]
    run_ctx.selected_offer = selected_offer
    run_ctx.transition_to(WorkflowState.VALIDATED)
    run_ctx.transition_to(WorkflowState.COLLECTING_QUOTES)
    run_ctx.transition_to(WorkflowState.RANKING)
    run_ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    run_ctx.transition_to(WorkflowState.RECOMMENDED_FOR_CONFIRMATION)

    proto = create_acp_chat_protocol(orchestrator)
    handler = proto._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)]

    class MockContext:
        def __init__(self):
            self.logger = type("L", (), {"info": lambda *a: None, "warning": lambda *a: None, "error": lambda *a: None})()
            self.sent = []

        async def send(self, dest, msg):
            self.sent.append((dest, msg))

    mock_ctx = MockContext()
    # Attacker tries to confirm a rogue carrier not recommended by audit
    attacker_action = json.dumps({
        "action": "confirm_award",
        "run_id": req.run_id,
        "carrier_id": "carrier_rogue_unverified",
    })
    msg = create_text_chat_message(attacker_action)
    await handler(mock_ctx, "attacker_agent", msg)

    # Must reject the tampered carrier
    reply = mock_ctx.sent[1][1]  # index 0 is ack, index 1 is reply
    assert "Security Warning" in reply.text()
    assert "does not match the cryptographically audited candidate" in reply.text()
    # Confirm database state was NOT changed to CONFIRMED_DISPATCHED
    run_summary = orchestrator.storage.get_run_summary(req.run_id)
    assert run_summary is not None
    assert run_summary["current_state"] != "CONFIRMED_DISPATCHED"


@pytest.mark.asyncio
async def test_confirm_award_rejects_unverified_run_state():
    from freightpact.chat_interface import create_acp_chat_protocol, create_text_chat_message
    from freightpact.coordinator_agent import CoordinatorOrchestrator

    req = RecoveryRequest(
        shipment_id="VAX-UNVERIFIED-STATE",
        cargo_class=CargoClass.VACCINES,
        origin_location="Delhi Hub",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )
    orchestrator = CoordinatorOrchestrator("auditor", {"carrier_beta": "beta"})
    run_ctx = orchestrator.init_mission(req)
    # State is still COLLECTING_QUOTES (not verified)
    run_ctx.transition_to(WorkflowState.VALIDATED)
    run_ctx.transition_to(WorkflowState.COLLECTING_QUOTES)

    proto = create_acp_chat_protocol(orchestrator)
    handler = proto._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)]

    class MockContext:
        def __init__(self):
            self.logger = type("L", (), {"info": lambda *a: None, "warning": lambda *a: None, "error": lambda *a: None})()
            self.sent = []

        async def send(self, dest, msg):
            self.sent.append((dest, msg))

    mock_ctx = MockContext()
    attacker_action = json.dumps({
        "action": "confirm_award",
        "run_id": req.run_id,
        "carrier_id": "carrier_beta",
    })
    msg = create_text_chat_message(attacker_action)
    await handler(mock_ctx, "attacker_agent", msg)

    reply = mock_ctx.sent[1][1]
    assert "Action Rejected" in reply.text()
    assert "Only missions with a cryptographically verified recommendation can be confirmed" in reply.text()
