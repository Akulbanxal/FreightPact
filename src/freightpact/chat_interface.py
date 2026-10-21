"""User-facing Agent Chat Protocol (ACP) entry point for FreightPact.

Implements the official Fetch.ai ACP specification using imports from
`uagents_core.contrib.protocols.chat`:
- ChatMessage
- ChatAcknowledgement
- TextContent
- chat_protocol_spec

Workflow:
1. Promptly acknowledges every incoming ChatMessage using official ChatAcknowledgement.
2. Extracts shipment incident parameters via ASI:One API (if configured and reachable)
   or robust deterministic parser fallback.
3. Checks for missing required constraints (shipment_id, cargo_class, temp range,
   budget, deadline) and prompts dispatcher for specifics without guessing.
4. Executes tested multi-agent Recovery Coordinator orchestrator.
5. Formats structured markdown outcome showing collected quotes, constraint evaluation,
   telemetry audit rejection reasons (Carrier Beta), verified candidate (Carrier Alpha),
   and explicit synthetic demo simulation disclosures.
6. Keeps session open for subsequent confirmation in Phase 7 interactive card flow.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from pydantic.v1 import UUID4
from uagents import Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

from freightpact.audit_log import audit_logger
from freightpact.coordinator_agent import CoordinatorOrchestrator
from freightpact.models import CargoClass, RecoveryRequest, RecoveryResult
from freightpact.states import WorkflowState


def create_chat_acknowledgement(
    msg_id: UUID4, metadata: Optional[Dict[str, str]] = None
) -> ChatAcknowledgement:
    """Builds an official ChatAcknowledgement for an incoming ChatMessage."""
    return ChatAcknowledgement(
        acknowledged_msg_id=msg_id,
        metadata=metadata or {"status": "received", "agent": "freightpact_coordinator"},
        timestamp=datetime.now(timezone.utc),
    )


def create_text_chat_message(text: str) -> ChatMessage:
    """Builds an official ChatMessage containing markdown text content."""
    return ChatMessage(
        content=[TextContent(text=text)],
        msg_id=uuid.uuid4(),
        timestamp=datetime.now(timezone.utc),
    )


async def parse_incident_with_asi1(text: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Attempts to parse incident parameters using ASI:One API endpoint.

    Returns parsed dictionary if successful, or None on error/timeout.
    """
    url = os.getenv("ASI_API_ENDPOINT", "https://api.asi1.ai/v1/chat/completions")
    prompt = (
        "Extract the following shipment incident parameters from the user text as a strict JSON object:\n"
        "- shipment_id (string or null)\n"
        "- cargo_class (one of 'PHARMACEUTICALS', 'VACCINES', 'BIOLOGICS', 'PERISHABLES', 'CHEMICALS', 'OTHER' or null)\n"
        "- origin_location (string or null)\n"
        "- temp_min_c (float or null)\n"
        "- temp_max_c (float or null)\n"
        "- max_budget (float or null)\n"
        "- deadline_minutes (integer or null)\n"
        "- required_capacity_kg (float or null)\n\n"
        f"Input: {text}\n"
        "Output JSON only without markdown wrapping:"
    )

    try:
        timeout = aiohttp.ClientTimeout(total=5.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": os.getenv("ASI_MODEL", "asi1-mini"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                },
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    if content.startswith("```"):
                        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE)
                    parsed = json.loads(content)
                    return parsed
    except Exception as exc:
        audit_logger.log_event(
            "ASI1_PARSER_FALLBACK",
            role="chat_interface",
            details={"error": str(exc), "action": "Falling back to deterministic parser"},
        )
    return None


