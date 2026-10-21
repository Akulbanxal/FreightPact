"""Unit tests for FreightPact domain models."""

import pytest
from pydantic import ValidationError
from freightpact.models import (
    CargoClass,
    CarrierOffer,
    RecoveryRequest,
    TelemetryPayload,
    AuditVerdict,
    RecoveryResult,
)


def test_valid_recovery_request():
    """Verify clean instantiation of RecoveryRequest with valid constraints."""
    req = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Highway Hub, Gurugram",
        destination_location="Apex BioCold Storage, Delhi",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
        required_capacity_kg=1200.0,
    )
    assert req.shipment_id == "VAX-8821"
    assert req.temp_min_c == 2.0
    assert req.temp_max_c == 8.0
    assert req.max_budget == 48000.0
    assert req.deadline_minutes == 45
    assert req.run_id.startswith("run_")


def test_invalid_temperature_range_rejected():
    """Verify temp_max_c must be strictly greater than temp_min_c."""
    with pytest.raises(ValidationError) as exc:
        RecoveryRequest(
            shipment_id="VAX-8821",
            origin_location="Highway 1",
            temp_min_c=8.0,
            temp_max_c=2.0,  # Invalid: max <= min
            max_budget=50000.0,
            deadline_minutes=30,
        )
    assert "temp_max_c" in str(exc.value)


def test_invalid_budget_rejected():
    """Verify non-positive budget ceiling is rejected."""
    with pytest.raises(ValidationError) as exc:
        RecoveryRequest(
            shipment_id="VAX-8821",
            origin_location="Highway 1",
            temp_min_c=2.0,
            temp_max_c=8.0,
            max_budget=0.0,  # Invalid budget <= 0
            deadline_minutes=30,
        )
    assert "max_budget" in str(exc.value)


def test_carrier_offer_simulation_tagging():
    """Verify CarrierOffer carries explicit simulation disclosure."""
    offer = CarrierOffer(
        carrier_id="carrier_alpha",
        carrier_name="Alpha Coldways Logistics",
        run_id="run_test_001",
        price=41500.0,
        eta_minutes=38,
        capacity_kg=1500.0,
        supported_temp_min_c=1.0,
        supported_temp_max_c=10.0,
    )
    assert offer.is_simulated is True
    assert offer.source_label == "SIMULATED_DEMO_FIXTURE"
    assert offer.offer_id.startswith("off_")


def test_carrier_offer_invalid_temp_range():
    """Verify CarrierOffer rejects invalid capability range."""
    with pytest.raises(ValidationError):
        CarrierOffer(
            carrier_id="carrier_test",
            carrier_name="Test Carrier",
            run_id="run_001",
            price=30000.0,
            eta_minutes=20,
            capacity_kg=1000.0,
            supported_temp_min_c=10.0,
            supported_temp_max_c=5.0,  # Invalid
        )


def test_telemetry_payload_and_verdict():
    """Verify TelemetryPayload and AuditVerdict instantiation."""
    telemetry = TelemetryPayload(
        candidate_id="carrier_alpha",
        run_id="run_001",
        nonce="nonce_test_123",
        recorded_temp_c=4.2,
        compressor_active=True,
        signature="mock_valid_hmac_signature",
    )
    assert telemetry.recorded_temp_c == 4.2
    assert telemetry.compressor_active is True
    assert telemetry.is_simulated is True

    verdict = AuditVerdict(
        candidate_id="carrier_alpha",
        run_id="run_001",
        nonce="nonce_test_123",
        passed=True,
        reason="Telemetry within safe band +2..+8°C; Compressor active",
        recorded_temp_c=4.2,
        compressor_active=True,
        signature_valid=True,
        timestamp_fresh=True,
    )
    assert verdict.passed is True
