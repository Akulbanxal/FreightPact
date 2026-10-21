"""Unit tests for deterministic hard constraint filtering in policy.py."""

import pytest
from freightpact.models import CargoClass, CarrierOffer, RecoveryRequest
from freightpact.policy import evaluate_hard_constraints, filter_eligible_offers


@pytest.fixture
def baseline_request():
    return RecoveryRequest(
        run_id="run_policy_test",
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Delhi Outer Ring Road",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
        required_capacity_kg=1000.0,
    )


def test_eligible_offer_passes_all_constraints(baseline_request):
    """Verify compliant offer passes all hard constraints."""
    offer = CarrierOffer(
        carrier_id="carrier_alpha",
        carrier_name="Alpha Fleet",
        run_id=baseline_request.run_id,
        price=41500.0,           # Within 48,000
        eta_minutes=38,           # Within 45m
        capacity_kg=1200.0,       # Exceeds 1000kg
        supported_temp_min_c=0.0, # Covers 2.0
        supported_temp_max_c=10.0,# Covers 8.0
    )
    is_eligible, violations = evaluate_hard_constraints(baseline_request, offer)
    assert is_eligible is True
    assert len(violations) == 0


def test_offer_exceeding_budget_rejected(baseline_request):
    """Verify offer exceeding budget ceiling is rejected."""
    offer = CarrierOffer(
        carrier_id="carrier_expensive",
        carrier_name="Luxury Reefer",
        run_id=baseline_request.run_id,
        price=52000.0,            # Exceeds 48,000
        eta_minutes=30,
        capacity_kg=1200.0,
        supported_temp_min_c=1.0,
        supported_temp_max_c=9.0,
    )
    is_eligible, violations = evaluate_hard_constraints(baseline_request, offer)
    assert is_eligible is False
    assert any(v.code == "EXCEEDS_BUDGET" for v in violations)


def test_offer_exceeding_deadline_rejected(baseline_request):
    """Verify offer exceeding arrival deadline is rejected."""
    offer = CarrierOffer(
        carrier_id="carrier_slow",
        carrier_name="Delayed Express",
        run_id=baseline_request.run_id,
        price=35000.0,
        eta_minutes=60,           # Exceeds 45m
        capacity_kg=1200.0,
        supported_temp_min_c=1.0,
        supported_temp_max_c=9.0,
    )
    is_eligible, violations = evaluate_hard_constraints(baseline_request, offer)
    assert is_eligible is False
    assert any(v.code == "EXCEEDS_DEADLINE" for v in violations)


def test_offer_insufficient_capacity_rejected(baseline_request):
    """Verify offer with insufficient payload capacity is rejected."""
    offer = CarrierOffer(
        carrier_id="carrier_small",
        carrier_name="Mini Reefer",
        run_id=baseline_request.run_id,
        price=32000.0,
        eta_minutes=25,
        capacity_kg=600.0,        # Below 1000kg
        supported_temp_min_c=1.0,
        supported_temp_max_c=9.0,
    )
    is_eligible, violations = evaluate_hard_constraints(baseline_request, offer)
    assert is_eligible is False
    assert any(v.code == "INSUFFICIENT_CAPACITY" for v in violations)


def test_offer_incompatible_temperature_capability_rejected(baseline_request):
    """Verify carrier whose certified unit cannot reach target min or max is rejected."""
    # Unit cannot go down to 2.0°C (only supports down to 4.0°C)
    offer = CarrierOffer(
        carrier_id="carrier_warm",
        carrier_name="Warm Reefer",
        run_id=baseline_request.run_id,
        price=35000.0,
        eta_minutes=30,
        capacity_kg=1200.0,
        supported_temp_min_c=4.0, # Cannot reach 2.0°C!
        supported_temp_max_c=12.0,
    )
    is_eligible, violations = evaluate_hard_constraints(baseline_request, offer)
    assert is_eligible is False
    assert any(v.code == "INCOMPATIBLE_TEMPERATURE_BAND" for v in violations)


def test_filter_eligible_offers_excludes_rejected_set(baseline_request):
    """Verify candidates previously recorded as rejected are excluded immediately."""
    offer1 = CarrierOffer(
        carrier_id="carrier_beta",
        carrier_name="Beta Fleet",
        run_id=baseline_request.run_id,
        price=38000.0,
        eta_minutes=32,
        capacity_kg=1200.0,
        supported_temp_min_c=1.0,
        supported_temp_max_c=9.0,
    )
    offer2 = CarrierOffer(
        carrier_id="carrier_alpha",
        carrier_name="Alpha Fleet",
        run_id=baseline_request.run_id,
        price=41500.0,
        eta_minutes=38,
        capacity_kg=1200.0,
        supported_temp_min_c=1.0,
        supported_temp_max_c=9.0,
    )

    # Pretend carrier_beta was already rejected during telemetry audit
    excluded = {"carrier_beta"}
    eligible, violations = filter_eligible_offers(
        baseline_request, [offer1, offer2], excluded_candidate_ids=excluded
    )

    assert len(eligible) == 1
    assert eligible[0].carrier_id == "carrier_alpha"
    assert any(v.carrier_id == "carrier_beta" and v.code == "PREVIOUSLY_REJECTED" for v in violations)
