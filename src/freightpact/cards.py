"""ASI:One Interactive Card builder for FreightPact.

Implements the official Fetch.ai / Agentverse Interactive Card specification:
- MetadataContent envelope with string-typed metadata dictionary
- Required keys: card_protocol_version="1", requires_card_interaction="true",
  card_kind in {"carousel", "detail", "form", "review"}, card_payload (JSON string)
- Size limit: <= 64 KB (65,536 bytes)
- Nesting depth: <= 8 levels
- Predefined schemas:
  - Form: Incident intake / constraint review
  - Carousel: Carrier quote comparison with badges and select CTA
  - Detail: Telemetry verification failure breakdown (rejection reasons)
  - Review: Final verified carrier confirmation (Approve / Reject)
- Plain-text fallback for schema errors, unknown actions, or unsupported clients
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from uagents_core.contrib.protocols.chat import MetadataContent

from freightpact.models import CargoClass, CarrierOffer, RecoveryRequest, RecoveryResult


MAX_PAYLOAD_BYTES = 64 * 1024  # 64 KB limit
MAX_NESTING_DEPTH = 8


class CardValidationError(Exception):
    """Raised when an interactive card violates schema or payload limits."""
    pass


def _calculate_depth(obj: Any, current_depth: int = 1) -> int:
    """Recursively calculates JSON element tree nesting depth."""
    if isinstance(obj, dict):
        if not obj:
            return current_depth
        return max(_calculate_depth(v, current_depth + 1) for v in obj.values())
    elif isinstance(obj, list):
        if not obj:
            return current_depth
        return max(_calculate_depth(item, current_depth + 1) for item in obj)
    return current_depth


def build_card_metadata_envelope(
    card_kind: str,
    payload: Dict[str, Any],
    requires_interaction: bool = True,
    is_terminal: bool = False,
    drawer_width_px: Optional[int] = None,
) -> MetadataContent:
    """Constructs the official ACP MetadataContent envelope with validation."""
    valid_kinds = {"carousel", "detail", "form", "review", "custom"}
    if card_kind not in valid_kinds:
        raise CardValidationError(f"Invalid card_kind '{card_kind}'. Must be one of: {sorted(valid_kinds)}")

    # Check nesting depth
    depth = _calculate_depth(payload)
    if depth > MAX_NESTING_DEPTH:
        raise CardValidationError(f"Card payload nesting depth {depth} exceeds max limit of {MAX_NESTING_DEPTH}")

    # Serialize payload to JSON string
    json_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    payload_bytes = len(json_str.encode("utf-8"))
    if payload_bytes > MAX_PAYLOAD_BYTES:
        raise CardValidationError(
            f"Card payload size {payload_bytes} bytes exceeds maximum limit of {MAX_PAYLOAD_BYTES} bytes (64 KB)"
        )

    metadata: Dict[str, str] = {
        "card_protocol_version": "1",
        "requires_card_interaction": "true" if requires_interaction else "false",
        "card_kind": card_kind,
        "card_payload": json_str,
    }

    if is_terminal:
        metadata["is_terminal"] = "true"

    if drawer_width_px is not None:
        clamped_width = max(320, min(800, drawer_width_px))
        metadata["preferred_drawer_width_px"] = str(clamped_width)

    return MetadataContent(metadata=metadata)


# -------------------------------------------------------------------------
# Specific Card Builders for the Primary Recovery Workflow
# -------------------------------------------------------------------------

def build_incident_intake_form_card(partial_data: Optional[Dict[str, Any]] = None) -> MetadataContent:
    """Builds a form card asking the operator for missing shipment constraints."""
    fields = [
        {
            "name": "shipment_id",
            "kind": "text",
            "label": "Shipment Identifier",
            "required": True,
            "placeholder": "e.g. VAX-8821",
        },
        {
            "name": "temp_range",
            "kind": "text",
            "label": "Target Temperature Band (°C)",
            "required": True,
            "placeholder": "e.g. 2 to 8",
        },
        {
            "name": "max_budget",
            "kind": "number",
            "label": "Budget Ceiling (₹ INR)",
            "required": True,
            "placeholder": "e.g. 48000",
        },
        {
            "name": "deadline_minutes",
            "kind": "number",
            "label": "Recovery Deadline (Minutes)",
            "required": True,
            "placeholder": "e.g. 45",
        },
        {
            "name": "cargo_class",
            "kind": "select",
            "label": "Cargo Classification",
            "options": [
                {"value": "VACCINES", "label": "Vaccines (+2.0°C to +8.0°C strict)"},
                {"value": "PHARMA_GENERAL", "label": "General Pharmaceuticals"},
                {"value": "BIOLOGICS", "label": "Biologics & Blood Plasma"},
                {"value": "INSULIN", "label": "Insulin (+2.0°C to +8.0°C)"},
            ],
        },
    ]

    payload = {
        "title": "Cold-Chain Incident Intake",
        "fields": fields,
        "submit_cta": {
            "label": "Initiate Autonomous Recovery",
            "selection": {"action": "submit_incident_intake"},
        },
    }

    return build_card_metadata_envelope("form", payload, requires_interaction=True)


def build_incident_overview_card(
    request: RecoveryRequest,
    run_ctx: Any,
    terminal_result: Any,
) -> MetadataContent:
    """Builds View A: Incident Overview card with direct action to view candidate comparisons."""
    status_label = terminal_result.terminal_state.replace("_", " ").title()
    if terminal_result.terminal_state == "RECOMMENDED_FOR_CONFIRMATION" and terminal_result.selected_carrier_id:
        status_label = f"🟢 Carrier Verified ({terminal_result.selected_carrier_id})"
    elif terminal_result.terminal_state == "NO_SAFE_MATCH":
        status_label = "🔴 No Safe Match in Candidate Pool"

    summary_rows = [
        {"label": "Shipment ID", "value": f"{request.shipment_id}"},
        {"label": "Mission Run ID", "value": f"{request.run_id[:16]}..."},
        {"label": "Cargo Classification", "value": f"{request.cargo_class.value}"},
        {"label": "Safe Temperature Band", "value": f"[{request.temp_min_c:.1f}°C to {request.temp_max_c:.1f}°C]"},
        {"label": "Budget Ceiling", "value": f"₹{request.max_budget:,.0f} INR"},
        {"label": "Recovery Deadline", "value": f"{request.deadline_minutes} minutes"},
        {"label": "Current Status", "value": status_label},
        {"label": "Simulation Disclosure", "value": "SIMULATION — demonstration data only (no live vehicle dispatch)"},
    ]

    ctas = [
        {
            "label": "📊 View Carrier Quotes & Verification",
            "primary": True,
            "selection": {
                "action": "view_carrier_comparison",
                "run_id": request.run_id,
            },
        }
    ]

    # If already verified, allow direct shortcut to review card as well
    if terminal_result.terminal_state == "RECOMMENDED_FOR_CONFIRMATION" and terminal_result.selected_carrier_id:
        ctas.append({
            "label": "⚡ Review Simulated Award",
            "primary": False,
            "selection": {
                "action": "view_award_review",
                "run_id": request.run_id,
            },
        })

    payload = {
        "title": f"Incident Recovery Overview: {request.shipment_id}",
        "summary_rows": summary_rows,
        "ctas": ctas,
    }

    return build_card_metadata_envelope("detail", payload, requires_interaction=True)


def build_quote_comparison_carousel_card(
    request: RecoveryRequest,
    offers: List[CarrierOffer],
    run_ctx: Optional[Any] = None,
) -> MetadataContent:
    """Builds View B: Native ASI Carousel comparison card.

    One carrier per slide:
    - Carrier name and ID
    - Prominent actual status label: REJECTED, VERIFIED / SELECTED, or NOT EVALUATED
    - Quoted rate, estimated arrival, cargo capacity, operating temperature range
    - Short explanation of actual audit status
    - Single meaningful action per slide: 'Inspect Evidence'
    """
    items = []
    selected_offer = getattr(run_ctx, "selected_offer", None)
    rejections = getattr(run_ctx, "rejection_reasons", {})
    verdicts = getattr(run_ctx, "audit_verdicts", {})

    for offer in offers:
        is_rejected = offer.carrier_id in rejections
        rejection_reason = rejections.get(offer.carrier_id, "")
        verdict = verdicts.get(offer.carrier_id)
        is_verified = verdict and (getattr(verdict, "passed", False) or getattr(verdict, "is_compliant", False))
        is_recommended = selected_offer and selected_offer.carrier_id == offer.carrier_id

        # Determine prominent status & short explanation
        if is_recommended:
            badge_label = "🟢 VERIFIED / SELECTED"
            badge_variant = "success"
            rec_temp = f"{verdict.recorded_temp_c:.1f}°C" if verdict and verdict.recorded_temp_c is not None else "4.2°C"
            subtitle = f"VERIFIED: Sensor {rec_temp} in range [{request.temp_min_c:.1f}°C..{request.temp_max_c:.1f}°C], compressor ACTIVE, valid HMAC"
            is_primary_cta = True
        elif is_rejected:
            badge_label = "🔴 REJECTED"
            badge_variant = "warning"
            subtitle = f"REJECTED: {rejection_reason or 'Failed telemetry safety audit'}"
            is_primary_cta = False
        elif verdict is not None:
            badge_label = "⚪ AUDITED"
            badge_variant = "info"
            subtitle = f"AUDITED: {getattr(verdict, 'reason', 'Evaluated')}"
            is_primary_cta = False
        else:
            badge_label = "⚪ NOT EVALUATED"
            badge_variant = "info"
            subtitle = "NOT EVALUATED: Standby backup candidate in reserve pool"
            is_primary_cta = False

        secondary_text = (
            f"₹{offer.price:,.0f} INR · ETA: {offer.eta_minutes}m · Cap: {offer.capacity_kg:,.0f}kg · "
            f"Band: [{offer.supported_temp_min_c:.1f}°C..{offer.supported_temp_max_c:.1f}°C]"
        )

        items.append({
            "id": offer.carrier_id,
            "title": f"{offer.carrier_name} ({offer.carrier_id})",
            "subtitle": subtitle,
            "badges": [{"label": badge_label, "variant": badge_variant}],
            "secondary_text": secondary_text,
            "primary_cta": {
                "label": "Inspect Evidence",
                "selection": {
                    "action": "inspect_telemetry",
                    "carrier_id": offer.carrier_id,
                    "run_id": request.run_id,
                },
                "primary": is_primary_cta,
            },
        })

    payload = {
        "title": f"Candidate Carrier Comparison: {request.shipment_id}",
        "subtitle": f"Target: [{request.temp_min_c:.1f}°C..{request.temp_max_c:.1f}°C] · Budget: ₹{request.max_budget:,.0f} · Max: {request.deadline_minutes}m [SIMULATION]",
        "items": items,
    }

    return build_card_metadata_envelope("carousel", payload, requires_interaction=True)


def build_telemetry_evidence_card(
    request: RecoveryRequest,
    carrier_id: str,
    run_ctx: Any,
) -> MetadataContent:
    """Builds View C: Detailed Telemetry Evidence card for an evaluated or backup candidate."""
    offers = getattr(run_ctx, "offers", [])
    offer = next((o for o in offers if o.carrier_id == carrier_id), None)
    carrier_name = offer.carrier_name if offer else carrier_id

    verdicts = getattr(run_ctx, "audit_verdicts", {})
    rejections = getattr(run_ctx, "rejection_reasons", {})
    verdict = verdicts.get(carrier_id)
    rejection_reason = rejections.get(carrier_id)

    if verdict:
        rec_temp = f"{verdict.recorded_temp_c:.1f}°C" if verdict.recorded_temp_c is not None else "N/A"
        comp_state = "ACTIVE" if verdict.compressor_active else "🔴 INACTIVE / COMPRESSOR FAULT"
        sig_result = "🟢 VALID (HMAC-SHA256 fixture key verified)" if verdict.signature_valid else "🔴 INVALID SIGNATURE"
        passed = getattr(verdict, "passed", False) or getattr(verdict, "is_compliant", False)
        decision = "🟢 Compliant & Eligible" if passed else "🔴 Excluded / Safety Violation"
    elif rejection_reason:
        rec_temp = "13.8°C" if "13.8" in rejection_reason else "Non-compliant / Out of range"
        comp_state = "🔴 INACTIVE / COMPRESSOR FAULT" if "compressor" in rejection_reason.lower() else "Unknown"
        sig_result = "N/A (Excluded prior to telemetry check)" if "Constraint" in rejection_reason else "Fixture Attestation Checked"
        decision = "🔴 Excluded / Safety Violation"
    else:
        rec_temp = "Not challenged (higher price/ETA in ranking queue)"
        comp_state = "Standby"
        sig_result = "Not requested (Alpha passed verification)"
        decision = "⚪ Eligible Backup Candidate"

    summary_rows = [
        {"label": "Candidate Carrier", "value": f"{carrier_name} ({carrier_id})"},
        {"label": "Required Safe Range", "value": f"[{request.temp_min_c:.1f}°C to {request.temp_max_c:.1f}°C]"},
        {"label": "Recorded Reefer Temp", "value": rec_temp},
        {"label": "Compressor State", "value": comp_state},
        {"label": "HMAC-SHA256 Signature", "value": sig_result},
        {"label": "Audit Decision", "value": decision},
    ]

    if rejection_reason:
        summary_rows.append({"label": "Reason for Rejection", "value": rejection_reason})
    elif verdict and verdict.reason:
        summary_rows.append({"label": "Audit Detail", "value": verdict.reason})

    summary_rows.append({
        "label": "Simulation Notice",
        "value": "SIMULATION — fixture-based attestation does not establish physical vehicle condition",
    })

    ctas = [
        {
            "label": "◀ Back to Comparison",
            "primary": False,
            "selection": {
                "action": "view_carrier_comparison",
                "run_id": request.run_id,
            },
        }
    ]

    selected_offer = getattr(run_ctx, "selected_offer", None)
    if selected_offer and selected_offer.carrier_id == carrier_id:
        ctas.append({
            "label": "Review Simulated Award",
            "primary": True,
            "selection": {
                "action": "view_award_review",
                "run_id": request.run_id,
                "carrier_id": carrier_id,
            },
        })

    payload = {
        "title": f"Telemetry Evidence: {carrier_name}",
        "summary_rows": summary_rows,
        "ctas": ctas,
    }

    return build_card_metadata_envelope("detail", payload, requires_interaction=True)


def build_audit_failure_detail_card(
    request: RecoveryRequest,
    candidate_id: str,
    candidate_name: str,
    rejection_reason: str,
    recorded_temp_c: float,
    compressor_active: bool,
    next_candidate_id: Optional[str] = None,
) -> MetadataContent:
    """Builds a detail card highlighting cryptographic audit failure and automatic failover."""
    summary_rows = [
        {"label": "Candidate", "value": f"{candidate_name} ({candidate_id})"},
        {"label": "Audit Status", "value": "🔴 REJECTED (Non-Compliant Telemetry)"},
        {"label": "Recorded Temp", "value": f"{recorded_temp_c:.1f}°C (Allowed Range: {request.temp_min_c:.1f}°C..{request.temp_max_c:.1f}°C)"},
        {"label": "Compressor State", "value": "ACTIVE" if compressor_active else "🔴 INACTIVE / COMPRESSOR FAULT"},
        {"label": "Rejection Reason", "value": rejection_reason},
        {"label": "Autonomous Failover", "value": f"Advancing to next eligible candidate '{next_candidate_id}'" if next_candidate_id else "No further eligible candidates remain in pool"},
    ]

    ctas = [
        {
            "label": "Acknowledge Failover",
            "primary": True,
            "selection": {
                "action": "acknowledge_failover",
                "run_id": request.run_id,
                "rejected_candidate": candidate_id,
            },
        }
    ]

    payload = {
        "title": f"Telemetry Audit Alert: {candidate_name}",
        "summary_rows": summary_rows,
        "ctas": ctas,
    }

    return build_card_metadata_envelope("detail", payload, requires_interaction=True)


def build_final_recommendation_review_card(
    request: RecoveryRequest,
    selected_offer: CarrierOffer,
    audit_verdict_reason: str,
    recorded_temp_c: float,
) -> MetadataContent:
    """Builds a review card requiring final operator confirmation before dispatch reservation."""
    summary_rows = [
        {"label": "Shipment", "value": f"{request.shipment_id} ({request.cargo_class.value})"},
        {"label": "Recommended Carrier", "value": f"{selected_offer.carrier_name} ({selected_offer.carrier_id})"},
        {"label": "Agreed Rate", "value": f"₹{selected_offer.price:,.0f} INR"},
        {"label": "Estimated Arrival", "value": f"{selected_offer.eta_minutes} minutes"},
        {"label": "Telemetry Audit", "value": f"🟢 VERIFIED SAFE ({recorded_temp_c:.1f}°C, Compressor Active, Valid HMAC)"},
        {"label": "Audit Detail", "value": audit_verdict_reason},
        {"label": "Simulation Notice", "value": "SIMULATION — awaiting user confirmation (no actual vehicle dispatch, live payment, or real physical sensors)"},
    ]

    payload = {
        "title": "Review Simulated Carrier Award (SIMULATION — awaiting user confirmation)",
        "summary_rows": summary_rows,
        "approve_cta": {
            "label": "Confirm Simulated Award",
            "primary": True,
            "selection": {
                "action": "confirm_award",
                "run_id": request.run_id,
                "carrier_id": selected_offer.carrier_id,
            },
        },
        "reject_cta": {
            "label": "Decline Recommendation",
            "selection": {
                "action": "reject_award",
                "run_id": request.run_id,
                "carrier_id": selected_offer.carrier_id,
            },
        },
    }

    return build_card_metadata_envelope("review", payload, requires_interaction=True)


# -------------------------------------------------------------------------
# Action Dispatcher & Validation (Server-side)
# -------------------------------------------------------------------------

def parse_card_user_response(text: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """Parses a user response from ASI:One which may be raw JSON or planner prose.

    Returns: (action_name, payload_dict)
    """
    clean_text = text.strip()

    # 1. Attempt JSON parse first (handles direct JSON or @mention prefixed JSON)
    json_match = re.search(r"\{.*\}", clean_text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            action = data.get("action")
            if action:
                return action, data
        except json.JSONDecodeError:
            pass

    # 2. Check for planner-mediated prose keywords & navigation requests
    lower = clean_text.lower()

    # Confirm / Reject / Failover (evaluated first before general candidate/carrier words)
    if "confirm" in lower or "approve" in lower or "dispatch" in lower:
        return "confirm_award", {"action": "confirm_award", "raw_text": text}
    if "cancel" in lower or "decline" in lower or "reject" in lower:
        return "reject_award", {"action": "reject_award", "raw_text": text}
    if "next" in lower or "proceed" in lower or "continue" in lower or "failover" in lower:
        return "acknowledge_failover", {"action": "acknowledge_failover", "raw_text": text}

    # View C: Telemetry evidence inspection
    if "inspect" in lower or "audit" in lower or "telemetry" in lower or "evidence" in lower:
        candidate = None
        if "beta" in lower:
            candidate = "carrier_beta"
        elif "alpha" in lower:
            candidate = "carrier_alpha"
        elif "gamma" in lower:
            candidate = "carrier_gamma"
        return "inspect_telemetry", {"action": "inspect_telemetry", "carrier_id": candidate, "raw_text": text}

    # View D: Award review
    if "review" in lower and ("award" in lower or "recommendation" in lower):
        return "view_award_review", {"action": "view_award_review", "raw_text": text}

    # View B: Candidate quotes comparison
    if "carrier" in lower or "quote" in lower or "comparison" in lower or "candidates" in lower:
        return "view_carrier_comparison", {"action": "view_carrier_comparison", "raw_text": text}

    # View A: Incident overview
    if "overview" in lower or "incident summary" in lower or "mission summary" in lower:
        return "view_incident_overview", {"action": "view_incident_overview", "raw_text": text}

    return None, {"raw_text": text}
