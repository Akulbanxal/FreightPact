"""Integration test demonstrating genuine multi-agent message exchange and bounded failover.

Verifies:
1. Real asynchronous uAgents message delivery across 4 independent agents (Coordinator, Carrier Beta, Carrier Alpha, Auditor).
2. Carrier Beta (cheapest @ ₹38,000) is ranked first, but fails Telemetry Auditor verification (13.8°C / compressor inactive).
3. Coordinator marks Beta as rejected and evaluates next candidate.
4. Carrier Alpha (₹41,500) passes Telemetry Auditor verification (4.2°C / compressor active).
5. Coordinator transitions to RECOMMENDED_FOR_CONFIRMATION with Alpha awarded.
"""

import asyncio
import socket
import pytest
from uagents import Bureau, Context
from freightpact.auditor_agent import create_auditor_agent
from freightpact.carrier_agent import create_carrier_agent
from freightpact.coordinator_agent import CoordinatorOrchestrator, create_coordinator_agent
from freightpact.models import CargoClass, RecoveryRequest
from freightpact.states import WorkflowState

FIXTURE_SECRET = "freightpact-synthetic-test-fixture-key-12345"


def get_free_port() -> int:
    """Finds an available local port for Bureau tests."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_multi_agent_real_message_flow_and_failover():
    """Executes a real in-process multi-agent message exchange and verifies Beta-to-Alpha failover."""

    # 1. Initialize independent agents
    beta_agent = create_carrier_agent(
        carrier_id="carrier_beta",
        seed="seed_test_carrier_beta_101",
    )
    alpha_agent = create_carrier_agent(
        carrier_id="carrier_alpha",
        seed="seed_test_carrier_alpha_102",
    )
    auditor_agent = create_auditor_agent(
        secret_key=FIXTURE_SECRET,
        seed="seed_test_telemetry_auditor_103",
    )

    carrier_addresses = {
        "carrier_beta": str(beta_agent.address),
        "carrier_alpha": str(alpha_agent.address),
    }

    orchestrator = CoordinatorOrchestrator(
        auditor_address=str(auditor_agent.address),
        carrier_addresses=carrier_addresses,
        max_audit_attempts=3,
    )

    coordinator_agent = create_coordinator_agent(
        orchestrator=orchestrator,
        seed="seed_test_coordinator_104",
    )

    # 2. Setup the recovery request
    request = RecoveryRequest(
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Cold Depot, Gurugram",
        destination_location="Apex BioCold Storage, Delhi",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
        required_capacity_kg=1000.0,
    )
    run_ctx = orchestrator.init_mission(request)

    # 3. Add background trigger to initiate quote broadcasting after agents start
    @coordinator_agent.on_event("startup")
    async def initiate_recovery(ctx: Context):
        await asyncio.sleep(0.1)
        await orchestrator.broadcast_quote_requests(ctx, request.run_id)

    # 4. Assemble Bureau on an ephemeral free port
    port = get_free_port()
    bureau = Bureau(port=port)
    bureau.add(coordinator_agent)
    bureau.add(beta_agent)
    bureau.add(alpha_agent)
    bureau.add(auditor_agent)

    bureau_task = asyncio.create_task(bureau.run_async())

    # Wait up to 5 seconds for complete multi-agent quote collection & failover flow
    await asyncio.wait_for(
        orchestrator.completion_events[request.run_id].wait(),
        timeout=5.0,
    )

    # 5. Assertions on final orchestrator state
    assert run_ctx.current_state == WorkflowState.RECOMMENDED_FOR_CONFIRMATION
    assert run_ctx.selected_offer is not None
    assert run_ctx.selected_offer.carrier_id == "carrier_alpha"
    assert run_ctx.selected_offer.price == 41500.0

    # Verify Carrier Beta was rejected
    assert "carrier_beta" in run_ctx.rejected_candidate_ids
    assert "Temperature non-compliant" in run_ctx.rejection_reasons["carrier_beta"]

    # Verify audit attempts count (Beta failed = attempt 1, Alpha passed = attempt 2)
    assert run_ctx.audit_attempts == 2

    # Verify terminal result build
    result = run_ctx.build_terminal_result()
    assert result.terminal_state == "RECOMMENDED_FOR_CONFIRMATION"
    assert result.selected_carrier_id == "carrier_alpha"
    assert "carrier_beta" in result.rejected_candidates
    assert result.is_simulated is True
