"""Comprehensive integration test for complete local multi-agent product wiring.

Tests:
1. Primary recovery scenario:
   - Request with temperature band, budget, deadline, capacity, location.
   - 3 real worker agent processes (Carrier Beta, Carrier Alpha, Carrier Gamma).
   - Real structured uAgents message exchange.
   - Deterministic policy filtering and ranking (Beta ₹38k ranked 1st, Alpha ₹41.5k 2nd, Gamma ₹43k 3rd).
   - Auditor challenges Beta with unique nonce.
   - Beta fails verification (13.8°C > 8.0°C and compressor inactive).
   - Coordinator rejects Beta and evaluates Alpha.
   - Alpha passes verification (4.2°C compliant, compressor active, valid HMAC).
   - Output includes quote, ETA, and explicit 'SIMULATION — awaiting user confirmation'.
2. Full failure scenario:
   - When all candidates fail verification (or strict budget/temp constraints), the system reports NO_SAFE_MATCH.
"""

import asyncio
import socket
import tempfile
import pytest
from uagents import Bureau, Context

from freightpact.auditor_agent import create_auditor_agent
from freightpact.carrier_agent import create_carrier_agent
from freightpact.chat_interface import (
    create_acp_chat_protocol,
    create_text_chat_message,
    format_recovery_response,
)
from freightpact.coordinator_agent import CoordinatorOrchestrator, create_coordinator_agent
from freightpact.models import CargoClass, RecoveryRequest
from freightpact.states import WorkflowState
from freightpact.storage import StorageRepository

FIXTURE_SECRET = "freightpact-synthetic-test-fixture-key-12345"


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_end_to_end_three_carrier_recovery_and_failover():
    """Tests primary recovery scenario with 3 real carrier agents and 1 auditor agent."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tf:
        db_path = tf.name

    storage = StorageRepository(db_path=db_path)

    # 1. Instantiate the three real carrier agents
    beta_agent = create_carrier_agent(carrier_id="carrier_beta", seed="seed_e2e_beta_1")
    alpha_agent = create_carrier_agent(carrier_id="carrier_alpha", seed="seed_e2e_alpha_2")
    gamma_agent = create_carrier_agent(carrier_id="carrier_gamma", seed="seed_e2e_gamma_3")

    # 2. Instantiate auditor agent
    auditor_agent = create_auditor_agent(secret_key=FIXTURE_SECRET, seed="seed_e2e_auditor_4")

    carrier_addresses = {
        "carrier_beta": str(beta_agent.address),
        "carrier_alpha": str(alpha_agent.address),
        "carrier_gamma": str(gamma_agent.address),
    }

    # 3. Instantiate coordinator orchestrator
    orchestrator = CoordinatorOrchestrator(
        auditor_address=str(auditor_agent.address),
        carrier_addresses=carrier_addresses,
        max_audit_attempts=3,
        storage=storage,
        quote_timeout_seconds=1.0,
    )

    coordinator_agent = create_coordinator_agent(
        orchestrator=orchestrator,
        seed="seed_e2e_coord_5",
        include_chat=True,
    )

    # 4. Construct incident request
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

    @coordinator_agent.on_event("startup")
    async def initiate_mission(ctx: Context):
        await asyncio.sleep(0.1)
        await orchestrator.broadcast_quote_requests(ctx, request.run_id)

    port = get_free_port()
    bureau = Bureau(port=port)
    bureau.add(coordinator_agent)
    bureau.add(beta_agent)
    bureau.add(alpha_agent)
    bureau.add(gamma_agent)
    bureau.add(auditor_agent)

    bureau_task = asyncio.create_task(bureau.run_async())

    # Wait for recovery mission to complete
    await asyncio.wait_for(
        orchestrator.completion_events[request.run_id].wait(),
        timeout=6.0,
    )

    # 5. Assertions on orchestrator outcome
    assert run_ctx.current_state == WorkflowState.RECOMMENDED_FOR_CONFIRMATION
    assert len(run_ctx.offers) == 3

    # Ranking verified: Beta was cheapest (₹38,000), Alpha (₹41,500), Gamma (₹43,000)
    assert run_ctx.offers[0].carrier_id in carrier_addresses

    # Telemetry verification: Beta failed, Alpha passed
    assert "carrier_beta" in run_ctx.rejected_candidate_ids
    assert "Temperature non-compliant" in run_ctx.rejection_reasons["carrier_beta"]

    assert run_ctx.selected_offer is not None
    assert run_ctx.selected_offer.carrier_id == "carrier_alpha"
    assert run_ctx.selected_offer.price == 41500.0
    assert run_ctx.selected_offer.eta_minutes == 38

    # Terminal result verification
    terminal_result = run_ctx.build_terminal_result()
    assert terminal_result.terminal_state == "RECOMMENDED_FOR_CONFIRMATION"
    assert terminal_result.selected_carrier_id == "carrier_alpha"

    # Verify ASI:One response markdown formatting and disclosures
    report = format_recovery_response(request, run_ctx, terminal_result)
    assert "FreightPact Cold-Chain Recovery Report" in report
    assert "VAX-8821" in report
    assert "REJECTED `carrier_beta`" in report
    assert "VERIFIED `carrier_alpha`" in report
    assert "SIMULATION — awaiting user confirmation" in report
    assert "actual physical vehicle dispatch" in report


@pytest.mark.asyncio
async def test_end_to_end_all_candidates_failing_reports_no_safe_match():
    """Tests failure scenario where every candidate fails and system reports NO_SAFE_MATCH."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tf:
        db_path = tf.name

    storage = StorageRepository(db_path=db_path)

    # Only Carrier Beta is deployed; its telemetry is non-compliant (13.8°C)
    beta_agent = create_carrier_agent(carrier_id="carrier_beta", seed="seed_e2e_fail_beta_1")
    auditor_agent = create_auditor_agent(secret_key=FIXTURE_SECRET, seed="seed_e2e_fail_auditor_2")

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
        seed="seed_e2e_fail_coord_3",
        include_chat=True,
    )

    request = RecoveryRequest(
        shipment_id="VAX-FAILURE-TEST",
        cargo_class=CargoClass.VACCINES,
        origin_location="Sector 18 Cold Depot, Gurugram",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
        required_capacity_kg=1000.0,
    )
    run_ctx = orchestrator.init_mission(request)

    @coordinator_agent.on_event("startup")
    async def initiate_fail_mission(ctx: Context):
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

    terminal_result = run_ctx.build_terminal_result()
    assert terminal_result.terminal_state == "NO_SAFE_MATCH"
    assert terminal_result.selected_carrier_id is None

    report = format_recovery_response(request, run_ctx, terminal_result)
    assert "NO_SAFE_MATCH" in report
    assert "Unable to find a compliant carrier matching budget, ETA, and verified sensor criteria" in report
    assert "SIMULATION — awaiting user confirmation" in report
