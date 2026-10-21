"""Robustness and failure handling tests for storage, persistence, and timeouts."""

import os
import tempfile
import pytest
from freightpact.audit_log import sanitize_log_message
from freightpact.models import AuditVerdict, CargoClass, CarrierOffer, RecoveryRequest
from freightpact.storage import StorageRepository, StorageError


@pytest.fixture
def temp_storage():
    """Provides an isolated SQLite database repository in a temporary directory."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    repo = StorageRepository(db_path=db_path)
    yield repo
    if os.path.exists(db_path):
        os.remove(db_path)


def test_save_run_and_retrieve_summary(temp_storage):
    """Verify storing and retrieving run metadata."""
    req = RecoveryRequest(
        run_id="run_storage_001",
        shipment_id="VAX-9000",
        cargo_class=CargoClass.VACCINES,
        origin_location="Delhi Gateway",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=45000.0,
        deadline_minutes=40,
    )
    temp_storage.save_run(req, initial_state="RECEIVED")

    summary = temp_storage.get_run_summary("run_storage_001")
    assert summary is not None
    assert summary["shipment_id"] == "VAX-9000"
    assert summary["current_state"] == "RECEIVED"
    assert summary["max_budget"] == 45000.0


def test_duplicate_quote_idempotency(temp_storage):
    """Verify duplicate quote messages for the same run and carrier are rejected by storage."""
    offer = CarrierOffer(
        offer_id="off_101",
        carrier_id="carrier_alpha",
        carrier_name="Alpha",
        run_id="run_storage_002",
        price=41500.0,
        eta_minutes=38,
        capacity_kg=1200.0,
        supported_temp_min_c=1.0,
        supported_temp_max_c=10.0,
    )

    first_save = temp_storage.save_offer(offer)
    second_save = temp_storage.save_offer(offer)

    assert first_save is True
    assert second_save is False  # Duplicate ignored due to UNIQUE constraint!


def test_duplicate_verdict_idempotency(temp_storage):
    """Verify duplicate audit verdicts for same candidate and nonce are ignored."""
    verdict = AuditVerdict(
        candidate_id="carrier_alpha",
        run_id="run_storage_003",
        nonce="nonce_unique_123",
        passed=True,
        reason="Safe",
        recorded_temp_c=4.2,
        compressor_active=True,
        signature_valid=True,
        timestamp_fresh=True,
    )

    first_save = temp_storage.save_audit_verdict(verdict)
    second_save = temp_storage.save_audit_verdict(verdict)

    assert first_save is True
    assert second_save is False  # Duplicate ignored


def test_audit_log_scrubbing():
    """Verify sensitive seeds, keys, and tokens are scrubbed from log emissions."""
    raw_log = '{"event": "AUTH", "seed": "super_secret_agent_mnemonic_phrase", "token": "secret_api_token_123"}'
    sanitized = sanitize_log_message(raw_log)

    assert "super_secret_agent_mnemonic_phrase" not in sanitized
    assert "secret_api_token_123" not in sanitized
    assert "***REDACTED***" in sanitized


def test_invalid_storage_path_raises_error():
    """Verify pointing SQLite to an impossible directory raises StorageError."""
    with pytest.raises(StorageError):
        StorageRepository(db_path="/nonexistent_dir_9999/test.db")