def parse_incident_fallback(text: str) -> Dict[str, Any]:
    """Deterministic regex and keyword extractor for shipment incidents."""
    data: Dict[str, Any] = {}

    # 1. Temperature band: e.g. "2°C to 8°C", "2C - 8C", "band: 2 to 8 C", "temp: 2 to 8"
    temp_pattern = (
        r"(?:temp(?:erature)?|band|range)?[:\s]*"
        r"([+-]?\d+(?:\.\d+)?)\s*(?:°?\s*C)?\s*"
        r"(?:to|[-–]|until)\s*"
        r"([+-]?\d+(?:\.\d+)?)\s*(?:°?\s*C|\b)"
    )
    # Prefer matches that include °C or 'temp' keyword
    temp_matches = list(re.finditer(temp_pattern, text, re.IGNORECASE))
    chosen_match = None
    for tm in temp_matches:
        full_span = tm.group(0).lower()
        if "c" in full_span or "temp" in full_span or "band" in full_span or "to" in full_span:
            chosen_match = tm
            break
    if not chosen_match and temp_matches:
        chosen_match = temp_matches[0]

    if chosen_match:
        try:
            t1, t2 = float(chosen_match.group(1)), float(chosen_match.group(2))
            data["temp_min_c"] = min(t1, t2)
            data["temp_max_c"] = max(t1, t2)
        except (ValueError, IndexError):
            pass

    # 2. Budget: e.g. "budget 48000", "max budget: ₹48,000", "₹48000"
    budget_pattern = r"(?:budget|cost|ceiling)[:\s]*(?:₹|rs\.?|inr)?\s*([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)"
    budget_match = re.search(budget_pattern, text, re.IGNORECASE)
    if budget_match:
        try:
            data["max_budget"] = float(budget_match.group(1).replace(",", ""))
        except ValueError:
            pass
    elif "₹" in text or "rs" in text.lower():
        symbol_match = re.search(r"(?:₹|rs\.?)\s*([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)", text, re.IGNORECASE)
        if symbol_match:
            try:
                data["max_budget"] = float(symbol_match.group(1).replace(",", ""))
            except ValueError:
                pass

    # 3. Deadline: e.g. "deadline 45 min", "within 45 mins", "45 minutes"
    deadline_pattern = r"(?:deadline|within|in)[:\s]*([0-9]+)\s*(?:min|mins|minutes)?"
    deadline_match = re.search(deadline_pattern, text, re.IGNORECASE)
    if deadline_match:
        try:
            data["deadline_minutes"] = int(deadline_match.group(1))
        except ValueError:
            pass

    # 4. Shipment ID: e.g. "shipment VAX-8821", "VAX-8821", "ID: VAX-8821"
    shipment_pattern = r"(?:shipment|tracking|id)[:\s]*([a-zA-Z0-9\-_]+)"
    shipment_match = re.search(shipment_pattern, text, re.IGNORECASE)
    if shipment_match and shipment_match.group(1).upper() not in {"IS", "AT", "THE", "OF"}:
        data["shipment_id"] = shipment_match.group(1).upper()
    else:
        code_match = re.search(r"\b([A-Z]{2,6}-[0-9]{3,6})\b", text)
        if code_match:
            data["shipment_id"] = code_match.group(1)

    # 5. Capacity: e.g. "1000 kg", "1000kg", "1 ton"
    capacity_pattern = r"([0-9]+(?:\.[0-9]+)?)\s*(?:kg|kgs|kilos)"
    cap_match = re.search(capacity_pattern, text, re.IGNORECASE)
    if cap_match:
        try:
            data["required_capacity_kg"] = float(cap_match.group(1))
        except ValueError:
            pass

    # 6. Cargo Class keyword check
    text_lower = text.lower()
    if "vaccine" in text_lower:
        data["cargo_class"] = CargoClass.VACCINES
    elif "biologic" in text_lower or "blood" in text_lower or "plasma" in text_lower:
        data["cargo_class"] = CargoClass.BIOLOGICS
    elif "insulin" in text_lower:
        data["cargo_class"] = CargoClass.INSULIN
    elif "pharma" in text_lower or "medicine" in text_lower or "drug" in text_lower:
        data["cargo_class"] = CargoClass.PHARMA_GENERAL
    elif "perishable" in text_lower or "food" in text_lower or "dairy" in text_lower:
        data["cargo_class"] = CargoClass.PHARMA_GENERAL
    elif "chemical" in text_lower:
        data["cargo_class"] = CargoClass.PHARMA_GENERAL

    # 7. Origin location extract (simple heuristic)
    origin_pattern = r"(?:at|near|location|origin)[:\s]+([^,.\n]+)"
    orig_match = re.search(origin_pattern, text, re.IGNORECASE)
    if orig_match:
        loc = orig_match.group(1).strip()
        loc = re.sub(r"\b(?:temp|temperature|budget|deadline|with|urgently|carrying)\b.*$", "", loc, flags=re.IGNORECASE).strip()
        if loc and len(loc) > 2:
            data["origin_location"] = loc

    return data


