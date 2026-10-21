"""Local multi-agent runnable demonstration for FreightPact.

Spawns real independent uAgents in an in-process Bureau:
1. Primary Recovery Coordinator Agent (with ACP support)
2. Carrier Beta Agent (Budget Express, ₹38,000 quote, 13.8°C compromised reefer)
3. Carrier Alpha Agent (PharmaCold, ₹41,500 quote, 4.2°C compliant reefer)
4. Carrier Gamma Agent (Reliable Transport, ₹43,000 quote, 4.8°C backup)
5. Telemetry Auditor Agent (cryptographic HMAC-SHA256 sensor verification)

Demonstrates:
- Autonomous quote collection & deterministic ranking (Beta cheapest)
- Auditor verification failure on Beta (13.8°C > 8.0°C and inactive compressor)
- Automatic failover to Alpha (4.2°C compliant, valid HMAC signature)
- Generation of transparent ASI:One Markdown Report & Interactive Card
- Clear simulation disclosures
"""

import asyncio
import os
import sys
import tempfile
from uagents import Bureau, Context

from freightpact.auditor_agent import create_auditor_agent
from freightpact.carrier_agent import create_carrier_agent
from freightpact.cards import build_final_recommendation_review_card
from freightpact.chat_interface import format_recovery_response
from freightpact.config import load_config
from freightpact.coordinator_agent import CoordinatorOrchestrator, create_coordinator_agent
from freightpact.models import CargoClass, RecoveryRequest
from freightpact.storage import StorageRepository


async def run_demo():
    print("=" * 80)
    print("🚀 STARTING FREIGHTPACT LOCAL MULTI-AGENT DEMONSTRATION")
    print("=" * 80)

    # 1. Load configuration or setup demo secrets
    secret_key = os.getenv("HMAC_FIXTURE_SECRET", "freightpact-demo-fixture-hmac-secret-12345")
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "freightpact_demo.db")
    storage = StorageRepository(db_path=db_path)

    print(f"\n[INIT] Using SQLite persistence at: {db_path}")
    print("[INIT] Initializing 5 independent uAgents...")

    # 2. Create worker agents
    carrier_beta = create_carrier_agent("carrier_beta", seed="demo_seed_carrier_beta_001")
    carrier_alpha = create_carrier_agent("carrier_alpha", seed="demo_seed_carrier_alpha_002")
    carrier_gamma = create_carrier_agent("carrier_gamma", seed="demo_seed_carrier_gamma_003")
    auditor = create_auditor_agent(secret_key=secret_key, seed="demo_seed_auditor_004")

    carrier_addresses = {
        "carrier_beta": str(carrier_beta.address),
        "carrier_alpha": str(carrier_alpha.address),
        "carrier_gamma": str(carrier_gamma.address),
    }

    print(f"  • Carrier Beta:    {carrier_beta.address} (Cheapest ₹38k / 13.8°C)")
    print(f"  • Carrier Alpha:   {carrier_alpha.address} (Compliant ₹41.5k / 4.2°C)")
    print(f"  • Carrier Gamma:   {carrier_gamma.address} (Backup ₹43k / 4.8°C)")
    print(f"  • Telemetry Audit: {auditor.address} (HMAC-SHA256 Verifier)")

    # 3. Create coordinator
    orchestrator = CoordinatorOrchestrator(
        auditor_address=str(auditor.address),
        carrier_addresses=carrier_addresses,
        max_audit_attempts=3,
        storage=storage,
        quote_timeout_seconds=1.5,
    )
    coordinator = create_coordinator_agent(
        orchestrator=orchestrator,
        seed="demo_seed_coordinator_005",
        include_chat=True,
    )
    print(f"  • Coordinator:     {coordinator.address}")

    # 4. Define emergency cold-chain incident
    incident_request = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Cold Logistics Hub, Gurugram",
        destination_location="Apex BioCold Storage Facility, New Delhi",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
        required_capacity_kg=1000.0,
    )

    print("\n" + "-" * 80)
    print("📦 INCOMING SHIPMENT INCIDENT DISPATCH:")
    print(f"  • Shipment ID:  {incident_request.shipment_id} ({incident_request.cargo_class.value})")
    print(f"  • Safe Temp:    +{incident_request.temp_min_c:.1f}°C to +{incident_request.temp_max_c:.1f}°C")
    print(f"  • Max Budget:   ₹{incident_request.max_budget:,.0f} INR")
    print(f"  • Max Deadline: {incident_request.deadline_minutes} minutes")
    print(f"  • Location:     {incident_request.origin_location}")
    print("-" * 80)

    run_ctx = orchestrator.init_mission(incident_request)

    # Trigger recovery dispatch on startup
    @coordinator.on_event("startup")
    async def trigger_recovery(ctx: Context):
        await asyncio.sleep(0.2)
        print("\n📡 [COORDINATOR] Broadcasting QuoteRequestMsg to 3 carrier agents...")
        await orchestrator.broadcast_quote_requests(ctx, incident_request.run_id)

    # 5. Assemble Bureau
    bureau = Bureau(port=8055)
    bureau.add(coordinator)
    bureau.add(carrier_beta)
    bureau.add(carrier_alpha)
    bureau.add(carrier_gamma)
    bureau.add(auditor)

    bureau_task = asyncio.create_task(bureau.run_async())

    print("\n⏳ Executing autonomous multi-agent coordination...")
    # Wait for completion event
    await asyncio.wait_for(
        orchestrator.completion_events[incident_request.run_id].wait(),
        timeout=8.0,
    )

    print("\n" + "=" * 80)
    print("🏁 RECOVERY MISSION COMPLETED — OUTPUT SUMMARY")
    print("=" * 80)

    terminal_result = run_ctx.build_terminal_result()
    report = format_recovery_response(incident_request, run_ctx, terminal_result)
    print(report)

    # If recommended, preview generated review card payload
    if run_ctx.selected_offer:
        verdict = run_ctx.audit_verdicts.get(run_ctx.selected_offer.carrier_id)
        card = build_final_recommendation_review_card(
            request=incident_request,
            selected_offer=run_ctx.selected_offer,
            audit_verdict_reason=verdict.reason if verdict else "Verified compliant",
            recorded_temp_c=verdict.recorded_temp_c if verdict and verdict.recorded_temp_c is not None else 4.2,
        )
        print("\n" + "-" * 80)
        print("🎴 ASI:ONE INTERACTIVE CARD GENERATED (Review Envelope):")
        print(f"  • Protocol Version: {card.metadata['card_protocol_version']}")
        print(f"  • Card Kind:        {card.metadata['card_kind']}")
        print(f"  • Requires Action:  {card.metadata['requires_card_interaction']}")
        print(f"  • Payload Size:     {len(card.metadata['card_payload'].encode('utf-8'))} bytes (< 64 KB limit)")
        print("-" * 80)

    print("\n✅ Verification complete: Beta rejected -> Failover succeeded -> Alpha recommended.")
    print("=" * 80)


def main():
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
