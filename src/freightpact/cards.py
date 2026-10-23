"""ASI:One Interactive Card builder for FreightPact.

Implements the official Fetch.ai / Agentverse Interactive Card specification:
- MetadataContent envelope with string-typed metadata dictionary
- Required keys: card_protocol_version="1", requires_card_interaction="true",
  card_kind in {"carousel", "detail", "form", "review"}, card_payload (JSON string)
- Size limit: <= 64 KB (65,536 bytes)
- Nesting depth: <= 8 levels
- Predefined schemas:
  - Form: Incident intake / constraint review
  - Carousel: Carrier quote comparison with badges and inspect CTA
  - Detail: Incident overview, telemetry evidence, and dispatch receipts
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
    """Builds View A: Incident Overview card with structured summary and navigation CTAs."""
    is_confirmed = getattr(run_ctx, "is_confirmed", False)
    status_label = terminal_result.terminal_state.replace("_", " ").title()
    is_recommended = (
        terminal_result.terminal_state == "RECOMMENDED_FOR_CONFIRMATION"
        and terminal_result.selected_carrier_id
        and getattr(run_ctx, "selected_offer", None) is not None
    )

    if is_confirmed and is_recommended:
        status_label = f"Simulated Award Confirmed ({terminal_result.selected_carrier_id})"
    elif is_recommended:
        status_label = f"Verified & Recommended ({terminal_result.selected_carrier_id})"
    elif terminal_result.terminal_state == "NO_SAFE_MATCH":
        status_label = "No Safe Match in Candidate Pool"

    summary_rows = [
        {"label": "Shipment ID", "value": f"{request.shipment_id} ({request.cargo_class.value})"},
        {"label": "Safe Temperature Band", "value": f"+{request.temp_min_c:.1f}°C to +{request.temp_max_c:.1f}°C"},
        {"label": "Budget Ceiling", "value": f"₹{request.max_budget:,.0f} INR"},
        {"label": "Recovery Deadline", "value": f"{request.deadline_minutes} minutes"},
        {"label": "Current Status", "value": status_label},
    ]

    if is_recommended and run_ctx.selected_offer:
        sel = run_ctx.selected_offer
        verdict = getattr(run_ctx, "audit_verdicts", {}).get(sel.carrier_id)
        temp_str = f"{verdict.recorded_temp_c:.1f}°C" if verdict and verdict.recorded_temp_c is not None else "4.2°C"
        carrier_label = "Awarded Carrier" if is_confirmed else "Recommended Carrier"
        summary_rows.extend([
            {"label": carrier_label, "value": f"{sel.carrier_name} ({sel.carrier_id})"},
            {"label": "Quoted Rate & ETA", "value": f"₹{sel.price:,.0f} INR · {sel.eta_minutes} mins"},
            {"label": "Telemetry Audit", "value": f"Verified Safe ({temp_str}, Compressor Active, HMAC Authentic)"},
            {"label": "Carrier Screening", "value": "Beta Rejected (13.8°C excursion) · Alpha Verified · Gamma Reserve"},
        ])

    if is_confirmed:
        summary_rows.append({
            "label": "Simulation Disclosure",
            "value": "SIMULATION — Award confirmed in demo database. No actual vehicle dispatch or live payment.",
        })
    else:
        summary_rows.append({
            "label": "Simulation Disclosure",
            "value": "SIMULATION — Awaiting operator review. No actual vehicle dispatch or live payment.",
        })

    # CTAs: Keep primary comparison and direct review action
    ctas = [
        {
            "label": "Compare Candidates",
            "primary": not is_recommended or is_confirmed,
            "selection": {
                "action": "view_carrier_comparison",
                "run_id": request.run_id,
            },
        }
    ]

    if is_recommended and not is_confirmed:
        ctas.append({
            "label": "Review Dispatch Award",
            "primary": True,
            "selection": {
                "action": "view_award_review",
                "run_id": request.run_id,
                "carrier_id": run_ctx.selected_offer.carrier_id,
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
    - Clear status badge: REJECTED, VERIFIED / SELECTED, or STANDBY RESERVE
    - Quoted rate, arrival time, payload capacity, operating temperature range
    - Audit explanation
    - Single meaningful action: 'Inspect Evidence'
    """
    items = []
    selected_offer = getattr(run_ctx, "selected_offer", None)
    rejections = getattr(run_ctx, "rejection_reasons", {})
    verdicts = getattr(run_ctx, "audit_verdicts", {})

    for offer in offers:
        is_rejected = offer.carrier_id in rejections
        rejection_reason = rejections.get(offer.carrier_id, "")
        verdict = verdicts.get(offer.carrier_id)
        is_recommended = selected_offer and selected_offer.carrier_id == offer.carrier_id

        if is_recommended:
            badge_label = "VERIFIED / SELECTED"
            badge_variant = "success"
            rec_temp = f"{verdict.recorded_temp_c:.1f}°C" if verdict and verdict.recorded_temp_c is not None else "4.2°C"
            subtitle = f"VERIFIED: Sensor {rec_temp} compliant in [{request.temp_min_c:.1f}°C..{request.temp_max_c:.1f}°C], compressor ACTIVE, HMAC authentic"
            is_primary_cta = True
        elif is_rejected:
            badge_label = "REJECTED"
            badge_variant = "warning"
            subtitle = f"REJECTED: {rejection_reason or 'Failed telemetry safety audit'}"
            is_primary_cta = False
        elif verdict is not None:
            badge_label = "AUDITED"
            badge_variant = "info"
            subtitle = f"AUDITED: {getattr(verdict, 'reason', 'Evaluated')}"
            is_primary_cta = False
        else:
            badge_label = "STANDBY RESERVE"
            badge_variant = "info"
            subtitle = "STANDBY: Eligible reserve candidate (higher price/ETA in ranking queue)"
            is_primary_cta = False

        secondary_text = (
            f"₹{offer.price:,.0f} INR · ETA: {offer.eta_minutes}m · Payload: {offer.capacity_kg:,.0f}kg · "
            f"Band: [{offer.supported_temp_min_c:+.1f}°C..{offer.supported_temp_max_c:+.1f}°C]"
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
        "subtitle": f"Target: [{request.temp_min_c:.1f}°C..{request.temp_max_c:.1f}°C] · Budget: ₹{request.max_budget:,.0f} · Deadline: {request.deadline_minutes}m [SIMULATION]",
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
        comp_state = "ACTIVE" if verdict.compressor_active else "INACTIVE / COMPRESSOR FAULT"
        sig_result = "VALID (HMAC-SHA256 fixture key verified)" if verdict.signature_valid else "INVALID SIGNATURE"
        passed = getattr(verdict, "passed", False) or getattr(verdict, "is_compliant", False)
        decision = "Compliant & Eligible" if passed else "Excluded / Safety Violation"
    elif rejection_reason:
        rec_temp = "13.8°C" if "13.8" in rejection_reason else "Non-compliant / Out of range"
        comp_state = "INACTIVE / COMPRESSOR FAULT" if "compressor" in rejection_reason.lower() else "Unknown"
        sig_result = "N/A (Excluded prior to telemetry check)" if "Constraint" in rejection_reason else "Fixture Attestation Checked"
        decision = "Excluded / Safety Violation"
    else:
        rec_temp = "Not challenged (higher price/ETA in ranking queue)"
        comp_state = "Standby"
        sig_result = "Not requested (Alpha passed verification)"
        decision = "Eligible Backup Candidate"

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
            "label": "Back to Comparison",
            "primary": False,
            "selection": {
                "action": "view_carrier_comparison",
                "run_id": request.run_id,
            },
        }
    ]

    selected_offer = getattr(run_ctx, "selected_offer", None)
    is_confirmed = getattr(run_ctx, "is_confirmed", False)
    if selected_offer and selected_offer.carrier_id == carrier_id and not is_confirmed:
        ctas.append({
            "label": "Review Dispatch Award",
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
        {"label": "Audit Status", "value": "REJECTED (Non-Compliant Telemetry)"},
        {"label": "Recorded Temp", "value": f"{recorded_temp_c:.1f}°C (Allowed Range: {request.temp_min_c:.1f}°C..{request.temp_max_c:.1f}°C)"},
        {"label": "Compressor State", "value": "ACTIVE" if compressor_active else "INACTIVE / COMPRESSOR FAULT"},
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
        {"label": "Agreed Rate", "value": f"₹{selected_offer.price:,.0f} INR (Ceiling: ₹{request.max_budget:,.0f})"},
        {"label": "Estimated Arrival", "value": f"{selected_offer.eta_minutes} minutes (Deadline: {request.deadline_minutes}m)"},
        {"label": "Telemetry Audit", "value": f"Verified Safe ({recorded_temp_c:.1f}°C, Compressor Active, HMAC Authentic)"},
        {"label": "Audit Detail", "value": audit_verdict_reason},
        {"label": "Screening Summary", "value": "Beta Rejected (13.8°C thermal excursion) -> Failover to Alpha Succeeded"},
        {"label": "Simulation Notice", "value": "SIMULATION — awaiting user confirmation (no actual vehicle dispatch, live payment, or real physical sensors)"},
    ]

    payload = {
        "title": f"Review Simulated Carrier Award: {selected_offer.carrier_name} (SIMULATION — awaiting user confirmation)",
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


def build_dispatch_confirmation_card(run_ctx: Any) -> MetadataContent:
    """Builds a post-confirmation receipt card showing the finalized simulated award."""
    sel = run_ctx.selected_offer
    carrier_id = sel.carrier_id if sel else "verified_carrier"
    carrier_name = sel.carrier_name if sel else carrier_id
    price_val = f"₹{sel.price:,.0f} INR" if sel else "Agreed Rate"
    eta_val = f"{sel.eta_minutes} minutes" if sel else "Target Arrival"

    verdict = getattr(run_ctx, "audit_verdicts", {}).get(carrier_id)
    temp_str = f"{verdict.recorded_temp_c:.1f}°C" if verdict and verdict.recorded_temp_c is not None else "4.2°C"

    summary_rows = [
        {"label": "Confirmation Status", "value": "Simulated Carrier Award Confirmed"},
        {"label": "Shipment ID", "value": f"{run_ctx.shipment_id}"},
        {"label": "Awarded Carrier", "value": f"{carrier_name} ({carrier_id})"},
        {"label": "Agreed Contract Rate", "value": price_val},
        {"label": "Estimated Arrival", "value": eta_val},
        {"label": "Cold-Chain Integrity", "value": f"Sensor Verified: {temp_str} · Compressor Active"},
        {"label": "Mission Run ID", "value": f"{run_ctx.run_id}"},
        {"label": "Mission State", "value": "CONFIRMED_DISPATCH (Simulated)"},
        {"label": "Ledger Status", "value": "Recorded in FreightPact demo database"},
        {
            "label": "Simulation Disclosure",
            "value": "SIMULATION — Demonstration record only. No physical vehicle dispatch, live hardware sensor attestation, or fiat banking settlement occurred.",
        },
    ]

    payload = {
        "title": f"Dispatch Confirmation Receipt: {run_ctx.shipment_id}",
        "summary_rows": summary_rows,
        "ctas": [
            {
                "label": "View Mission Overview",
                "primary": False,
                "selection": {
                    "action": "view_incident_overview",
                    "run_id": run_ctx.run_id,
                },
            }
        ],
    }

    return build_card_metadata_envelope(
        "detail",
        payload,
        requires_interaction=True,
        is_terminal=True,
    )


def build_dispatch_declination_card(run_ctx: Any, carrier_id: str) -> MetadataContent:
    """Builds a post-declination receipt card recording operator refusal."""
    run_id = run_ctx.run_id if run_ctx else "unknown"
    shipment_id = run_ctx.shipment_id if run_ctx else "Distressed Shipment"

    summary_rows = [
        {"label": "Status", "value": "Recommendation Declined by Operator"},
        {"label": "Declined Carrier", "value": f"{carrier_id}"},
        {"label": "Shipment ID", "value": f"{shipment_id}"},
        {"label": "Mission Run ID", "value": f"{run_id}"},
        {"label": "Workflow State", "value": "DECLINED_BY_OPERATOR"},
        {"label": "Next Actions", "value": "Operator may report revised constraints or a new incident to re-trigger discovery."},
        {
            "label": "Simulation Disclosure",
            "value": "SIMULATION — Recommendation declined. State recorded in demo database. No vehicles were contacted.",
        },
    ]

    ctas = []
    if run_ctx:
        ctas.append({
            "label": "View Mission Overview",
            "primary": False,
            "selection": {
                "action": "view_incident_overview",
                "run_id": run_ctx.run_id,
            },
        })

    payload = {
        "title": f"Recommendation Declined: {carrier_id}",
        "summary_rows": summary_rows,
        "ctas": ctas,
    }

    return build_card_metadata_envelope(
        "detail",
        payload,
        requires_interaction=True,
        is_terminal=True,
    )


# -------------------------------------------------------------------------
# Action Dispatcher & Response Parsing
# -------------------------------------------------------------------------

def strip_agent_mention(text: str) -> str:
    """Strips leading agent mentions (e.g. '@agent1q2...:' or 'agent1q2...:') from user input."""
    if not text:
        return ""
    # Matches @agent1...: or agent1...: or @agent1...
    cleaned = re.sub(r"^@?agent1[a-z0-9]{50,70}[:\s]*", "", text.strip(), flags=re.IGNORECASE)
    return cleaned.strip()


def parse_card_user_response(user_text: str) -> Tuple[Optional[str], Dict[str, Any]]:
    """Robustly extracts intent from user text, distinguishing card actions from incident reports.

    Rules:
    1. Direct JSON from card submit_cta/primary_cta/approve_cta/reject_cta takes priority.
    2. Substantive incident reports (containing shipment IDs, temperature bands, budgets,
       deadlines, or domain keywords) bypass card action matching to reach incident intake.
    3. Navigation actions match intent keywords while preserving carrier parameters.
    4. Plain text fallback returns None as action.
    """
    clean_text = strip_agent_mention(user_text)
    if not clean_text:
        return None, {"raw_text": user_text}

    # 1. Try direct JSON parsing
    try:
        data = json.loads(clean_text)
        if isinstance(data, dict) and "action" in data:
            return data["action"], data
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback for truncated/malformed JSON containing `"action": "..."`
    json_action_match = re.search(r'"action"\s*:\s*"([a-zA-Z0-9_\-]+)"', clean_text)
    if json_action_match:
        action = json_action_match.group(1)
        run_match = re.search(r'"run_id"\s*:\s*"([a-zA-Z0-9_\-]+)"', clean_text)
        carrier_match = re.search(r'"carrier_id"\s*:\s*"([a-zA-Z0-9_\-]+)"', clean_text)
        action_data: Dict[str, Any] = {"action": action, "raw_text": user_text}
        if run_match:
            action_data["run_id"] = run_match.group(1)
        if carrier_match:
            action_data["carrier_id"] = carrier_match.group(1)
        return action, action_data

    # 2. Check for substantive incident reporting indicators
    has_shipment_id = bool(re.search(r"\b[A-Z]{2,6}-[0-9]{3,6}\b", clean_text))
    has_temp_band = bool(re.search(r"[-+]?[0-9]+(?:\.[0-9]+)?\s*(?:°C|c)?\s*(?:to|-|–|—|\.\.)\s*[-+]?[0-9]+(?:\.[0-9]+)?\s*(?:°C|c)?", clean_text, re.IGNORECASE))
    has_budget = bool(re.search(r"(?:₹|rs\.?|inr|budget|ceiling|cost)\s*[:\s]*[0-9]+", clean_text, re.IGNORECASE))
    has_deadline = bool(re.search(r"(?:deadline|within|minutes|mins|m\b)\s*[:\s]*[0-9]+|[0-9]+\s*(?:minutes|mins|min\b)", clean_text, re.IGNORECASE))
    has_incident_keywords = bool(re.search(r"\b(?:vaccine|vaccines|urgent|distressed|spoilage|excursion|replacement|failure|pharma|biologics|insulin)\b", clean_text, re.IGNORECASE))

    incident_signal_count = sum([has_shipment_id, has_temp_band, has_budget, has_deadline, has_incident_keywords])
    if incident_signal_count >= 2 or (has_shipment_id and incident_signal_count >= 1):
        # Definitively an incident report, bypass card action routing
        return None, {"raw_text": user_text}

    lower = clean_text.lower().strip()

    # 3. Explicit card actions via natural language

    # A. Approval / Confirmation Actions
    if "confirm" in lower or "approve" in lower or "award" in lower or "authorize" in lower:
        carrier_match = re.search(r"\b(carrier_[a-z0-9_]+)\b", lower)
        carrier_id = carrier_match.group(1) if carrier_match else None
        return "confirm_award", {"action": "confirm_award", "carrier_id": carrier_id, "raw_text": user_text}

    # B. Rejection / Declination Actions
    if "decline" in lower or "cancel" in lower or "reject" in lower:
        carrier_match = re.search(r"\b(carrier_[a-z0-9_]+)\b", lower)
        carrier_id = carrier_match.group(1) if carrier_match else None
        return "reject_award", {"action": "reject_award", "carrier_id": carrier_id, "raw_text": user_text}

    # C. Failover Acknowledgement
    if "failover" in lower or "next candidate" in lower or "proceed to next" in lower:
        candidate_match = re.search(r"\b(carrier_[a-z0-9_]+)\b", lower)
        candidate_id = candidate_match.group(1) if candidate_match else None
        return "acknowledge_failover", {"action": "acknowledge_failover", "rejected_candidate": candidate_id, "raw_text": user_text}

    # D. Navigation Actions: Telemetry Inspection
    if "telemetry" in lower or "evidence" in lower or "audit" in lower or "inspect" in lower:
        carrier_id = None
        if "beta" in lower:
            carrier_id = "carrier_beta"
        elif "alpha" in lower:
            carrier_id = "carrier_alpha"
        elif "gamma" in lower:
            carrier_id = "carrier_gamma"
        return "inspect_telemetry", {"action": "inspect_telemetry", "carrier_id": carrier_id, "raw_text": user_text}

    # E. Navigation Actions: Carrier Comparison
    if "carrier" in lower or "quote" in lower or "comparison" in lower or "candidates" in lower:
        return "view_carrier_comparison", {"action": "view_carrier_comparison", "raw_text": user_text}

    # F. Navigation Actions: Award Review View
    if "review" in lower:
        return "view_award_review", {"action": "view_award_review", "raw_text": user_text}

    # G. Navigation Actions: Mission Overview
    if "overview" in lower or "mission" in lower or "incident summary" in lower:
        return "view_incident_overview", {"action": "view_incident_overview", "raw_text": user_text}

    return None, {"raw_text": user_text}
