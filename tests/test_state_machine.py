"""Unit tests for workflow state transitions and run isolation in states.py."""

import pytest
from freightpact.models import CargoClass, CarrierOffer, RecoveryRequest
from freightpact.states import (
    InvalidStateTransitionError,
    RunContext,
    WorkflowState,
)


@pytest.fixture
def sample_request():
    return RecoveryRequest(
        run_id="run_fsm_001",
        shipment_id="VAX-8821",
        cargo_class=CargoClass.VACCINES,
        origin_location="Delhi Highway Hub",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=48000.0,
        deadline_minutes=45,
    )


def test_valid_state_progression_to_recommendation(sample_request):
    """Verify happy path state progression from RECEIVED to RECOMMENDED_FOR_CONFIRMATION."""
    ctx = RunContext(sample_request)
    assert ctx.current_state == WorkflowState.RECEIVED

    ctx.transition_to(WorkflowState.VALIDATED)
    assert ctx.current_state == WorkflowState.VALIDATED

    ctx.transition_to(WorkflowState.COLLECTING_QUOTES)
    assert ctx.current_state == WorkflowState.COLLECTING_QUOTES

    ctx.transition_to(WorkflowState.RANKING)
    assert ctx.current_state == WorkflowState.RANKING

    ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE, reason="Audit Carrier Alpha")
    assert ctx.current_state == WorkflowState.VERIFYING_CANDIDATE

    ctx.transition_to(
        WorkflowState.RECOMMENDED_FOR_CONFIRMATION, reason="Auditor returned PASS"
    )
    assert ctx.current_state == WorkflowState.RECOMMENDED_FOR_CONFIRMATION

    # Terminal state allows no further transitions
    with pytest.raises(InvalidStateTransitionError):
        ctx.transition_to(WorkflowState.RECEIVED)


def test_illegal_state_transition_raises_error(sample_request):
    """Verify skipping states (e.g. RECEIVED -> RECOMMENDED_FOR_CONFIRMATION) is blocked."""
    ctx = RunContext(sample_request)
    with pytest.raises(InvalidStateTransitionError) as exc:
        ctx.transition_to(WorkflowState.RECOMMENDED_FOR_CONFIRMATION)
    assert "[FSM Error] Illegal transition" in str(exc.value)


def test_failover_transition_flow(sample_request):
    """Verify failover loop: VERIFYING_CANDIDATE -> CANDIDATE_REJECTED -> VERIFYING_NEXT -> VERIFYING_CANDIDATE."""
    ctx = RunContext(sample_request)
    ctx.transition_to(WorkflowState.VALIDATED)
    ctx.transition_to(WorkflowState.COLLECTING_QUOTES)
    ctx.transition_to(WorkflowState.RANKING)

    # 1. Audit Beta
    ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    ctx.record_rejected_candidate("carrier_beta", "Reefer temp 13.8°C exceeded safe limit")
    ctx.transition_to(WorkflowState.CANDIDATE_REJECTED)

    assert "carrier_beta" in ctx.rejected_candidate_ids

    # 2. Transition to next candidate
    ctx.transition_to(WorkflowState.VERIFYING_NEXT)
    ctx.transition_to(WorkflowState.VERIFYING_CANDIDATE)
    assert ctx.current_state == WorkflowState.VERIFYING_CANDIDATE


def test_no_safe_match_terminal_state(sample_request):
    """Verify transition to NO_SAFE_MATCH when all candidates fail or exceed budget."""
    ctx = RunContext(sample_request)
    ctx.transition_to(WorkflowState.VALIDATED)
    ctx.transition_to(WorkflowState.COLLECTING_QUOTES)
    ctx.transition_to(WorkflowState.RANKING)
    ctx.transition_to(WorkflowState.NO_SAFE_MATCH, reason="Zero feasible candidates")

    assert ctx.current_state == WorkflowState.NO_SAFE_MATCH
    result = ctx.build_terminal_result()
    assert result.terminal_state == "NO_SAFE_MATCH"
    assert result.selected_carrier_id is None


def test_run_isolation_rejected_set_does_not_leak():
    """Verify separate run contexts have completely isolated rejection memory."""
    req1 = RecoveryRequest(
        run_id="run_isolation_A",
        shipment_id="VAX-101",
        origin_location="Site A",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=40000.0,
        deadline_minutes=30,
    )
    req2 = RecoveryRequest(
        run_id="run_isolation_B",
        shipment_id="VAX-102",
        origin_location="Site B",
        temp_min_c=2.0,
        temp_max_c=8.0,
        max_budget=40000.0,
        deadline_minutes=30,
    )

    ctx1 = RunContext(req1)
    ctx2 = RunContext(req2)

    ctx1.record_rejected_candidate("carrier_beta", "Failed audit in Run A")

    # In Run A, carrier_beta is rejected
    assert "carrier_beta" in ctx1.rejected_candidate_ids

    # In Run B, carrier_beta is NOT rejected!
    assert "carrier_beta" not in ctx2.rejected_candidate_ids
    assert len(ctx2.rejected_candidate_ids) == 0
