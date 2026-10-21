"""Unit tests for deterministic candidate ranking and tie-breaking in ranking.py."""

from freightpact.models import CarrierOffer
from freightpact.ranking import rank_carrier_offers


def test_ranking_price_primary_order():
    """Verify offers are ordered with lowest price first."""
    o1 = CarrierOffer(
        carrier_id="carrier_gamma",
        carrier_name="Gamma",
        run_id="run_1",
        price=43000.0,
        eta_minutes=40,
        capacity_kg=1000.0,
        supported_temp_min_c=1.0,
        supported_temp_max_c=9.0,
    )
    o2 = CarrierOffer(
        carrier_id="carrier_beta",
        carrier_name="Beta",
        run_id="run_1",
        price=38000.0,
        eta_minutes=32,
        capacity_kg=1000.0,
        supported_temp_min_c=1.0,
        supported_temp_max_c=9.0,
    )
    o3 = CarrierOffer(
        carrier_id="carrier_alpha",
        carrier_name="Alpha",
        run_id="run_1",
        price=41500.0,
        eta_minutes=38,
        capacity_kg=1000.0,
        supported_temp_min_c=1.0,
        supported_temp_max_c=9.0,
    )

    ranked = rank_carrier_offers([o1, o2, o3])
    # Expect: Beta (38k), Alpha (41.5k), Gamma (43k)
    assert [c.carrier_id for c in ranked] == [
        "carrier_beta",
        "carrier_alpha",
        "carrier_gamma",
    ]


def test_ranking_eta_tie_breaker():
    """Verify when price is identical, shorter ETA breaks the tie."""
    o1 = CarrierOffer(
        carrier_id="carrier_same_price_slow",
        carrier_name="Slow",
        run_id="run_1",
        price=40000.0,
        eta_minutes=42,
        capacity_kg=1000.0,
        supported_temp_min_c=1.0,
        supported_temp_max_c=9.0,
    )
    o2 = CarrierOffer(
        carrier_id="carrier_same_price_fast",
        carrier_name="Fast",
        run_id="run_1",
        price=40000.0,
        eta_minutes=25,
        capacity_kg=1000.0,
        supported_temp_min_c=1.0,
        supported_temp_max_c=9.0,
    )

    ranked = rank_carrier_offers([o1, o2])
    assert ranked[0].carrier_id == "carrier_same_price_fast"
    assert ranked[1].carrier_id == "carrier_same_price_slow"


def test_ranking_empty_offers():
    """Verify ranking empty list returns empty list cleanly."""
    assert rank_carrier_offers([]) == []
