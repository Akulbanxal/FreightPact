"""Workflow state machine, transition guards, and run isolation for FreightPact."""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Set
from freightpact.models import AuditEvent, AuditVerdict, CarrierOffer, RecoveryRequest, RecoveryResult


class WorkflowState(str, Enum):
    """Explicit lifecycle states for recovery orchestration."""
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    COLLECTING_QUOTES = "COLLECTING_QUOTES"
    RANKING = "RANKING"
    VERIFYING_CANDIDATE = "VERIFYING_CANDIDATE"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    VERIFYING_NEXT = "VERIFYING_NEXT"
    RECOMMENDED_FOR_CONFIRMATION = "RECOMMENDED_FOR_CONFIRMATION"  # Terminal success
    NO_SAFE_MATCH = "NO_SAFE_MATCH"                              # Terminal safe-exit
    ERROR_RECOVERABLE = "ERROR_RECOVERABLE"                      # Safe prompt state


# Strict transition graph: current_state -> set of permitted next_states
ALLOWED_TRANSITIONS: Dict[WorkflowState, Set[WorkflowState]] = {
    WorkflowState.RECEIVED: {
        WorkflowState.VALIDATED,
        WorkflowState.ERROR_RECOVERABLE,
    },
    WorkflowState.ERROR_RECOVERABLE: {
        WorkflowState.VALIDATED,
        WorkflowState.NO_SAFE_MATCH,
    },
    WorkflowState.VALIDATED: {
        WorkflowState.COLLECTING_QUOTES,
        WorkflowState.NO_SAFE_MATCH,
    },
    WorkflowState.COLLECTING_QUOTES: {
        WorkflowState.RANKING,
        WorkflowState.NO_SAFE_MATCH,
    },
    WorkflowState.RANKING: {
        WorkflowState.VERIFYING_CANDIDATE,
        WorkflowState.NO_SAFE_MATCH,
    },
    WorkflowState.VERIFYING_CANDIDATE: {
        WorkflowState.RECOMMENDED_FOR_CONFIRMATION,
        WorkflowState.CANDIDATE_REJECTED,
    },
    WorkflowState.CANDIDATE_REJECTED: {
        WorkflowState.VERIFYING_NEXT,
        WorkflowState.NO_SAFE_MATCH,
    },
    WorkflowState.VERIFYING_NEXT: {
        WorkflowState.VERIFYING_CANDIDATE,
        WorkflowState.NO_SAFE_MATCH,
    },
    # Terminal states admit no further transitions
    WorkflowState.RECOMMENDED_FOR_CONFIRMATION: set(),
    WorkflowState.NO_SAFE_MATCH: set(),
}


class InvalidStateTransitionError(Exception):
    """Raised when an illegal workflow state transition is attempted."""
    pass


class RunContext:
    """Isolated state container for a single recovery run execution."""

    def __init__(self, request: RecoveryRequest, max_audit_attempts: int = 3):
        self.run_id: str = request.run_id
        self.shipment_id: str = request.shipment_id
        self.request: RecoveryRequest = request
        self.max_audit_attempts: int = max_audit_attempts

        self.current_state: WorkflowState = WorkflowState.RECEIVED
        self.offers: List[CarrierOffer] = []
        self.ranked_candidates: List[CarrierOffer] = []
        self.active_candidate_index: int = 0

        self.rejected_candidate_ids: Set[str] = set()
        self.rejection_reasons: Dict[str, str] = {}
        self.audit_verdicts: Dict[str, AuditVerdict] = {}
        self.audit_attempts: int = 0

        self.selected_offer: Optional[CarrierOffer] = None
        self.audit_trail: List[AuditEvent] = []

        self._record_event(
            f"Recovery mission initialized for shipment {self.shipment_id} (run_id={self.run_id})"
        )

    def transition_to(self, new_state: WorkflowState, reason: str = "") -> None:
        """Transitions to new state validating against the allowed transition graph."""
        allowed = ALLOWED_TRANSITIONS.get(self.current_state, set())
        if new_state not in allowed:
            raise InvalidStateTransitionError(
                f"[FSM Error] Illegal transition from {self.current_state.value} to {new_state.value}. "
                f"Allowed destinations: {[s.value for s in allowed]}"
            )

        old_state = self.current_state
        self.current_state = new_state
        msg = f"Transitioned: {old_state.value} -> {new_state.value}"
        if reason:
            msg += f" ({reason})"
        self._record_event(msg)

    def record_rejected_candidate(self, candidate_id: str, reason: str) -> None:
        """Permanently records candidate rejection for this run to prevent reconsideration."""
        self.rejected_candidate_ids.add(candidate_id)
        self.rejection_reasons[candidate_id] = reason
        self._record_event(
            f"Candidate '{candidate_id}' rejected: {reason}",
            candidate_id=candidate_id,
            details={"reason": reason},
        )

    def get_next_candidate(self) -> Optional[CarrierOffer]:
        """Returns the next feasible candidate that has not been rejected."""
        while self.active_candidate_index < len(self.ranked_candidates):
            candidate = self.ranked_candidates[self.active_candidate_index]
            self.active_candidate_index += 1
            if candidate.carrier_id not in self.rejected_candidate_ids:
                return candidate
        return None

    def build_terminal_result(self) -> RecoveryResult:
        """Constructs the immutable final recovery result."""
        verdict = None
        if self.selected_offer and self.selected_offer.carrier_id in self.audit_verdicts:
            verdict = self.audit_verdicts[self.selected_offer.carrier_id]

        return RecoveryResult(
            run_id=self.run_id,
            shipment_id=self.shipment_id,
            terminal_state=self.current_state.value,
            selected_carrier_id=self.selected_offer.carrier_id if self.selected_offer else None,
            selected_offer=self.selected_offer,
            audit_verdict=verdict,
            rejected_candidates=sorted(list(self.rejected_candidate_ids)),
            rejection_reasons=self.rejection_reasons,
            audit_trail=self.audit_trail,
            is_simulated=True,
        )

    def _record_event(self, message: str, candidate_id: Optional[str] = None, details: Optional[dict] = None) -> None:
        event = AuditEvent(
            run_id=self.run_id,
            state=self.current_state.value,
            message=message,
            candidate_id=candidate_id,
            details=details or {},
        )
        self.audit_trail.append(event)
