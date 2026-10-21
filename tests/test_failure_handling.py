"""Comprehensive robustness, timeout, and failure handling integration tests."""

import asyncio
import socket
import tempfile
import pytest
from uagents import Bureau, Context
from freightpact.auditor_agent import create_auditor_agent
from freightpact.carrier_agent import create_carrier_agent
from freightpact.coordinator_agent import CoordinatorOrchestrator, create_coordinator_agent
from freightpact.models import CargoClass, RecoveryRequest
from freightpact.states import WorkflowState
from freightpact.storage import StorageRepository

FIXTURE_SECRET = "freightpact-synthetic-test-fixture-key-12345"


def get_free_port() -> int:
    """Finds an available local port for Bureau tests."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_all_candidates_failing_audit_results_in_no_safe_match():
    """Verify when all available candidates fail verification, state safely terminates in NO_SAFE_MATCH."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tf:
        db_path = tf.name

    storage = StorageRepository(db_path=db_path)

    # In this test, we only register Carrier Beta (whose telemetry is broken)
    beta_agent = create_carrier_agent(
        carrier_id="carrier_beta",
        seed="seed_fail_beta_201",
    )
    auditor_agent = create_auditor_agent(
        secret_key=FIXTURE_SECRET,
        seed="seed_fail_auditor_202",
    )

    carrier_addresses = {"carrier_beta": str(beta_agent.address)}

    orchestrator = CoordinatorOrchestrator(
        auditor_address=str(auditor_agent.address),
        carrier_addresses=carrier_addresses,
        max_audit_attempts=2,
        storage=storage,
        quote_timeout_seconds=0.5,
    )

    coordinator_agent = create_coordinator_agent(
        orchestrator=orchestrator,
        seed="seed_fail_coord_203",
    )

    request = RecoveryRequest(
        shipment_id="VAX-9999",
        cargo_class=CargoClass.VACCINES,
        origin_location="Delhi Gateway",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=50000.0,
        deadline_minutes=45,
    )
    run_ctx = orchestrator.init_mission(request)

    @coordinator_agent.on_event("startup")
    async def initiate_test(ctx: Context):
        await asyncio.sleep(0.1)
        await orchestrator.broadcast_quote_requests(ctx, request.run_id)

    port = get_free_port()
    bureau = Bureau(port=port)
    bureau.add(coordinator_agent)
    bureau.add(beta_agent)
    bureau.add(auditor_agent)

    bureau_task = asyncio.create_task(bureau.run_async())

    await asyncio.wait_for(
        orchestrator.completion_events[request.run_id].wait(),
        timeout=5.0,
    )

    assert run_ctx.current_state == WorkflowState.NO_SAFE_MATCH
    assert run_ctx.selected_offer is None
    assert "carrier_beta" in run_ctx.rejected_candidate_ids

    result = run_ctx.build_terminal_result()
    assert result.terminal_state == "NO_SAFE_MATCH"
    assert result.selected_carrier_id is None


@pytest.mark.asyncio
async def test_worker_timeout_proceeds_with_available_quotes():
    """Verify that when a carrier times out / does not respond, the coordinator advances with available quotes."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tf:
        db_path = tf.name

    storage = StorageRepository(db_path=db_path)

    # Carrier Alpha responds; Carrier Ghost will not be started (simulating a dead/timed-out carrier)
    alpha_agent = create_carrier_agent(
        carrier_id="carrier_alpha",
        seed="seed_timeout_alpha_301",
    )
    auditor_agent = create_auditor_agent(
        secret_key=FIXTURE_SECRET,
        seed="seed_timeout_auditor_302",
    )

    # Carrier Ghost has a dummy address that will never reply
    carrier_addresses = {
        "carrier_alpha": str(alpha_agent.address),
        "carrier_ghost": "agent1qdummyghostaddressneverreplying0000000000000000",
    }

    orchestrator = CoordinatorOrchestrator(
        auditor_address=str(auditor_agent.address),
        carrier_addresses=carrier_addresses,
        max_audit_attempts=2,
        storage=storage,
        quote_timeout_seconds=0.5,  # Short timeout for fast test
    )

    coordinator_agent = create_coordinator_agent(
        orchestrator=orchestrator,
        seed="seed_timeout_coord_303",
    )

    request = RecoveryRequest(
        shipment_id="VAX-TIMEOUT-TEST",
        cargo_class=CargoClass.VACCINES,
        origin_location="Delhi Gateway",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=50000.0,
        deadline_minutes=45,
    )
    run_ctx = orchestrator.init_mission(request)

    @coordinator_agent.on_event("startup")
    async def initiate_timeout_run(ctx: Context):
        await asyncio.sleep(0.1)
        await orchestrator.broadcast_quote_requests(ctx, request.run_id)

    port = get_free_port()
    bureau = Bureau(port=port)
    bureau.add(coordinator_agent)
    bureau.add(alpha_agent)
    bureau.add(auditor_agent)

    bureau_task = asyncio.create_task(bureau.run_async())

    # Wait for timeout guard to fire and complete with Alpha
    await asyncio.wait_for(
        orchestrator.completion_events[request.run_id].wait(),
        timeout=5.0,
    )

    # Even though ghost carrier never replied, timeout guard allowed Alpha to be audited and awarded!
    assert run_ctx.current_state == WorkflowState.RECOMMENDED_FOR_CONFIRMATION
    assert run_ctx.selected_offer is not None
    assert run_ctx.selected_offer.carrier_id == "carrier_alpha"
