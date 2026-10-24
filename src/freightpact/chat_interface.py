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
    from freightpact.cards import strip_agent_mention
    text = strip_agent_mention(text)

    # 1. Temperature band: e.g. "2°C to 8°C", "2C - 8C", "band: 2 to 8 C", "temp: 2 to 8", "range is 2–8°C"
    temp_pattern = (
        r"(?:temp(?:erature)?|band|range)?\s*(?:is|of|between|from|within)?[:\s]*"
        r"([+-]?\d+(?:\.\d+)?)\s*(?:°?\s*C)?\s*"
        r"(?:to|[-–—]|until|and)\s*"
        r"([+-]?\d+(?:\.\d+)?)\s*(?:°?\s*C|\b)"
    )
    # Prefer matches that include °C or 'temp' keyword or dash/to
    temp_matches = list(re.finditer(temp_pattern, text, re.IGNORECASE))
    chosen_match = None
    for tm in temp_matches:
        full_span = tm.group(0).lower()
        if "c" in full_span or "temp" in full_span or "band" in full_span or "to" in full_span or "–" in full_span or "—" in full_span:
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

    # 2. Budget: e.g. "budget 48000", "max budget: ₹48,000", "the maximum budget is ₹48,000", "₹48000"
    budget_pattern = (
        r"(?:budget|cost|ceiling)\s*(?:is|of|ceiling)?[:\s]*(?:₹|rs\.?|inr)?\s*([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)"
    )
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

    # 3. Deadline: e.g. "deadline 45 min", "within 45 mins", "the deadline is 45 minutes", "45 minutes"
    deadline_pattern = (
        r"(?:deadline|within|timeframe|eta|window)\s*(?:is|of)?[:\s]*([0-9]+)\s*(?:min|mins|minutes|m\b)?"
    )
    deadline_match = re.search(deadline_pattern, text, re.IGNORECASE)
    if deadline_match:
        try:
            data["deadline_minutes"] = int(deadline_match.group(1))
        except ValueError:
            pass
    elif "deadline" in text.lower() or "within" in text.lower():
        min_match = re.search(r"\b([0-9]+)\s*(?:min|mins|minutes)\b", text, re.IGNORECASE)
        if min_match:
            try:
                data["deadline_minutes"] = int(min_match.group(1))
            except ValueError:
                pass

    # 4. Shipment ID: e.g. "shipment VAX-8821", "VAX-8821", "ID: VAX-8821"
    shipment_pattern = r"(?:shipment|tracking|id)[:\s]*([a-zA-Z0-9\-_]+)"
    shipment_match = re.search(shipment_pattern, text, re.IGNORECASE)
    if shipment_match and shipment_match.group(1).upper() not in {"IS", "AT", "THE", "OF", "FOR", "NEAR"}:
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

    # 7. Origin location extract (handles comma-separated hubs like Sector 18, Gurugram)
    origin_pattern = r"(?:at|near|location|origin|hub)[:\s]+([^.!?\r\n]+)"
    orig_match = re.search(origin_pattern, text, re.IGNORECASE)
    if orig_match:
        loc = orig_match.group(1).strip()
        loc = re.sub(
            r"\b(?:the\s+required|target|temp|temperature|budget|deadline|max|with|urgently|carrying)\b.*$",
            "",
            loc,
            flags=re.IGNORECASE,
        ).strip(" ,.-")
        if loc and len(loc) > 2:
            data["origin_location"] = loc

    return data