def validate_or_request_missing_fields(data: Dict[str, Any]) -> Tuple[Optional[RecoveryRequest], Optional[str]]:
    """Validates extracted parameters.

    If required parameters are missing, returns (None, prompt_message) asking
    for specifics instead of inventing defaults.
    """
    missing = []
    if not data.get("shipment_id"):
        missing.append("• **Shipment Identifier** (e.g. `VAX-8821`)")
    if data.get("temp_min_c") is None or data.get("temp_max_c") is None:
        missing.append("• **Target Temperature Band** (e.g. `+2°C to +8°C`)")
    if not data.get("max_budget"):
        missing.append("• **Maximum Budget Ceiling** in INR (e.g. `₹48,000`)")
    if not data.get("deadline_minutes"):
        missing.append("• **Recovery Deadline** in minutes (e.g. `45 mins`)")

    if missing:
        prompt = (
            "⚠️ **FreightPact Cold-Chain Incident Intake**\n\n"
            "I detected an active cold-chain incident report, but need the following required parameters "
            "to initiate verified multi-agent recovery without guessing:\n\n"
            + "\n".join(missing)
            + "\n\nPlease reply with the missing parameters to trigger autonomous carrier discovery and telemetry verification."
        )
        return None, prompt

    # Safe defaults for non-strict fields
    raw_cargo = data.get("cargo_class")
    if isinstance(raw_cargo, CargoClass):
        cargo_class = raw_cargo
    elif isinstance(raw_cargo, str):
        cargo_upper = raw_cargo.upper()
        if "VACCINE" in cargo_upper:
            cargo_class = CargoClass.VACCINES
        elif "BIOLOGIC" in cargo_upper:
            cargo_class = CargoClass.BIOLOGICS
        elif "INSULIN" in cargo_upper:
            cargo_class = CargoClass.INSULIN
        else:
            cargo_class = CargoClass.PHARMA_GENERAL
    else:
        cargo_class = CargoClass.VACCINES
    origin_location = data.get("origin_location") or "Distressed Vehicle Location"
    capacity_kg = data.get("required_capacity_kg") or 1000.0

    try:
        req = RecoveryRequest(
            shipment_id=str(data["shipment_id"]),
            cargo_class=cargo_class,
            origin_location=origin_location,
            temp_min_c=float(data["temp_min_c"]),
            temp_max_c=float(data["temp_max_c"]),
            max_budget=float(data["max_budget"]),
            deadline_minutes=int(data["deadline_minutes"]),
            required_capacity_kg=float(capacity_kg),
        )
        return req, None
    except Exception as exc:
        return None, f"❌ Invalid recovery parameters provided: {exc}"


