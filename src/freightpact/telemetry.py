"""Cryptographic signing and verification utilities for synthetic telemetry fixtures.

Uses standard-library HMAC-SHA256 with constant-time digest comparison (`hmac.compare_digest`).
Validates challenge nonces, payload freshness, sensor ranges, and compressor readiness.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Dict, Optional, Set
from freightpact.models import AuditVerdict, TelemetryPayload

# Default tolerance window for timestamp freshness (in seconds)
DEFAULT_FRESHNESS_WINDOW_SECONDS: float = 300.0


def format_canonical_payload(
    candidate_id: str,
    run_id: str,
    nonce: str,
    timestamp_iso: str,
    recorded_temp_c: float,
    compressor_active: bool,
    reefer_battery_pct: float,
    pressure_bar: float,
) -> str:
    """Produces the exact canonical string representation for cryptographic signing.

    Order and formatting are deterministic to prevent signature mismatch across systems:
    candidate_id|run_id|nonce|timestamp_iso|recorded_temp_c|compressor_active|reefer_battery_pct|pressure_bar
    """
    return (
        f"{candidate_id.strip()}|"
        f"{run_id.strip()}|"
        f"{nonce.strip()}|"
        f"{timestamp_iso.strip()}|"
        f"{recorded_temp_c:.2f}|"
        f"{str(compressor_active).lower()}|"
        f"{reefer_battery_pct:.2f}|"
        f"{pressure_bar:.2f}"
    )


def compute_telemetry_signature(
    candidate_id: str,
    run_id: str,
    nonce: str,
    timestamp: datetime,
    recorded_temp_c: float,
    compressor_active: bool,
    reefer_battery_pct: float = 95.0,
    pressure_bar: float = 2.1,
    secret_key: str = "",
) -> str:
    """Computes an HMAC-SHA256 signature authenticating the payload relative to the fixture secret key."""
    if not secret_key:
        raise ValueError("Cannot sign payload: secret_key cannot be empty.")

    ts_iso = timestamp.astimezone(timezone.utc).isoformat()
    canonical_str = format_canonical_payload(
        candidate_id=candidate_id,
        run_id=run_id,
        nonce=nonce,
        timestamp_iso=ts_iso,
        recorded_temp_c=recorded_temp_c,
        compressor_active=compressor_active,
        reefer_battery_pct=reefer_battery_pct,
        pressure_bar=pressure_bar,
    )

    signature = hmac.new(
        key=secret_key.encode("utf-8"),
        msg=canonical_str.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    return signature


class NonceReplayTracker:
    """Tracks spent challenge nonces per run ID to prevent replay attacks."""

    def __init__(self):
        # Maps run_id -> set of spent nonces
        self._spent_nonces: Dict[str, Set[str]] = {}

    def is_nonce_spent(self, run_id: str, nonce: str) -> bool:
        return nonce in self._spent_nonces.get(run_id, set())

    def mark_nonce_spent(self, run_id: str, nonce: str) -> None:
        if run_id not in self._spent_nonces:
            self._spent_nonces[run_id] = set()
        self._spent_nonces[run_id].add(nonce)


def verify_telemetry_payload(
    payload: TelemetryPayload,
    expected_candidate_id: str,
    expected_run_id: str,
    expected_nonce: str,
    target_temp_min_c: float,
    target_temp_max_c: float,
    secret_key: str,
    nonce_tracker: Optional[NonceReplayTracker] = None,
    current_time: Optional[datetime] = None,
    freshness_window_seconds: float = DEFAULT_FRESHNESS_WINDOW_SECONDS,
) -> AuditVerdict:
    """Audits a telemetry payload against cryptographic and physical cold-chain requirements.

    Evaluation checks:
    1. Candidate identity matching
    2. Run ID matching
    3. Nonce validity and freshness (replay prevention)
    4. Timestamp freshness (reject stale payloads)
    5. Cryptographic HMAC-SHA256 signature verification (using hmac.compare_digest)
    6. Equipment temperature band compliance (temp_min <= recorded_temp <= temp_max)
    7. Active cooling compressor status
    """
    now = current_time or datetime.now(timezone.utc)
    ts_fresh = True
    sig_valid = False

    # Check 1: Candidate identity
    if payload.candidate_id != expected_candidate_id:
        return AuditVerdict(
            candidate_id=payload.candidate_id,
            run_id=expected_run_id,
            nonce=payload.nonce,
            passed=False,
            reason=f"Identity mismatch: payload carrier '{payload.candidate_id}' does not match challenge '{expected_candidate_id}'",
            recorded_temp_c=payload.recorded_temp_c,
            compressor_active=payload.compressor_active,
            signature_valid=False,
            timestamp_fresh=False,
        )

    # Check 2: Run ID matching
    if payload.run_id != expected_run_id:
        return AuditVerdict(
            candidate_id=expected_candidate_id,
            run_id=expected_run_id,
            nonce=payload.nonce,
            passed=False,
            reason=f"Run mismatch: payload run '{payload.run_id}' does not match challenge run '{expected_run_id}'",
            recorded_temp_c=payload.recorded_temp_c,
            compressor_active=payload.compressor_active,
            signature_valid=False,
            timestamp_fresh=False,
        )

    # Check 3: Nonce match
    if payload.nonce != expected_nonce:
        return AuditVerdict(
            candidate_id=expected_candidate_id,
            run_id=expected_run_id,
            nonce=payload.nonce,
            passed=False,
            reason=f"Nonce mismatch: received '{payload.nonce}' does not match challenge '{expected_nonce}'",
            recorded_temp_c=payload.recorded_temp_c,
            compressor_active=payload.compressor_active,
            signature_valid=False,
            timestamp_fresh=False,
        )

    # Check 4: Replay protection
    if nonce_tracker and nonce_tracker.is_nonce_spent(expected_run_id, payload.nonce):
        return AuditVerdict(
            candidate_id=expected_candidate_id,
            run_id=expected_run_id,
            nonce=payload.nonce,
            passed=False,
            reason=f"Replay detected: nonce '{payload.nonce}' has already been spent in run '{expected_run_id}'",
            recorded_temp_c=payload.recorded_temp_c,
            compressor_active=payload.compressor_active,
            signature_valid=False,
            timestamp_fresh=False,
        )

    # Check 5: Timestamp freshness
    payload_dt = payload.timestamp
    if payload_dt.tzinfo is None:
        payload_dt = payload_dt.replace(tzinfo=timezone.utc)
    delta_seconds = abs((now - payload_dt).total_seconds())
    if delta_seconds > freshness_window_seconds:
        ts_fresh = False
        return AuditVerdict(
            candidate_id=expected_candidate_id,
            run_id=expected_run_id,
            nonce=payload.nonce,
            passed=False,
            reason=f"Stale telemetry: timestamp age ({delta_seconds:.1f}s) exceeds freshness window ({freshness_window_seconds:.1f}s)",
            recorded_temp_c=payload.recorded_temp_c,
            compressor_active=payload.compressor_active,
            signature_valid=False,
            timestamp_fresh=False,
        )

    # Check 6: Cryptographic signature verification using constant-time digest comparison
    try:
        expected_sig = compute_telemetry_signature(
            candidate_id=payload.candidate_id,
            run_id=payload.run_id,
            nonce=payload.nonce,
            timestamp=payload.timestamp,
            recorded_temp_c=payload.recorded_temp_c,
            compressor_active=payload.compressor_active,
            reefer_battery_pct=payload.reefer_battery_pct or 95.0,
            pressure_bar=payload.pressure_bar or 2.1,
            secret_key=secret_key,
        )
        sig_valid = hmac.compare_digest(expected_sig, payload.signature)
    except Exception as exc:
        sig_valid = False

    if not sig_valid:
        return AuditVerdict(
            candidate_id=expected_candidate_id,
            run_id=expected_run_id,
            nonce=payload.nonce,
            passed=False,
            reason="Cryptographic signature verification failed: payload tampered or invalid fixture secret",
            recorded_temp_c=payload.recorded_temp_c,
            compressor_active=payload.compressor_active,
            signature_valid=False,
            timestamp_fresh=True,
        )

    # Mark nonce spent once signature is authenticated
    if nonce_tracker:
        nonce_tracker.mark_nonce_spent(expected_run_id, payload.nonce)

    # Check 7: Equipment temperature compliance
    if payload.recorded_temp_c < target_temp_min_c or payload.recorded_temp_c > target_temp_max_c:
        return AuditVerdict(
            candidate_id=expected_candidate_id,
            run_id=expected_run_id,
            nonce=payload.nonce,
            passed=False,
            reason=(
                f"Temperature non-compliant: recorded {payload.recorded_temp_c:.1f}°C is outside "
                f"required safe range [{target_temp_min_c:.1f}°C to {target_temp_max_c:.1f}°C]"
            ),
            recorded_temp_c=payload.recorded_temp_c,
            compressor_active=payload.compressor_active,
            signature_valid=True,
            timestamp_fresh=True,
        )

    # Check 8: Compressor status
    if not payload.compressor_active:
        return AuditVerdict(
            candidate_id=expected_candidate_id,
            run_id=expected_run_id,
            nonce=payload.nonce,
            passed=False,
            reason="Equipment failure: reefer cooling compressor is INACTIVE",
            recorded_temp_c=payload.recorded_temp_c,
            compressor_active=False,
            signature_valid=True,
            timestamp_fresh=True,
        )

    # All checks passed
    return AuditVerdict(
        candidate_id=expected_candidate_id,
        run_id=expected_run_id,
        nonce=payload.nonce,
        passed=True,
        reason=(
            f"Verified safe: {payload.recorded_temp_c:.1f}°C within [{target_temp_min_c:.1f}°C..{target_temp_max_c:.1f}°C], "
            "compressor ACTIVE, cryptographic signature authentic relative to demo fixture key"
        ),
        recorded_temp_c=payload.recorded_temp_c,
        compressor_active=True,
        signature_valid=True,
        timestamp_fresh=True,
    )
