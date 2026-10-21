"""Deterministic ranking rule for candidate carrier offers in FreightPact.

Prioritizes cost efficiency (lowest quote first), with arrival ETA as deterministic tie-breaker.
"""

from __future__ import annotations

from typing import List
from freightpact.models import CarrierOffer


def rank_carrier_offers(offers: List[CarrierOffer]) -> List[CarrierOffer]:
    """Deterministically ranks eligible carrier offers.

    Ranking Rule:
    1. Primary Sort Key: Price ASC (cheapest quote first).
    2. Secondary Sort Key (Tie-breaker): ETA ASC (shortest arrival time first).
    3. Tertiary Sort Key (Deterministic stable tie-breaker): carrier_id ASC.

    Args:
        offers: Pre-filtered list of eligible carrier offers satisfying hard constraints.

    Returns:
        Sorted list of carrier offers ordered by preference.
    """
    if not offers:
        return []

    return sorted(
        offers,
        key=lambda offer: (
            offer.price,
            offer.eta_minutes,
            offer.carrier_id,
        ),
    )