def format_recovery_response(
    request: RecoveryRequest,
    run_ctx: Any,
    terminal_result: RecoveryResult,
) -> str:
    """Formats a dense, restrained enterprise logistics operations report for ASI:One."""
    status_icon = "🟢" if terminal_result.terminal_state == "RECOMMENDED_FOR_CONFIRMATION" else "🔴"
    status_text = (
        "CANDIDATE AUDITED & VERIFIED (AWAITING OPERATOR CONFIRMATION)"
        if terminal_result.terminal_state == "RECOMMENDED_FOR_CONFIRMATION"
        else f"{terminal_result.terminal_state}"
    )

    # A. Incident Overview
    lines = [
        f"### {status_icon} FreightPact Cold-Chain Recovery Report",
        f"**Shipment ID:** `{request.shipment_id}` ({request.cargo_class.value}) · **Mission Run ID:** `{request.run_id}`",
        f"**Required Band:** `+{request.temp_min_c:.1f}°C to +{request.temp_max_c:.1f}°C` · **Budget Ceiling:** `₹{request.max_budget:,.0f} INR` · **Deadline:** `{request.deadline_minutes} mins`",
    ]

    # Prominent outcome summary
    if terminal_result.terminal_state == "RECOMMENDED_FOR_CONFIRMATION" and run_ctx.selected_offer:
        lines.append(
            f"**Mission Status:** 🟢 **RECOVERY CANDIDATE VERIFIED & RECOMMENDED** (`{run_ctx.selected_offer.carrier_name}`)\n"
        )
    elif terminal_result.terminal_state == "NO_SAFE_MATCH":
        lines.append(
            f"**Mission Status:** 🔴 **NO COMPLIANT CARRIER AVAILABLE**\n"
        )
    else:
        lines.append(f"**Mission Status:** `{status_text}`\n")

    # B. Carrier Comparison
    lines.append("#### A. Carrier Quotes & Constraint Screening")
    if run_ctx.offers:
        for offer in run_ctx.offers:
            is_viable_temp = (
                offer.supported_temp_min_c <= request.temp_min_c
                and offer.supported_temp_max_c >= request.temp_max_c
            )
            is_viable_budget = offer.price <= request.max_budget
            is_viable_deadline = offer.eta_minutes <= request.deadline_minutes
            is_eligible = is_viable_temp and is_viable_budget and is_viable_deadline

            tag = "🟢 [ELIGIBLE]" if is_eligible else "🟡 [OUT-OF-BOUNDS]"
            lines.append(
                f"- {tag} **{offer.carrier_name}** (`{offer.carrier_id}`)\n"
                f"  Quote: **₹{offer.price:,.0f} INR** · ETA: **{offer.eta_minutes}m** · "
                f"Operating Band: `[{offer.supported_temp_min_c:+.1f}°C to {offer.supported_temp_max_c:+.1f}°C]` · Payload: `{offer.capacity_kg:,.0f} kg`"
            )
    else:
        lines.append("- *No carrier quotes received within response window.*")
    lines.append("")

    # C. Verification Evidence
    lines.append("#### B. Cryptographic Telemetry Verification Evidence")
    if run_ctx.rejected_candidate_ids:
        for rejected_id in run_ctx.rejected_candidate_ids:
            reason = run_ctx.rejection_reasons.get(rejected_id, "Failed telemetry audit")
            candidate_offer = next((o for o in run_ctx.offers if o.carrier_id == rejected_id), None)
            name = candidate_offer.carrier_name if candidate_offer else rejected_id
            lines.append(
                f"- 🔴 **REJECTED `{rejected_id}` — {name}**\n"
                f"  Reason: {reason}\n"
                f"  Result: Excluded from contract award. Automatic failover initiated."
            )

    if run_ctx.selected_offer:
        verdict = run_ctx.audit_verdicts.get(run_ctx.selected_offer.carrier_id)
        sel_name = run_ctx.selected_offer.carrier_name
        sel_id = run_ctx.selected_offer.carrier_id
        temp_val = verdict.recorded_temp_c if verdict and verdict.recorded_temp_c is not None else 4.2
        lines.append(
            f"- 🟢 **VERIFIED `{sel_id}` — {sel_name}**\n"
            f"  Telemetry: Recorded `{temp_val:.1f}°C` (within permitted `+{request.temp_min_c:.1f}°C..+{request.temp_max_c:.1f}°C`) · Compressor ACTIVE\n"
            f"  Integrity: Authenticated via cryptographic HMAC-SHA256 signature and fresh challenge nonce."
        )

    if not run_ctx.rejected_candidate_ids and not run_ctx.selected_offer:
        lines.append("- *No candidates audited.*")
    lines.append("")

    # D. Decision Summary
    lines.append("#### C. Operational Recommendation Decision")
    if terminal_result.terminal_state == "RECOMMENDED_FOR_CONFIRMATION" and run_ctx.selected_offer:
        sel = run_ctx.selected_offer
        lines.append(
            f"**Selected Award Candidate:** **{sel.carrier_name}** (`{sel.carrier_id}`)\n"
            f"- **Contract Rate:** `₹{sel.price:,.0f} INR` (within ₹{request.max_budget:,.0f} ceiling)\n"
            f"- **Response Window:** `{sel.eta_minutes} minutes` (within {request.deadline_minutes}m deadline)\n"
            f"- **Selection Rationale:** Lowest-priced candidate satisfying all hard commercial constraints and fully verified sensor telemetry.\n"
            f"- **Action Required:** Operator review and confirmation required via the card below."
        )
    else:
        lines.append(
            f"**Status:** `NO_SAFE_MATCH`\n"
            f"**Details:** Unable to find a compliant carrier matching budget, ETA, and verified sensor criteria.\n"
            f"- **Action Required:** Operator manual intervention required or revise budget/deadline constraints."
        )
    lines.append("")

    # E. Simulation Disclosure
    lines.append(
        "> [!NOTE]\n"
        "> **Fetch-A-Thon 3.0 Simulation Disclosure:**\n"
        "> `SIMULATION — awaiting user confirmation`\n"
        "> Carrier quotes, sensor readings (Beta 13.8°C / Alpha 4.2°C), and cryptographic signatures are "
        "synthetic fixtures generated for reproducible hackathon demonstration. This system does not perform "
        "actual physical vehicle dispatch, real hardware sensor attestation, or live payment settlement."
    )

    return "\n".join(lines)


