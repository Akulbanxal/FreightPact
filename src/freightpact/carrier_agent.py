"""Independent Carrier Agent factory and message handlers for FreightPact."""

from __future__ import annotations

from typing import Optional
from uagents import Agent, Context, Protocol
from freightpact.fixtures import get_carrier_profile
from freightpact.protocols import QuoteOfferMsg, QuoteRequestMsg


def create_carrier_protocol(carrier_id: str) -> Protocol:
    """Creates a protocol bound to a specific carrier profile."""
    profile = get_carrier_profile(carrier_id)
    if not profile:
        raise ValueError(f"Unknown carrier_id='{carrier_id}' — cannot create carrier protocol")

    proto = Protocol(name=f"CarrierProtocol_{carrier_id}", version="1.0.0")

    @proto.on_message(model=QuoteRequestMsg, replies=QuoteOfferMsg)
    async def handle_quote_request(ctx: Context, sender: str, msg: QuoteRequestMsg):
        ctx.logger.info(
            f"[{carrier_id}] Received quote request for shipment {msg.shipment_id} (run_id={msg.run_id})"
        )

        offer = QuoteOfferMsg(
            correlation_id=msg.correlation_id,
            run_id=msg.run_id,
            carrier_id=profile.carrier_id,
            carrier_name=profile.name,
            price=profile.base_price,
            eta_minutes=profile.eta_minutes,
            capacity_kg=profile.capacity_kg,
            supported_temp_min_c=profile.temp_min_c,
            supported_temp_max_c=profile.temp_max_c,
            is_simulated=True,
        )

        ctx.logger.info(
            f"[{carrier_id}] Sending offer ₹{offer.price:,.0f} ({offer.eta_minutes}m) to {sender}"
        )
        await ctx.send(sender, offer)

    return proto


def create_carrier_agent(
    carrier_id: str,
    seed: Optional[str] = None,
    port: Optional[int] = None,
    enable_agent_inspector: bool = True,
    report_events: bool = False,
) -> Agent:
    """Instantiates an independent carrier agent process."""
    profile = get_carrier_profile(carrier_id)
    if not profile:
        raise ValueError(f"Unknown carrier_id='{carrier_id}' — cannot create carrier agent")

    agent = Agent(
        name=carrier_id,
        seed=seed or f"seed_{carrier_id}_demo_transport",
        port=port,
        endpoint=[f"http://127.0.0.1:{port}/submit"] if port else None,
        enable_agent_inspector=enable_agent_inspector,
        report_events=report_events,
    )

    proto = create_carrier_protocol(carrier_id)
    agent.include(proto)
    return agent
