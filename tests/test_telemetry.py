"""Unit tests for cryptographic telemetry verification and simulation fixtures."""

from datetime import datetime, timedelta, timezone
import pytest
from freightpact.fixtures import (
    generate_all_carrier_offers,
    generate_carrier_offer,
    generate_telemetry_payload,
    get_carrier_profile,
)
from freightpact.telemetry import (
    NonceReplayTracker,
    compute_telemetry_signature,
    verify_telemetry_payload,
)

FIXTURE_SECRET = "freightpact-synthetic-test-fixture-key-12345"
TARGET_TEMP_MIN = 2.0
TARGET_TEMP_MAX = 8.0


def test_carrier_profiles_fixture_data():
    """Verify all 3 canonical carrier profiles are distinct and properly configured."""
    beta = get_carrier_profile("carrier_beta")
    alpha = get_carrier_profile("carrier_alpha")
    gamma = get_carrier_profile("carrier_gamma")

    assert beta is not None and alpha is not None and gamma is not None

    # Beta: lowest price (₹38k), but failing temperature (13.8°C) and inactive compressor
    assert beta.base_price == 38000.0
    assert beta.simulated_temp_c == 13.8
    assert beta.simulated_compressor_active is False

    # Alpha: higher price (₹41.5k), safe temperature (4.2°C) and active compressor
    assert alpha.base_price == 41500.0
    assert alpha.simulated_temp_c == 4.2
    assert alpha.simulated_compressor_active is True

    # Gamma: backup (₹43k), safe temperature (4.8°C)
    assert gamma.base_price == 43000.0
    assert gamma.simulated_temp_c == 4.8
    assert gamma.simulated_compressor_active is True


def test_unknown_carrier_never_defaults_to_alpha():
    """Verify unknown carrier IDs return None, never defaulting to Alpha."""
    assert get_carrier_profile("unknown_carrier_xyz") is None
    assert generate_carrier_offer("unknown_carrier_xyz", "run_1") is None
    assert (
        generate_telemetry_payload(
            "unknown_carrier_xyz", "run_1", "nonce_1", FIXTURE_SECRET
        )
        is None
    )


def test_valid_alpha_telemetry_passes_verification():
    """Verify Carrier Alpha's payload has valid signature, compliant temp, and passes audit."""
    run_id = "run_audit_001"
    nonce = "nonce_alpha_test_100"
    now = datetime.now(timezone.utc)
    tracker = NonceReplayTracker()

    payload = generate_telemetry_payload(
        candidate_id="carrier_alpha",
        run_id=run_id,
        nonce=nonce,
        secret_key=FIXTURE_SECRET,
        timestamp=now,
    )
    assert payload is not None

    verdict = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=TARGET_TEMP_MIN,
        target_temp_max_c=TARGET_TEMP_MAX,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
        current_time=now,
    )

    assert verdict.passed is True
    assert verdict.signature_valid is True
    assert verdict.timestamp_fresh is True
    assert verdict.recorded_temp_c == 4.2
    assert verdict.compressor_active is True
    assert "Verified safe" in verdict.reason


def test_beta_telemetry_fails_verification_due_to_temperature_and_compressor():
    """Verify Carrier Beta fails audit due to 13.8°C temperature breach."""
    run_id = "run_audit_002"
    nonce = "nonce_beta_test_200"
    now = datetime.now(timezone.utc)
    tracker = NonceReplayTracker()

    payload = generate_telemetry_payload(
        candidate_id="carrier_beta",
        run_id=run_id,
        nonce=nonce,
        secret_key=FIXTURE_SECRET,
        timestamp=now,
    )
    assert payload is not None

    verdict = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_beta",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=TARGET_TEMP_MIN,
        target_temp_max_c=TARGET_TEMP_MAX,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
        current_time=now,
    )

    assert verdict.passed is False
    assert verdict.signature_valid is True  # Signature itself was valid!
    assert verdict.recorded_temp_c == 13.8
    assert "Temperature non-compliant" in verdict.reason


