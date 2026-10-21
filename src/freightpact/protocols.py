"""Internal structured message models for agent-to-agent communication.

These models are used for internal coordination between Recovery Coordinator,
Carrier agents, and the Telemetry Auditor. They are strictly typed uagents.Model schemas.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from uagents import Model
try:
    from pydantic.v1 import Field
except ImportError:
    from pydantic import Field


class QuoteRequestMsg(Model):
    """Broadcast by Recovery Coordinator to candidate carriers to collect quotes."""

    correlation_id: str = Field(
        default_factory=lambda: f"corr_{uuid4().hex[:12]}",
        description="Correlation ID for tracing message exchange",
    )
    run_id: str = Field(
        ...,
        description="Recovery mission run ID",
    )
    shipment_id: str = Field(
        ...,
        description="Distressed shipment ID",
    )
    cargo_class: str = Field(
        ...,
        description="Cargo class (e.g. vaccines, biologics)",
    )
    origin_location: str = Field(
        ...,
        description="Location of the distressed vehicle",
    )
    temp_min_c: float = Field(
        ...,
        description="Required minimum temperature in Celsius",
    )
    temp_max_c: float = Field(
        ...,
        description="Required maximum temperature in Celsius",
    )
    max_budget: float = Field(
        ...,
        description="Budget ceiling in INR",
    )
    deadline_minutes: int = Field(
        ...,
        description="Arrival deadline in minutes",
    )
    required_capacity_kg: float = Field(
        default=1000.0,
        description="Required payload capacity in kg",
    )
    timestamp_iso: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Dispatch timestamp in ISO-8601",
    )


class QuoteOfferMsg(Model):
    """Returned by an independent carrier agent in response to a QuoteRequestMsg."""

    correlation_id: str = Field(
        ...,
        description="Echoed correlation ID matching the request",
    )
    run_id: str = Field(
        ...,
        description="Associated recovery run ID",
    )
    carrier_id: str = Field(
        ...,
        description="Explicit identifier of the carrier (e.g. 'carrier_alpha')",
    )
    carrier_name: str = Field(
        ...,
        description="Human-readable business name of the carrier",
    )
    price: float = Field(
        ...,
        description="Offered quote price",
    )
    eta_minutes: int = Field(
        ...,
        description="Estimated arrival time in minutes",
    )
    capacity_kg: float = Field(
        ...,
        description="Vehicle capacity in kg",
    )
    supported_temp_min_c: float = Field(
        ...,
        description="Lowest certified temperature capability",
    )
    supported_temp_max_c: float = Field(
        ...,
        description="Highest certified temperature capability",
    )
    is_simulated: bool = Field(
        default=True,
        description="Explicit disclosure that this quote is synthetic demo data",
    )
    timestamp_iso: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Offer timestamp in ISO-8601",
    )


class AuditChallengeMsg(Model):
    """Sent by Recovery Coordinator to Telemetry Auditor to challenge leading candidate."""

    correlation_id: str = Field(
        default_factory=lambda: f"corr_{uuid4().hex[:12]}",
        description="Correlation ID",
    )
    run_id: str = Field(
        ...,
        description="Recovery run ID",
    )
    candidate_id: str = Field(
        ...,
        description="Explicit candidate carrier identifier being audited",
    )
    nonce: str = Field(
        ...,
        description="Fresh cryptographically random challenge nonce",
    )
    target_temp_min_c: float = Field(
        ...,
        description="Required minimum safe temperature",
    )
    target_temp_max_c: float = Field(
        ...,
        description="Required maximum safe temperature",
    )
    timestamp_iso: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Challenge dispatch timestamp in ISO-8601",
    )


class AuditVerdictMsg(Model):
    """Returned by Telemetry Auditor to Recovery Coordinator with independent audit verdict."""

    correlation_id: str = Field(
        ...,
        description="Echoed correlation ID",
    )
    run_id: str = Field(
        ...,
        description="Recovery run ID",
    )
    candidate_id: str = Field(
        ...,
        description="Candidate carrier evaluated",
    )
    nonce: str = Field(
        ...,
        description="Echoed challenge nonce",
    )
    passed: bool = Field(
        ...,
        description="Whether the candidate passed cold-chain and cryptographic audit",
    )
    reason: str = Field(
        ...,
        description="Human and machine readable reason for verdict",
    )
    recorded_temp_c: float = Field(
        ...,
        description="Audited temperature reading",
    )
    compressor_active: bool = Field(
        ...,
        description="Audited compressor status",
    )
    signature_valid: bool = Field(
        ...,
        description="Whether cryptographic signature verified against fixture key",
    )
    timestamp_iso: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Verdict timestamp in ISO-8601",
    )
