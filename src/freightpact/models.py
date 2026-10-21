"""Domain models for FreightPact: Autonomous Cold-Chain Shipment Recovery Coordinator.

All models are strictly validated using Pydantic V2 and carry explicit simulation/source tags.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field, field_validator


class CargoClass(str, Enum):
    """Cold-chain pharmaceutical cargo classes."""
    VACCINES = "vaccines"
    BIOLOGICS = "biologics"
    INSULIN = "insulin"
    PHARMA_GENERAL = "pharma_general"


class RecoveryRequest(BaseModel):
    """Shipment incident recovery request submitted by dispatcher or upstream TMS."""

    run_id: str = Field(
        default_factory=lambda: f"run_{uuid4().hex[:12]}",
        description="Unique identifier for this specific recovery orchestration run",
    )
    shipment_id: str = Field(
        ...,
        description="Identifier of the distressed shipment (e.g., 'VAX-8821')",
    )
    cargo_class: CargoClass = Field(
        default=CargoClass.VACCINES,
        description="Class of temperature-sensitive cargo",
    )
    origin_location: str = Field(
        ...,
        description="Current location of the distressed vehicle / incident site",
    )
    destination_location: Optional[str] = Field(
        default=None,
        description="Intended delivery destination",
    )
    temp_min_c: float = Field(
        ...,
        description="Lower bound of acceptable cargo temperature band in Celsius (e.g., 2.0)",
    )
    temp_max_c: float = Field(
        ...,
        description="Upper bound of acceptable cargo temperature band in Celsius (e.g., 8.0)",
    )
    max_budget: float = Field(
        ...,
        gt=0,
        description="Maximum financial budget ceiling for replacement carrier (e.g., in INR)",
    )
    deadline_minutes: int = Field(
        ...,
        gt=0,
        description="Maximum acceptable carrier arrival ETA in minutes (e.g., 45)",
    )
    required_capacity_kg: float = Field(
        default=1000.0,
        gt=0,
        description="Minimum cargo capacity required in kg",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the recovery request was initiated",
    )

    @field_validator("temp_max_c")
    @classmethod
    def validate_temp_range(cls, v: float, info) -> float:
        min_temp = info.data.get("temp_min_c")
        if min_temp is not None and v <= min_temp:
            raise ValueError(
                f"temp_max_c ({v}°C) must be strictly greater than temp_min_c ({min_temp}°C)"
            )
        return v


class CarrierOffer(BaseModel):
    """Structured quote and availability offer submitted by an independent carrier."""

    offer_id: str = Field(
        default_factory=lambda: f"off_{uuid4().hex[:12]}",
        description="Unique identifier for this offer",
    )
    carrier_id: str = Field(
        ...,
        description="Unique identity of the carrier agent (e.g., 'carrier_alpha')",
    )
    carrier_name: str = Field(
        ...,
        description="Human-readable business name of the carrier",
    )
    run_id: str = Field(
        ...,
        description="The recovery run ID this offer is responding to",
    )
    price: float = Field(
        ...,
        gt=0,
        description="Offered price quote for the recovery mission",
    )
    eta_minutes: int = Field(
        ...,
        ge=0,
        description="Estimated arrival time at distressed vehicle location in minutes",
    )
    capacity_kg: float = Field(
        ...,
        gt=0,
        description="Available refrigerated cargo payload capacity in kg",
    )
    supported_temp_min_c: float = Field(
        ...,
        description="Lowest certified temperature capability of the vehicle unit",
    )
    supported_temp_max_c: float = Field(
        ...,
        description="Highest certified temperature capability of the vehicle unit",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the offer was issued",
    )
    is_simulated: bool = Field(
        default=True,
        description="Explicit disclosure that this quote is synthetic demo data",
    )
    source_label: str = Field(
        default="SIMULATED_DEMO_FIXTURE",
        description="Explicit source label for judges and auditors",
    )

    @field_validator("supported_temp_max_c")
    @classmethod
    def validate_supported_temp_range(cls, v: float, info) -> float:
        min_temp = info.data.get("supported_temp_min_c")
        if min_temp is not None and v <= min_temp:
            raise ValueError(
                f"supported_temp_max_c ({v}°C) must be greater than supported_temp_min_c ({min_temp}°C)"
            )
        return v


class TelemetryPayload(BaseModel):
    """Reefer equipment sensor telemetry payload retrieved for independent audit."""

    candidate_id: str = Field(
        ...,
        description="Candidate carrier ID whose vehicle is being audited",
    )
    run_id: str = Field(
        ...,
        description="The recovery run ID associated with this audit challenge",
    )
    nonce: str = Field(
        ...,
        description="Fresh cryptographically generated challenge nonce preventing replay",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when telemetry readings were sampled",
    )
    recorded_temp_c: float = Field(
        ...,
        description="Active temperature sensor reading inside the refrigerated compartment in Celsius",
    )
    compressor_active: bool = Field(
        ...,
        description="Whether the cooling compressor unit is currently running",
    )
    reefer_battery_pct: Optional[float] = Field(
        default=95.0,
        ge=0,
        le=100,
        description="Auxiliary reefer battery level percentage",
    )
    pressure_bar: Optional[float] = Field(
        default=2.1,
        ge=0,
        description="Refrigerant line pressure in bars",
    )
    signature: str = Field(
        ...,
        description="Cryptographic HMAC-SHA256 signature authenticating sensor reading against fixture key",
    )
    is_simulated: bool = Field(
        default=True,
        description="Explicit disclosure: synthetic sensor data signed by fixture key",
    )


class AuditVerdict(BaseModel):
    """Typed evaluation verdict returned by the independent Telemetry Auditor."""

    candidate_id: str = Field(
        ...,
        description="Candidate carrier evaluated",
    )
    run_id: str = Field(
        ...,
        description="Associated recovery run ID",
    )
    nonce: str = Field(
        ...,
        description="Echoed challenge nonce proving evaluation freshness",
    )
    passed: bool = Field(
        ...,
        description="True if all cryptographic and cold-chain constraints passed; False otherwise",
    )
    reason: str = Field(
        ...,
        description="Machine-readable and human-verifiable explanation of the audit verdict",
    )
    recorded_temp_c: float = Field(
        ...,
        description="Temperature recorded during the audit",
    )
    compressor_active: bool = Field(
        ...,
        description="Compressor status observed",
    )
    signature_valid: bool = Field(
        ...,
        description="Whether the cryptographic HMAC signature verified correctly",
    )
    timestamp_fresh: bool = Field(
        ...,
        description="Whether the reading was within acceptable freshness threshold",
    )
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when verdict was rendered",
    )


class AuditEvent(BaseModel):
    """Immutable audit trail event recorded during workflow execution."""

    event_id: str = Field(
        default_factory=lambda: f"evt_{uuid4().hex[:12]}",
        description="Unique audit event ID",
    )
    run_id: str = Field(
        ...,
        description="Associated recovery run ID",
    )
    state: str = Field(
        ...,
        description="Workflow state when event was logged",
    )
    message: str = Field(
        ...,
        description="Human and machine readable log message",
    )
    candidate_id: Optional[str] = Field(
        default=None,
        description="Candidate carrier involved, if applicable",
    )
    details: dict = Field(
        default_factory=dict,
        description="Structured key-value context for verification",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of event occurrence",
    )


class RecoveryResult(BaseModel):
    """Final outcome of an autonomous shipment recovery coordination run."""

    run_id: str = Field(
        ...,
        description="Recovery run ID",
    )
    shipment_id: str = Field(
        ...,
        description="Distressed shipment ID",
    )
    terminal_state: str = Field(
        ...,
        description="Terminal FSM state: RECOMMENDED_FOR_CONFIRMATION | NO_SAFE_MATCH | ERROR_RECOVERABLE",
    )
    selected_carrier_id: Optional[str] = Field(
        default=None,
        description="Carrier awarded the recommendation, if any",
    )
    selected_offer: Optional[CarrierOffer] = Field(
        default=None,
        description="Winning candidate's offer details",
    )
    audit_verdict: Optional[AuditVerdict] = Field(
        default=None,
        description="Telemetry audit verdict for the recommended carrier",
    )
    rejected_candidates: List[str] = Field(
        default_factory=list,
        description="List of candidate IDs rejected during this run",
    )
    rejection_reasons: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of candidate ID to exact rejection reason",
    )
    audit_trail: List[AuditEvent] = Field(
        default_factory=list,
        description="Chronological audit events recorded during the run",
    )
    is_simulated: bool = Field(
        default=True,
        description="Explicit disclosure that this result is a simulation recommendation",
    )
    disclaimer: str = Field(
        default="SIMULATION — Sandbox recommendation awaiting human operator confirmation. No physical dispatch or transaction executed.",
        description="Mandatory MVP disclosure",
    )
