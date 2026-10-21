"""Synthetic carrier profiles and telemetry fixtures for FreightPact.

CRITICAL DISCLOSURE: All data in this module represents synthetic demo fixtures.
There is no live GPS tracking, physical sensor hardware, or real pharmaceutical cargo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional
from freightpact.models import CarrierOffer, TelemetryPayload
from freightpact.telemetry import compute_telemetry_signature


class CarrierProfile:
    """Static metadata and deterministic behavior profile for a simulated carrier."""

    def __init__(
        self,
        carrier_id: str,
        name: str,
        base_price: float,
        eta_minutes: int,
        capacity_kg: float,
        temp_min_c: float,
        temp_max_c: float,
        simulated_temp_c: float,
        simulated_compressor_active: bool,
        notes: str,
    ):
        self.carrier_id = carrier_id
        self.name = name
        self.base_price = base_price
        self.eta_minutes = eta_minutes
        self.capacity_kg = capacity_kg
        self.temp_min_c = temp_min_c
        self.temp_max_c = temp_max_c
        self.simulated_temp_c = simulated_temp_c
        self.simulated_compressor_active = simulated_compressor_active
        self.notes = notes


# Three canonical demo carrier fixtures:
# 1. Beta: Lowest quote (₹38,000), but vehicle has failed cooling (13.8°C, compressor inactive). MUST FAIL AUDIT.
# 2. Alpha: Compliant quote (₹41,500, 38m ETA) with verified cooling (4.2°C, compressor active). CANDIDATE FOR AWARD.
# 3. Gamma: Compliant backup quote (₹43,000, 40m ETA) with safe cooling (4.8°C, compressor active). BACKUP CANDIDATE.
DEMO_CARRIERS: Dict[str, CarrierProfile] = {
    "carrier_beta": CarrierProfile(
        carrier_id="carrier_beta",
        name="Beta Logistics (Budget Express)",
        base_price=38000.0,
        eta_minutes=32,
        capacity_kg=1500.0,
        temp_min_c=1.0,
        temp_max_c=10.0,
        simulated_temp_c=13.8,               # Dangerous temperature breach!
        simulated_compressor_active=False,  # Compressor broken!
        notes="Cheapest digital quote; simulated broken reefer unit to demonstrate verification failover.",
    ),
    "carrier_alpha": CarrierProfile(
        carrier_id="carrier_alpha",
        name="Alpha PharmaCold Fleet",
        base_price=41500.0,
        eta_minutes=38,
        capacity_kg=2000.0,
        temp_min_c=-5.0,
        temp_max_c=12.0,
        simulated_temp_c=4.2,                # Optimal cold chain (+2°C to +8°C)
        simulated_compressor_active=True,   # Active cooling
        notes="Reliable pharma carrier; simulated compliant unit for recommended recovery award.",
    ),
    "carrier_gamma": CarrierProfile(
        carrier_id="carrier_gamma",
        name="Gamma Reliable Transport",
        base_price=43000.0,
        eta_minutes=40,
        capacity_kg=1800.0,
        temp_min_c=0.0,
        temp_max_c=10.0,
        simulated_temp_c=4.8,                # Compliant backup
        simulated_compressor_active=True,   # Active cooling
        notes="Backup carrier profile; compliant equipment if Alpha fails.",
    ),
}


def get_carrier_profile(carrier_id: str) -> Optional[CarrierProfile]:
    """Retrieves carrier profile by exact ID. Never defaults unknown IDs to Alpha."""
    return DEMO_CARRIERS.get(carrier_id)


def generate_carrier_offer(
    carrier_id: str,
    run_id: str,
) -> Optional[CarrierOffer]:
    """Generates a structured CarrierOffer from deterministic scenario fixtures.

    Returns None if carrier_id is unknown. Never defaults to Alpha.
    """
    profile = get_carrier_profile(carrier_id)
    if not profile:
        return None

    return CarrierOffer(
        carrier_id=profile.carrier_id,
        carrier_name=profile.name,
        run_id=run_id,
        price=profile.base_price,
        eta_minutes=profile.eta_minutes,
        capacity_kg=profile.capacity_kg,
        supported_temp_min_c=profile.temp_min_c,
        supported_temp_max_c=profile.temp_max_c,
        is_simulated=True,
        source_label=f"SIMULATED_FIXTURE:{profile.carrier_id}",
    )


def generate_all_carrier_offers(run_id: str) -> List[CarrierOffer]:
    """Generates offers for all registered demo carrier agents."""
    offers = []
    for carrier_id in DEMO_CARRIERS:
        offer = generate_carrier_offer(carrier_id, run_id)
        if offer:
            offers.append(offer)
    return offers


def generate_telemetry_payload(
    candidate_id: str,
    run_id: str,
    nonce: str,
    secret_key: str,
    timestamp: Optional[datetime] = None,
    tamper_temp_c: Optional[float] = None,
    tamper_signature: Optional[str] = None,
) -> Optional[TelemetryPayload]:
    """Retrieves synthetic sensor readings for a candidate and signs them using the fixture secret.

    Returns None if candidate_id is unknown. Never defaults unknown IDs to Alpha.
    """
    profile = get_carrier_profile(candidate_id)
    if not profile:
        return None

    ts = timestamp or datetime.now(timezone.utc)
    temp_c = tamper_temp_c if tamper_temp_c is not None else profile.simulated_temp_c
    compressor = profile.simulated_compressor_active

    # Compute valid signature relative to fixture secret
    sig = tamper_signature or compute_telemetry_signature(
        candidate_id=candidate_id,
        run_id=run_id,
        nonce=nonce,
        timestamp=ts,
        recorded_temp_c=temp_c,
        compressor_active=compressor,
        reefer_battery_pct=95.0,
        pressure_bar=2.1,
        secret_key=secret_key,
    )

    return TelemetryPayload(
        candidate_id=candidate_id,
        run_id=run_id,
        nonce=nonce,
        timestamp=ts,
        recorded_temp_c=temp_c,
        compressor_active=compressor,
        reefer_battery_pct=95.0,
        pressure_bar=2.1,
        signature=sig,
        is_simulated=True,
    )
