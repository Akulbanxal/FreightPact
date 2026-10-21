"""Deterministic policy and hard constraint evaluation for FreightPact.

Enforces zero-tolerance rules for cold-chain safety before candidate ranking.
"""

from __future__ import annotations

from typing import List, Tuple
from freightpact.models import CarrierOffer, RecoveryRequest


class ConstraintViolation:
    """Explains why a candidate failed a hard constraint filter."""

    def __init__(self, carrier_id: str, code: str, detail: str):
        self.carrier_id = carrier_id
        self.code = code
        self.detail = detail

    def __repr__(self) -> str:
        return f"ConstraintViolation({self.carrier_id}: [{self.code}] {self.detail})"


def evaluate_hard_constraints(
    request: RecoveryRequest,
    offer: CarrierOffer,
) -> Tuple[bool, List[ConstraintViolation]]:
    """Evaluates whether a candidate carrier offer strictly satisfies all incident hard constraints.

    Checks:
    1. Budget: offer.price <= request.max_budget
    2. Deadline: offer.eta_minutes <= request.deadline_minutes
    3. Capacity: offer.capacity_kg >= request.required_capacity_kg
    4. Temperature Capability:
       offer.supported_temp_min_c <= request.temp_min_c and
       offer.supported_temp_max_c >= request.temp_max_c

    Returns:
        (is_eligible, list_of_violations)
    """
    violations: List[ConstraintViolation] = []

    # 1. Budget constraint
    if offer.price > request.max_budget:
        violations.append(
            ConstraintViolation(
                carrier_id=offer.carrier_id,
                code="EXCEEDS_BUDGET",
                detail=f"Quote price ₹{offer.price:,.2f} exceeds budget ceiling ₹{request.max_budget:,.2f}",
            )
        )

    # 2. Arrival deadline constraint
    if offer.eta_minutes > request.deadline_minutes:
        violations.append(
            ConstraintViolation(
                carrier_id=offer.carrier_id,
                code="EXCEEDS_DEADLINE",
                detail=f"ETA {offer.eta_minutes}m exceeds deadline {request.deadline_minutes}m",
            )
        )

    # 3. Cargo payload capacity constraint
    if offer.capacity_kg < request.required_capacity_kg:
        violations.append(
            ConstraintViolation(
                carrier_id=offer.carrier_id,
                code="INSUFFICIENT_CAPACITY",
                detail=f"Capacity {offer.capacity_kg}kg is below required {request.required_capacity_kg}kg",
            )
        )

    # 4. Temperature band capability constraint
    if (
        offer.supported_temp_min_c > request.temp_min_c
        or offer.supported_temp_max_c < request.temp_max_c
    ):
        violations.append(
            ConstraintViolation(
                carrier_id=offer.carrier_id,
                code="INCOMPATIBLE_TEMPERATURE_BAND",
                detail=(
                    f"Vehicle capability [{offer.supported_temp_min_c}°C to {offer.supported_temp_max_c}°C] "
                    f"does not fully cover required target band [{request.temp_min_c}°C to {request.temp_max_c}°C]"
                ),
            )
        )

    return (len(violations) == 0, violations)


def filter_eligible_offers(
    request: RecoveryRequest,
    offers: List[CarrierOffer],
    excluded_candidate_ids: set[str],
) -> Tuple[List[CarrierOffer], List[ConstraintViolation]]:
    """Filters candidate offers, returning eligible candidates and recorded violations."""
    eligible: List[CarrierOffer] = []
    all_violations: List[ConstraintViolation] = []

    for offer in offers:
        # Exclude candidates previously rejected in the same run
        if offer.carrier_id in excluded_candidate_ids:
            all_violations.append(
                ConstraintViolation(
                    carrier_id=offer.carrier_id,
                    code="PREVIOUSLY_REJECTED",
                    detail=f"Carrier {offer.carrier_id} was already rejected during this run",
                )
            )
            continue

        is_eligible, violations = evaluate_hard_constraints(request, offer)
        if is_eligible:
            eligible.append(offer)
        else:
            all_violations.extend(violations)

    return eligible, all_violations