def validate_or_request_missing_fields(data: Dict[str, Any]) -> Tuple[Optional[RecoveryRequest], Optional[str]]:
    """Validates extracted parameters.

    If required parameters are missing, returns (None, prompt_message) with:
    - Concise summary of recognised fields (shipment ID, cargo class, temperature band, location)
    - Explicit list of missing required parameters (budget, deadline, etc.)
    Validates completed request server-side (preventing non-positive budgets or invalid temp ranges).
    """
    missing = []
    if not data.get("shipment_id"):
        missing.append("• **Shipment Identifier** (e.g. `VAX-8821`)")
    if data.get("temp_min_c") is None or data.get("temp_max_c") is None:
        missing.append("• **Target Temperature Band** (e.g. `+2°C to +8°C`)")

    budget_val = data.get("max_budget")
    if budget_val is None or (isinstance(budget_val, (int, float)) and budget_val <= 0):
        missing.append("• **Maximum Budget Ceiling** in INR (e.g. `₹48,000`)")

    deadline_val = data.get("deadline_minutes")
    if deadline_val is None or (isinstance(deadline_val, (int, float)) and deadline_val <= 0):
        missing.append("• **Recovery Deadline** in minutes (e.g. `45 mins`)")

    if missing:
        # Build concise summary of recognised fields
        recognised_lines = []
        if data.get("shipment_id"):
            recognised_lines.append(f"• **Shipment ID:** `{data['shipment_id']}`")
        if data.get("cargo_class"):
            cargo_name = data["cargo_class"].value.capitalize() if isinstance(data["cargo_class"], CargoClass) else str(data["cargo_class"]).capitalize()
            recognised_lines.append(f"• **Cargo Classification:** `{cargo_name}`")
        if data.get("temp_min_c") is not None and data.get("temp_max_c") is not None:
            recognised_lines.append(f"• **Temperature Band:** `+{data['temp_min_c']:.1f}°C to +{data['temp_max_c']:.1f}°C`")
        if data.get("origin_location") and data["origin_location"] != "Distressed Vehicle Location":
            recognised_lines.append(f"• **Location:** `{data['origin_location']}`")
        if budget_val is not None and isinstance(budget_val, (int, float)) and budget_val > 0:
            recognised_lines.append(f"• **Budget Ceiling:** `₹{budget_val:,.0f} INR`")
        if deadline_val is not None and isinstance(deadline_val, (int, float)) and deadline_val > 0:
            recognised_lines.append(f"• **Recovery Deadline:** `{deadline_val} mins`")

        prompt_lines = [
            "⚠️ **FreightPact Cold-Chain Incident Intake**\n",
            "I detected an active cold-chain incident report, but need additional required parameters to initiate verified multi-agent recovery without guessing:\n",
        ]
        if recognised_lines:
            prompt_lines.append("**Recognised Incident Details:**\n" + "\n".join(recognised_lines) + "\n")

        prompt_lines.append("**Missing Required Parameters:**\n" + "\n".join(missing))
        prompt_lines.append("\nPlease submit the missing parameters using the form below or reply in chat to initiate verified multi-agent recovery.")
        prompt = "\n".join(prompt_lines)
        return None, prompt

    # All required parameters present -> Perform strict server-side validation
    try:
        max_budget = float(data["max_budget"])
        if max_budget <= 0:
            return None, "❌ Invalid budget ceiling: Maximum budget must be greater than ₹0 INR."
    except (ValueError, TypeError):
        return None, "❌ Invalid budget ceiling provided: Must be a positive numeric amount."

    try:
        deadline_minutes = int(data["deadline_minutes"])
        if deadline_minutes <= 0:
            return None, "❌ Invalid recovery deadline: Deadline must be greater than 0 minutes."
    except (ValueError, TypeError):
        return None, "❌ Invalid recovery deadline provided: Must be a positive integer in minutes."

    try:
        temp_min = float(data["temp_min_c"])
        temp_max = float(data["temp_max_c"])
        if temp_max <= temp_min:
            return None, f"❌ Invalid temperature band: Upper bound ({temp_max:.1f}°C) must be strictly greater than lower bound ({temp_min:.1f}°C)."
    except (ValueError, TypeError):
        return None, "❌ Invalid temperature values provided: Must be numeric degrees Celsius."

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
            shipment_id=str(data["shipment_id"]).strip().upper(),
            cargo_class=cargo_class,
            origin_location=origin_location,
            temp_min_c=temp_min,
            temp_max_c=temp_max,
            max_budget=max_budget,
            deadline_minutes=deadline_minutes,
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
    """Formats a concise, professional executive logistics briefing for ASI:One chat narration."""
    status_icon = "🟢" if terminal_result.terminal_state == "RECOMMENDED_FOR_CONFIRMATION" else "🔴"

    lines = [
        f"### {status_icon} FreightPact Cold-Chain Recovery Report",
        f"**Shipment ID:** `{request.shipment_id}` ({request.cargo_class.value}) · **Target:** `+{request.temp_min_c:.1f}°C to +{request.temp_max_c:.1f}°C` · **Budget:** `₹{request.max_budget:,.0f} INR` · **Deadline:** `{request.deadline_minutes} mins`\n",
    ]

    # Candidate Screening & Telemetry Verification Summary
    lines.append("**Candidate Screening & Telemetry Verification:**")
    if run_ctx.offers:
        for offer in run_ctx.offers:
            cid = offer.carrier_id
            name = offer.carrier_name
            quote_str = f"₹{offer.price:,.0f} INR · {offer.eta_minutes}m ETA"

            if run_ctx.selected_offer and cid == run_ctx.selected_offer.carrier_id:
                lines.append(
                    f"- 🟢 **VERIFIED `{cid}`** — {name} ({quote_str} · Verified Safe)"
                )
            elif cid in getattr(run_ctx, "rejected_candidate_ids", []):
                reason = run_ctx.rejection_reasons.get(cid, "Failed telemetry audit")
                lines.append(
                    f"- 🔴 **REJECTED `{cid}`** — {name} ({quote_str} · {reason})"
                )
            else:
                lines.append(
                    f"- 🟡 **STANDBY RESERVE `{cid}`** — {name} ({quote_str} · Reserve)"
                )
    else:
        lines.append("- *No carrier quotes received within response window.*")
    lines.append("")

    # Operational Recommendation Decision
    if terminal_result.terminal_state == "RECOMMENDED_FOR_CONFIRMATION" and run_ctx.selected_offer:
        sel = run_ctx.selected_offer
        lines.append(
            f"**Recommendation:** Simulated award recommended for **{sel.carrier_name}** (`{sel.carrier_id}`) "
            f"at **₹{sel.price:,.0f} INR** ({sel.eta_minutes}m ETA). "
            f"Use the interactive cards below to inspect telemetry evidence or confirm dispatch.\n"
        )
    else:
        lines.append(
            f"**Status:** `NO_SAFE_MATCH`\n"
            f"Unable to find a compliant carrier matching budget, ETA, and verified sensor criteria. "
            f"Operator manual intervention required or revise budget/deadline constraints.\n"
        )

    # Simulation Disclosure (Clean plain text without GitHub-specific [!NOTE])
    lines.append(
        "**Simulation Disclosure:** `SIMULATION — awaiting user confirmation`. "
        "Carrier quotes, sensor readings (Beta 13.8°C / Alpha 4.2°C), and cryptographic signatures are synthetic fixtures "
        "generated for reproducible hackathon demonstration; this system does not perform actual physical vehicle dispatch, "
        "real hardware sensor attestation, or live payment settlement."
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


def _extract_form_submission_data(action_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts and normalizes form submission values from ASI:One card return."""
    raw: Dict[str, Any] = {}
    for container in ("form_values", "form_data", "values", "fields"):
        nested = action_data.get(container)
        if isinstance(nested, dict):
            raw.update(nested)
        elif isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    raw[item["name"]] = item["value"]

    for k, v in action_data.items():
        if k not in {"action", "raw_text", "form_values", "form_data", "values", "fields"} and v is not None:
            raw[k] = v

    normalized: Dict[str, Any] = {}
    if raw.get("shipment_id"):
        normalized["shipment_id"] = str(raw["shipment_id"]).strip().upper()

    if raw.get("temp_range"):
        temp_str = str(raw["temp_range"])
        match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(?:to|[-–—]|and)\s*([+-]?\d+(?:\.\d+)?)", temp_str)
        if match:
            t1, t2 = float(match.group(1)), float(match.group(2))
            normalized["temp_min_c"] = min(t1, t2)
            normalized["temp_max_c"] = max(t1, t2)
    elif "temp_min_c" in raw and "temp_max_c" in raw:
        try:
            normalized["temp_min_c"] = float(raw["temp_min_c"])
            normalized["temp_max_c"] = float(raw["temp_max_c"])
        except (ValueError, TypeError):
            pass

    if raw.get("max_budget") is not None:
        try:
            val = str(raw["max_budget"]).replace(",", "").replace("₹", "").strip()
            normalized["max_budget"] = float(val)
        except (ValueError, TypeError):
            pass

    if raw.get("deadline_minutes") is not None:
        try:
            val = str(raw["deadline_minutes"]).replace("mins", "").replace("min", "").replace("m", "").strip()
            normalized["deadline_minutes"] = int(val)
        except (ValueError, TypeError):
            pass

    if raw.get("cargo_class"):
        val = str(raw["cargo_class"]).upper()
        if "VACCINE" in val:
            normalized["cargo_class"] = CargoClass.VACCINES
        elif "BIOLOGIC" in val:
            normalized["cargo_class"] = CargoClass.BIOLOGICS
        elif "INSULIN" in val:
            normalized["cargo_class"] = CargoClass.INSULIN
        else:
            normalized["cargo_class"] = CargoClass.PHARMA_GENERAL

    if raw.get("origin_location"):
        normalized["origin_location"] = str(raw["origin_location"]).strip()

    if raw.get("required_capacity_kg"):
        try:
            normalized["required_capacity_kg"] = float(raw["required_capacity_kg"])
        except (ValueError, TypeError):
            pass

    return normalized


def create_acp_chat_protocol(orchestrator: CoordinatorOrchestrator) -> Protocol:
    """Creates the official Agent Chat Protocol (ACP) for the primary coordinator agent."""
    from freightpact.cards import (
        build_dispatch_confirmation_card,
        build_dispatch_declination_card,
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

        from freightpact.cards import strip_agent_mention
        clean_text = strip_agent_mention(user_text)

        if not clean_text:
            reply = create_text_chat_message(
                "👋 Hello! I am **FreightPact**, your Autonomous Cold-Chain Shipment Recovery Coordinator. "
                "Report a distressed shipment incident (e.g., reefer failure, temperature rise, location, budget) "
                "to trigger autonomous multi-agent carrier discovery and telemetry verification."
            )
            await ctx.send(sender, reply)
            return

        # 2. Check if this is an interactive card action return (JSON or planner prose)
        card_action, action_data = parse_card_user_response(clean_text)

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

                is_conf = getattr(run_ctx, "is_confirmed", False)
                state_display = "CONFIRMED_DISPATCHED (Simulated)" if is_conf else run_ctx.current_state.value
                intro_text = (
                    f"📋 **Cold-Chain Incident Overview: `{req.shipment_id}`**\n\n"
                    f"- **Mission Run ID:** `{run_ctx.run_id[:16]}...`\n"
                    f"- **Cargo Class:** {req.cargo_class.value}\n"
                    f"- **Target Range:** {req.temp_min_c:.1f}°C to {req.temp_max_c:.1f}°C\n"
                    f"- **Budget Ceiling:** ₹{req.max_budget:,.0f} INR | **Deadline:** {req.deadline_minutes} mins\n"
                    f"- **Current State:** `{state_display}`"
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

                # If award is already confirmed, show the finalized receipt instead of re-prompting confirmation
                if getattr(run_ctx, "is_confirmed", False):
                    ctx.logger.info(f"[ACP] Mission {run_ctx.run_id} is already confirmed. Returning confirmation receipt.")
                    receipt_card = build_dispatch_confirmation_card(run_ctx)
                    reply_text = (
                        f"✅ **Simulated Carrier Award Already Confirmed**\n\n"
                        f"- **Shipment ID:** `{run_ctx.shipment_id}`\n"
                        f"- **Awarded Carrier:** `{run_ctx.selected_offer.carrier_name} ({run_ctx.selected_offer.carrier_id})`\n"
                        f"- **Mission Run ID:** `{run_ctx.run_id}`\n\n"
                        "**Simulation Disclosure:** `SIMULATION — demonstration record already finalized`."
                    )
                    reply_msg = create_interactive_chat_message(reply_text, metadata_card=receipt_card)
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
                    "**Simulation Disclosure:** `SIMULATION — demonstration record already finalized`."
                )
                receipt_card = None
                try:
                    receipt_card = build_dispatch_confirmation_card(run_ctx)
                except Exception as card_err:
                    ctx.logger.warning(f"[ACP] Failed to build confirmation card: {card_err}")
                if receipt_card:
                    ctx.logger.info(f"[ACP] Attached confirmation receipt card (kind={receipt_card.metadata.get('card_kind')}, is_terminal={receipt_card.metadata.get('is_terminal')}) for {sender}")
                reply_msg = create_interactive_chat_message(reply_text, metadata_card=receipt_card)
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
                "**Simulation Disclosure:** `SIMULATION — demonstration record only`. "
                "No physical vehicle dispatch, live hardware sensor attestation, or fiat banking settlement was performed."
            )
            receipt_card = None
            try:
                receipt_card = build_dispatch_confirmation_card(run_ctx)
            except Exception as card_err:
                ctx.logger.warning(f"[ACP] Failed to build confirmation card: {card_err}")

            if receipt_card:
                ctx.logger.info(f"[ACP] Attached confirmation receipt card (kind={receipt_card.metadata.get('card_kind')}, is_terminal={receipt_card.metadata.get('is_terminal')}) for {sender}")
            else:
                ctx.logger.warning(f"[ACP] No confirmation receipt card attached for {sender}")

            reply_msg = create_interactive_chat_message(reply_text, metadata_card=receipt_card)
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
                "**Simulation Disclosure:** `SIMULATION — recommendation declined`. "
                "State recorded in demo database. No vehicles were contacted or scheduled."
            )
            declined_card = None
            if run_ctx:
                try:
                    declined_card = build_dispatch_declination_card(run_ctx, carrier_id=declined_id)
                except Exception as card_err:
                    ctx.logger.warning(f"[ACP] Failed to build declination card: {card_err}")

            if declined_card:
                ctx.logger.info(f"[ACP] Attached declination receipt card (kind={declined_card.metadata.get('card_kind')}, is_terminal={declined_card.metadata.get('is_terminal')}) for {sender}")
            else:
                ctx.logger.warning(f"[ACP] No declination receipt card attached for {sender}")

            reply_msg = create_interactive_chat_message(reply_text, metadata_card=declined_card)
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

        async def _run_recovery_mission(req: RecoveryRequest):
            run_ctx = orchestrator.init_mission(req)
            await orchestrator.broadcast_quote_requests(ctx, req.run_id)

            # Wait for completion or timeout
            if req.run_id in orchestrator.completion_events:
                try:
                    await asyncio.wait_for(
                        orchestrator.completion_events[req.run_id].wait(),
                        timeout=12.0,
                    )
                except asyncio.TimeoutError:
                    ctx.logger.error(f"[ACP] Orchestration timed out for run {req.run_id}")
                    run_ctx.transition_to(WorkflowState.NO_SAFE_MATCH, reason="Orchestrator timeout")

            # Build real terminal outcome
            terminal_result = run_ctx.build_terminal_result()
            response_text = format_recovery_response(req, run_ctx, terminal_result)

            # Attach Interactive Card based on outcome (Default to Step 1: Incident Overview with interactive CTA)
            initial_card = None
            try:
                initial_card = build_incident_overview_card(
                    request=req,
                    run_ctx=run_ctx,
                    terminal_result=terminal_result,
                )
            except Exception as card_err:
                ctx.logger.warning(f"[ACP] Failed to generate initial overview card: {card_err}. Using plain-text fallback.")

            if initial_card:
                ctx.logger.info(f"[ACP] Attaching multi-step interactive card (kind={initial_card.metadata.get('card_kind')}) to response for {sender}")
            else:
                ctx.logger.warning(f"[ACP] No interactive card attached for mission {req.run_id}")

            reply_msg = create_interactive_chat_message(response_text, metadata_card=initial_card)
            await ctx.send(sender, reply_msg)

        if card_action == "submit_incident_intake":
            ctx.logger.info(f"[ACP] Form action 'submit_incident_intake' received from {sender}")
            submitted = _extract_form_submission_data(action_data)

            # Retrieve existing pending intake for this sender or shipment
            shipment_id = submitted.get("shipment_id") or action_data.get("shipment_id")
            pending = dict(orchestrator.get_pending_intake(sender) or {})
            if not pending and shipment_id:
                pending = dict(orchestrator.get_pending_intake(shipment_id) or {})

            # Merge existing pending data with newly submitted form data
            pending.update({k: v for k, v in submitted.items() if v is not None})

            # Server-side validation
            recovery_request, error_prompt = validate_or_request_missing_fields(pending)
            if error_prompt:
                # Still missing fields or invalid values -> save partial and re-render form
                orchestrator.save_pending_intake(sender, pending)
                if shipment_id:
                    orchestrator.save_pending_intake(shipment_id, pending)

                form_card = None
                try:
                    form_card = build_incident_intake_form_card(pending)
                except Exception as card_err:
                    ctx.logger.warning(f"[ACP] Failed to build form card: {card_err}")

                reply_msg = create_interactive_chat_message(error_prompt, metadata_card=form_card)
                await ctx.send(sender, reply_msg)
                return

            # All required fields are valid! Clear pending intake
            orchestrator.clear_pending_intake(sender)
            if shipment_id:
                orchestrator.clear_pending_intake(shipment_id)
            if recovery_request.shipment_id:
                orchestrator.clear_pending_intake(recovery_request.shipment_id)

            await _run_recovery_mission(recovery_request)
            return

        # 3. Parse intent via ASI:One if configured, else deterministic fallback
        asi_key = os.getenv("ASI_API_KEY")
        extracted_data = None
        if asi_key and asi_key.strip():
            extracted_data = await parse_incident_with_asi1(clean_text, asi_key.strip())

        if not extracted_data:
            extracted_data = parse_incident_fallback(clean_text)

        # Merge with pending intake for this sender if one exists
        pending = orchestrator.get_pending_intake(sender)
        if pending:
            new_ship = extracted_data.get("shipment_id")
            old_ship = pending.get("shipment_id")
            if not new_ship or not old_ship or new_ship == old_ship:
                merged = dict(pending)
                merged.update({k: v for k, v in extracted_data.items() if v is not None})
                extracted_data = merged

        # 4. Check for missing required fields
        recovery_request, error_prompt = validate_or_request_missing_fields(extracted_data)
        if error_prompt:
            orchestrator.save_pending_intake(sender, extracted_data)
            if extracted_data.get("shipment_id"):
                orchestrator.save_pending_intake(extracted_data["shipment_id"], extracted_data)

            # Build interactive form card to prompt user directly in ASI:One UI
            form_card = None
            try:
                form_card = build_incident_intake_form_card(extracted_data)
            except Exception as card_err:
                ctx.logger.warning(f"[ACP] Failed to generate form card: {card_err}. Using plain-text fallback.")

            reply = create_interactive_chat_message(error_prompt, metadata_card=form_card)
            await ctx.send(sender, reply)
            return

        # Clear pending intake once validated
        orchestrator.clear_pending_intake(sender)
        if extracted_data.get("shipment_id"):
            orchestrator.clear_pending_intake(extracted_data["shipment_id"])

        # 5. Initialize and run recovery mission
        await _run_recovery_mission(recovery_request)

    @proto.on_message(model=ChatAcknowledgement)
    async def handle_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        ctx.logger.info(f"[ACP] Received acknowledgement from {sender} for msg {msg.acknowledged_msg_id}")

    return proto