def test_tampered_temperature_fails_signature_check():
    """Verify tampering with temperature reading breaks cryptographic HMAC verification."""
    run_id = "run_audit_003"
    nonce = "nonce_tamper_300"
    now = datetime.now(timezone.utc)

    # Payload claims 4.2°C, but someone tampers with the value after signing
    original_payload = generate_telemetry_payload(
        candidate_id="carrier_alpha",
        run_id=run_id,
        nonce=nonce,
        secret_key=FIXTURE_SECRET,
        timestamp=now,
    )

    # Alter temperature to 3.5°C without recomputing signature
    tampered_payload = original_payload.model_copy(
        update={"recorded_temp_c": 3.5}
    )

    verdict = verify_telemetry_payload(
        payload=tampered_payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=TARGET_TEMP_MIN,
        target_temp_max_c=TARGET_TEMP_MAX,
        secret_key=FIXTURE_SECRET,
        current_time=now,
    )

    assert verdict.passed is False
    assert verdict.signature_valid is False
    assert "Cryptographic signature verification failed" in verdict.reason


def test_wrong_nonce_rejected():
    """Verify challenge with mismatched nonce is rejected."""
    run_id = "run_audit_004"
    now = datetime.now(timezone.utc)

    payload = generate_telemetry_payload(
        candidate_id="carrier_alpha",
        run_id=run_id,
        nonce="nonce_sent_by_carrier_xyz",
        secret_key=FIXTURE_SECRET,
        timestamp=now,
    )

    verdict = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce="nonce_expected_by_auditor_abc",
        target_temp_min_c=TARGET_TEMP_MIN,
        target_temp_max_c=TARGET_TEMP_MAX,
        secret_key=FIXTURE_SECRET,
        current_time=now,
    )

    assert verdict.passed is False
    assert "Nonce mismatch" in verdict.reason


def test_nonce_replay_protection():
    """Verify attempting to replay a previously audited nonce is blocked."""
    run_id = "run_audit_005"
    nonce = "replayed_nonce_500"
    now = datetime.now(timezone.utc)
    tracker = NonceReplayTracker()

    payload = generate_telemetry_payload(
        candidate_id="carrier_alpha",
        run_id=run_id,
        nonce=nonce,
        secret_key=FIXTURE_SECRET,
        timestamp=now,
    )

    # First attempt: PASS and mark nonce spent
    verdict1 = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=TARGET_TEMP_MIN,
        target_temp_max_c=TARGET_TEMP_MAX,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
        current_time=now,
    )
    assert verdict1.passed is True

    # Second attempt with same nonce: Replay rejected!
    verdict2 = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=TARGET_TEMP_MIN,
        target_temp_max_c=TARGET_TEMP_MAX,
        secret_key=FIXTURE_SECRET,
        nonce_tracker=tracker,
        current_time=now,
    )
    assert verdict2.passed is False
    assert "Replay detected" in verdict2.reason


def test_stale_timestamp_rejected():
    """Verify telemetry payload with timestamp older than 300 seconds is rejected."""
    run_id = "run_audit_006"
    nonce = "nonce_stale_600"
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=350)  # 350s old > 300s threshold

    payload = generate_telemetry_payload(
        candidate_id="carrier_alpha",
        run_id=run_id,
        nonce=nonce,
        secret_key=FIXTURE_SECRET,
        timestamp=old_time,
    )

    verdict = verify_telemetry_payload(
        payload=payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=TARGET_TEMP_MIN,
        target_temp_max_c=TARGET_TEMP_MAX,
        secret_key=FIXTURE_SECRET,
        current_time=now,
    )

    assert verdict.passed is False
    assert verdict.timestamp_fresh is False
    assert "Stale telemetry" in verdict.reason


def test_wrong_candidate_identity_rejected():
    """Verify payload signed for Carrier Gamma cannot be passed off as Carrier Alpha."""
    run_id = "run_audit_007"
    nonce = "nonce_spoof_700"
    now = datetime.now(timezone.utc)

    # Gamma generates its own valid payload
    gamma_payload = generate_telemetry_payload(
        candidate_id="carrier_gamma",
        run_id=run_id,
        nonce=nonce,
        secret_key=FIXTURE_SECRET,
        timestamp=now,
    )

    # Attacker presents Gamma's payload to audit Carrier Alpha
    verdict = verify_telemetry_payload(
        payload=gamma_payload,
        expected_candidate_id="carrier_alpha",
        expected_run_id=run_id,
        expected_nonce=nonce,
        target_temp_min_c=TARGET_TEMP_MIN,
        target_temp_max_c=TARGET_TEMP_MAX,
        secret_key=FIXTURE_SECRET,
        current_time=now,
    )

    assert verdict.passed is False
    assert "Identity mismatch" in verdict.reason