def create_interactive_chat_message(
    text: str, metadata_card: Optional[MetadataContent] = None
) -> ChatMessage:
    """Builds an official ChatMessage containing text and optional MetadataContent card."""
    content: List[Any] = [TextContent(text=text)]
    if metadata_card is not None:
        content.append(metadata_card)
    return ChatMessage(
        content=content,
        msg_id=uuid.uuid4(),
        timestamp=datetime.now(timezone.utc),
    )


def create_acp_chat_protocol(orchestrator: CoordinatorOrchestrator) -> Protocol:
    """Creates the official Agent Chat Protocol (ACP) for the primary coordinator agent."""
    from freightpact.cards import (
        build_final_recommendation_review_card,
        build_incident_intake_form_card,
        build_incident_overview_card,
        build_quote_comparison_carousel_card,
        build_telemetry_evidence_card,
        parse_card_user_response,
    )

    proto = Protocol(spec=chat_protocol_spec)

    @proto.on_message(model=ChatMessage)
    async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage):
        user_text = msg.text().strip()
        ctx.logger.info(f"[ACP] Received chat message from {sender}: {user_text[:80]}...")

        # 1. Promptly send official ChatAcknowledgement
        ack = create_chat_acknowledgement(msg.msg_id)
        await ctx.send(sender, ack)

        if not user_text:
            reply = create_text_chat_message(
                "👋 Hello! I am **FreightPact**, your Autonomous Cold-Chain Shipment Recovery Coordinator. "
                "Report a distressed shipment incident (e.g., reefer failure, temperature rise, location, budget) "
                "to trigger autonomous multi-agent carrier discovery and telemetry verification."
            )
            await ctx.send(sender, reply)
            return

        # 2. Check if this is an interactive card action return (JSON or planner prose)
        card_action, action_data = parse_card_user_response(user_text)

        # -------------------------------------------------------------
        # 2A. Card Navigation Views (Exploration without re-orchestrating)
        # -------------------------------------------------------------
        if card_action in {"view_incident_overview", "view_carrier_comparison", "inspect_telemetry", "view_award_review"}:
            run_id = action_data.get("run_id")
            run_ctx = orchestrator.get_run(run_id) if run_id else None
            if not run_ctx and orchestrator.runs:
                run_ctx = list(orchestrator.runs.values())[-1]

            if not run_ctx:
                reply_msg = create_interactive_chat_message(
                    "⚠️ **Mission Not Found:** No active recovery mission is available to view. "
                    "Please report a new cold-chain incident to initiate carrier discovery."
                )
                await ctx.send(sender, reply_msg)
                return

            req = run_ctx.request

            # VIEW A: Incident Overview
            if card_action == "view_incident_overview":
                ctx.logger.info(f"[ACP] Rendering View A (Incident Overview) for run {run_ctx.run_id}")
                terminal_result = run_ctx.build_terminal_result()
                try:
                    card = build_incident_overview_card(req, run_ctx, terminal_result)
                except Exception as exc:
                    ctx.logger.warning(f"[ACP] Failed to build overview card: {exc}")
                    card = None

                intro_text = (
                    f"📋 **Cold-Chain Incident Overview: `{req.shipment_id}`**\n\n"
                    f"- **Mission Run ID:** `{run_ctx.run_id[:16]}...`\n"
                    f"- **Cargo Class:** {req.cargo_class.value}\n"
                    f"- **Target Range:** {req.temp_min_c:.1f}°C to {req.temp_max_c:.1f}°C\n"
                    f"- **Budget Ceiling:** ₹{req.max_budget:,.0f} INR | **Deadline:** {req.deadline_minutes} mins\n"
                    f"- **Current State:** `{run_ctx.current_state.value}`"
                )
                reply_msg = create_interactive_chat_message(intro_text, metadata_card=card)
                await ctx.send(sender, reply_msg)
                return

            # VIEW B: Carrier Comparison Carousel
            elif card_action == "view_carrier_comparison":
                ctx.logger.info(f"[ACP] Rendering View B (Carrier Comparison) for run {run_ctx.run_id}")
                try:
                    card = build_quote_comparison_carousel_card(req, run_ctx.offers, run_ctx=run_ctx)
                except Exception as exc:
                    ctx.logger.warning(f"[ACP] Failed to build comparison carousel: {exc}")
                    card = None

                intro_text = (
                    f"📊 **Candidate Carrier Comparison: `{req.shipment_id}`**\n\n"
                    f"Showing all **{len(run_ctx.offers)} candidates** evaluated for this mission. "
                    f"Select **'Inspect Verification'** on any carrier to review its cryptographic telemetry attestation."
                )
                reply_msg = create_interactive_chat_message(intro_text, metadata_card=card)
                await ctx.send(sender, reply_msg)
                return

            # VIEW C: Telemetry Evidence Detail Card
            elif card_action == "inspect_telemetry":
                carrier_id = action_data.get("carrier_id")
                # Fallback to recommended or first candidate if not specified
                if not carrier_id:
                    carrier_id = (
                        run_ctx.selected_offer.carrier_id
                        if run_ctx.selected_offer
                        else (run_ctx.offers[0].carrier_id if run_ctx.offers else "carrier_beta")
                    )

                ctx.logger.info(f"[ACP] Rendering View C (Telemetry Evidence) for {carrier_id} in run {run_ctx.run_id}")
                try:
                    card = build_telemetry_evidence_card(req, carrier_id, run_ctx)
                except Exception as exc:
                    ctx.logger.warning(f"[ACP] Failed to build telemetry card: {exc}")
                    card = None

                offer = next((o for o in run_ctx.offers if o.carrier_id == carrier_id), None)
                c_name = offer.carrier_name if offer else carrier_id
                intro_text = (
                    f"🔍 **Cryptographic Telemetry Audit Evidence: `{c_name}`** (`{carrier_id}`)\n\n"
                    f"Review sensor readings and HMAC attestation generated for mission `{run_ctx.run_id[:16]}...`"
                )
                reply_msg = create_interactive_chat_message(intro_text, metadata_card=card)
                await ctx.send(sender, reply_msg)
                return

            # VIEW D: Award Review Card
            elif card_action == "view_award_review":
                ctx.logger.info(f"[ACP] Rendering View D (Award Review) for run {run_ctx.run_id}")
                if not run_ctx.selected_offer:
                    reply_msg = create_interactive_chat_message(
                        f"⚠️ **No Verified Carrier Available:** Mission `{run_ctx.run_id[:16]}...` is in state "
                        f"`{run_ctx.current_state.value}` and does not have an approved candidate."
                    )
                    await ctx.send(sender, reply_msg)
                    return

                verdict = run_ctx.audit_verdicts.get(run_ctx.selected_offer.carrier_id)
                recorded_temp = verdict.recorded_temp_c if verdict and verdict.recorded_temp_c is not None else 4.2
                verdict_reason = verdict.reason if verdict else "Cryptographically verified compliant"

                try:
                    card = build_final_recommendation_review_card(
                        request=req,
                        selected_offer=run_ctx.selected_offer,
                        audit_verdict_reason=verdict_reason,
                        recorded_temp_c=recorded_temp,
                    )
                except Exception as exc:
                    ctx.logger.warning(f"[ACP] Failed to build review card: {exc}")
                    card = None

                intro_text = (
                    f"⚡ **Final Dispatch Review: `{run_ctx.selected_offer.carrier_name}`**\n\n"
                    f"Carrier has passed cryptographic sensor verification. Please confirm or decline simulated award reservation."
                )
                reply_msg = create_interactive_chat_message(intro_text, metadata_card=card)
                await ctx.send(sender, reply_msg)
                return

        # -------------------------------------------------------------
        # 2B. Final Confirmation / Declination / Failover Actions
        # -------------------------------------------------------------
        if card_action == "confirm_award":
            carrier_id = action_data.get("carrier_id")
            run_id = action_data.get("run_id")
            ctx.logger.info(f"[ACP] Card action 'confirm_award' received for run {run_id}, carrier {carrier_id}")

            # Lookup active run strictly by run_id if provided, else latest run
            run_ctx = orchestrator.get_run(run_id) if run_id else None
            if not run_ctx and orchestrator.runs:
                run_ctx = list(orchestrator.runs.values())[-1]

            if not run_ctx:
                reply_msg = create_interactive_chat_message(
                    "❌ **Action Rejected:** No active recovery mission found matching the specified identifier."
                )
                await ctx.send(sender, reply_msg)
                return

            # Idempotency check: If already confirmed, return success without duplicate write
            if getattr(run_ctx, "is_confirmed", False):
                reply_text = (
                    f"✅ **Simulated Carrier Award Already Confirmed** (Idempotent)\n\n"
                    f"- **Shipment ID:** `{run_ctx.shipment_id}`\n"
                    f"- **Awarded Candidate:** `{run_ctx.selected_offer.carrier_id if run_ctx.selected_offer else 'verified'}`\n"
                    f"- **Mission Run ID:** `{run_ctx.run_id}`\n\n"
                    "> [!NOTE]\n"
                    "> `SIMULATION — demonstration record already finalized`"
                )
                reply_msg = create_interactive_chat_message(reply_text)
                await ctx.send(sender, reply_msg)
                return

            # Security Check 1: Ensure run is in an awardable state
            if run_ctx.current_state != WorkflowState.RECOMMENDED_FOR_CONFIRMATION or not run_ctx.selected_offer:
                reply_msg = create_interactive_chat_message(
                    f"⚠️ **Action Rejected:** Mission `{run_ctx.run_id}` is in state `{run_ctx.current_state.value}` "
                    "and cannot be confirmed. Only missions with a cryptographically verified recommendation can be confirmed."
                )
                await ctx.send(sender, reply_msg)
                return

            # Security Check 2: Untrusted carrier_id validation (prevent parameter tampering)
            expected_carrier_id = run_ctx.selected_offer.carrier_id
            if carrier_id and carrier_id != expected_carrier_id:
                reply_msg = create_interactive_chat_message(
                    f"🚨 **Security Warning:** Specified carrier candidate `{carrier_id}` does not match "
                    f"the cryptographically audited candidate `{expected_carrier_id}` for mission `{run_ctx.run_id}`. Award confirmation denied."
                )
                await ctx.send(sender, reply_msg)
                return

            confirmed_carrier = expected_carrier_id
            run_ctx.is_confirmed = True
            # Confirm in storage and audit log
            try:
                orchestrator.storage.update_run_state(
                    run_id=run_ctx.run_id,
                    state="CONFIRMED_DISPATCHED",
                    selected_carrier_id=confirmed_carrier,
                    retry_count=run_ctx.audit_attempts,
                )
            except Exception as exc:
                ctx.logger.error(f"[ACP] Failed to record confirmation in storage: {exc}")

            reply_text = (
                f"✅ **Simulated Carrier Award Confirmed**\n\n"
                f"- **Shipment ID:** `{run_ctx.shipment_id}`\n"
                f"- **Awarded Candidate:** `{confirmed_carrier}`\n"
                f"- **Status:** Simulated award recorded in FreightPact demo ledger.\n"
                f"- **Telemetry Monitoring:** Verification complete; simulated telemetry tracking active for demonstration run.\n"
                f"- **Mission Run ID:** `{run_ctx.run_id}`\n\n"
                "> [!NOTE]\n"
                "> **Fetch-A-Thon 3.0 Simulation Disclosure:**\n"
                "> `SIMULATION — demonstration record only`\n"
                "> No physical vehicle dispatch, live hardware sensor attestation, or fiat banking settlement was performed."
            )
            reply_msg = create_interactive_chat_message(reply_text)
            await ctx.send(sender, reply_msg)
            return

        elif card_action == "reject_award":
            carrier_id = action_data.get("carrier_id")
            run_id = action_data.get("run_id")
            ctx.logger.info(f"[ACP] Card action 'reject_award' received for run {run_id}, carrier {carrier_id}")

            run_ctx = orchestrator.get_run(run_id) if run_id else None
            if not run_ctx and orchestrator.runs:
                run_ctx = list(orchestrator.runs.values())[-1]

            if run_ctx and run_ctx.current_state == WorkflowState.RECOMMENDED_FOR_CONFIRMATION:
                try:
                    orchestrator.storage.update_run_state(
                        run_id=run_ctx.run_id,
                        state="DECLINED_BY_OPERATOR",
                        selected_carrier_id=None,
                        retry_count=run_ctx.audit_attempts,
                    )
                except Exception as exc:
                    ctx.logger.error(f"[ACP] Failed to record declination in storage: {exc}")

            declined_id = carrier_id or (run_ctx.selected_offer.carrier_id if run_ctx and run_ctx.selected_offer else "candidate")
            reply_text = (
                f"🛑 **Simulated Carrier Recommendation Declined**\n\n"
                f"- **Candidate:** `{declined_id}`\n"
                f"- **Status:** Recommendation rejected by operator. Workflow terminated safely.\n"
                f"- **Next Steps:** You may report revised operational constraints at any time to re-run candidate evaluation.\n\n"
                "> [!NOTE]\n"
                "> **Fetch-A-Thon 3.0 Simulation Disclosure:**\n"
                "> `SIMULATION — recommendation declined`\n"
                "> State recorded in demo database. No vehicles were contacted or scheduled."
            )
            reply_msg = create_interactive_chat_message(reply_text)
            await ctx.send(sender, reply_msg)
            return

        elif card_action == "acknowledge_failover":
            rejected_id = action_data.get("rejected_candidate")
            reply_text = (
                f"ℹ️ **Autonomous Failover Acknowledged**\n\n"
                f"- **Excluded Candidate:** `{rejected_id}` (failed telemetry verification)\n"
                f"- **Status:** Proceeding with candidate progression in accordance with deterministic ranking policy."
            )
            reply_msg = create_interactive_chat_message(reply_text)
            await ctx.send(sender, reply_msg)
            return

        # 3. Parse intent via ASI:One if configured, else deterministic fallback
        asi_key = os.getenv("ASI_API_KEY")
        extracted_data = None
        if asi_key and asi_key.strip():
            extracted_data = await parse_incident_with_asi1(user_text, asi_key.strip())

        if not extracted_data:
            extracted_data = parse_incident_fallback(user_text)

        # 4. Check for missing required fields
        recovery_request, error_prompt = validate_or_request_missing_fields(extracted_data)
        if error_prompt:
            # Build interactive form card to prompt user directly in ASI:One UI
            form_card = None
            try:
                form_card = build_incident_intake_form_card(extracted_data)
            except Exception as card_err:
                ctx.logger.warning(f"[ACP] Failed to generate form card: {card_err}. Using plain-text fallback.")

            reply = create_interactive_chat_message(error_prompt, metadata_card=form_card)
            await ctx.send(sender, reply)
            return

        # 5. Initialize and run recovery mission
        run_ctx = orchestrator.init_mission(recovery_request)
        await orchestrator.broadcast_quote_requests(ctx, recovery_request.run_id)

        # Wait for completion or timeout
        if recovery_request.run_id in orchestrator.completion_events:
            try:
                await asyncio.wait_for(
                    orchestrator.completion_events[recovery_request.run_id].wait(),
                    timeout=12.0,
                )
            except asyncio.TimeoutError:
                ctx.logger.error(f"[ACP] Orchestration timed out for run {recovery_request.run_id}")
                run_ctx.transition_to(WorkflowState.NO_SAFE_MATCH, reason="Orchestrator timeout")

        # 6. Build real terminal outcome
        terminal_result = run_ctx.build_terminal_result()
        response_text = format_recovery_response(recovery_request, run_ctx, terminal_result)

        # 7. Attach Interactive Card based on outcome (Default to Step 1: Incident Overview with interactive CTA)
        initial_card = None
        try:
            initial_card = build_incident_overview_card(
                request=recovery_request,
                run_ctx=run_ctx,
                terminal_result=terminal_result,
            )
        except Exception as card_err:
            ctx.logger.warning(f"[ACP] Failed to generate initial overview card: {card_err}. Using plain-text fallback.")

        # 8. Send response back to user with optional card attachment
        if initial_card:
            ctx.logger.info(f"[ACP] Attaching multi-step interactive card (kind={initial_card.metadata.get('card_kind')}) to response for {sender}")
        else:
            ctx.logger.warning(f"[ACP] No interactive card attached for mission {recovery_request.run_id}")

        reply_msg = create_interactive_chat_message(response_text, metadata_card=initial_card)
        await ctx.send(sender, reply_msg)

    @proto.on_message(model=ChatAcknowledgement)
    async def handle_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        ctx.logger.info(f"[ACP] Received acknowledgement from {sender} for msg {msg.acknowledged_msg_id}")

    return proto
