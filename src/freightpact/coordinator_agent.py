"""Recovery Coordinator multi-agent protocol and orchestration module.

Owns the workflow lifecycle, quote collection from carriers, deterministic ranking,
and failover audit challenge loops with the Telemetry Auditor.
Integrates SQLite persistence, idempotency handling, and structured audit logs.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from uuid import uuid4
from uagents import Agent, Context, Protocol
from freightpact.audit_log import audit_logger
from freightpact.models import AuditVerdict, CarrierOffer, RecoveryRequest, RecoveryResult
from freightpact.policy import filter_eligible_offers
from freightpact.protocols import AuditChallengeMsg, AuditVerdictMsg, QuoteOfferMsg, QuoteRequestMsg
from freightpact.ranking import rank_carrier_offers
from freightpact.states import RunContext, WorkflowState
from freightpact.storage import StorageRepository


class CoordinatorOrchestrator:
    """Manages multi-agent recovery missions for the primary coordinator agent."""

    def __init__(
        self,
        auditor_address: str,
        carrier_addresses: Dict[str, str],
        max_audit_attempts: int = 3,
        storage: Optional[StorageRepository] = None,
        quote_timeout_seconds: float = 4.0,
    ):
        self.auditor_address = auditor_address
        self.carrier_addresses = carrier_addresses
        self.max_audit_attempts = max_audit_attempts
        self.storage = storage or StorageRepository()
        self.quote_timeout_seconds = quote_timeout_seconds

        # Active run contexts keyed by run_id
        self.runs: Dict[str, RunContext] = {}
        # Completion futures keyed by run_id
        self.completion_events: Dict[str, asyncio.Event] = {}
        # Background quote timeout tasks keyed by run_id
        self.quote_guard_tasks: Dict[str, asyncio.Task] = {}

    def init_mission(self, request: RecoveryRequest) -> RunContext:
        """Initializes and persists a new isolated run context."""
        ctx = RunContext(request, max_audit_attempts=self.max_audit_attempts)
        self.runs[request.run_id] = ctx
        self.completion_events[request.run_id] = asyncio.Event()

        # Persist in storage
        try:
            self.storage.save_run(request, initial_state=ctx.current_state.value)
        except Exception as exc:
            audit_logger.log_event(
                "STORAGE_ERROR",
                run_id=request.run_id,
                role="coordinator",
                details={"error": str(exc)},
            )

        audit_logger.log_event(
            "MISSION_INITIALIZED",
            run_id=request.run_id,
            role="coordinator",
            details={
                "shipment_id": request.shipment_id,
                "temp_min_c": request.temp_min_c,
                "temp_max_c": request.temp_max_c,
                "max_budget": request.max_budget,
                "deadline_minutes": request.deadline_minutes,
            },
        )
        return ctx

    def get_run(self, run_id: str) -> Optional[RunContext]:
        return self.runs.get(run_id)

    async def broadcast_quote_requests(self, agent_ctx: Context, run_id: str):
        """Broadcasts QuoteRequestMsg to all known carrier agents with timeout enforcement."""
        run = self.get_run(run_id)
        if not run:
            return

        run.transition_to(WorkflowState.VALIDATED)
        run.transition_to(WorkflowState.COLLECTING_QUOTES)
        self._sync_state(run)

        req = run.request
        audit_logger.log_event(
            "BROADCASTING_QUOTES",
            run_id=run.run_id,
            role="coordinator",
            details={"carriers_targeted": list(self.carrier_addresses.keys())},
        )

        for carrier_id, address in self.carrier_addresses.items():
            msg = QuoteRequestMsg(
                run_id=run.run_id,
                shipment_id=run.shipment_id,
                cargo_class=req.cargo_class.value,
                origin_location=req.origin_location,
                temp_min_c=req.temp_min_c,
                temp_max_c=req.temp_max_c,
                max_budget=req.max_budget,
                deadline_minutes=req.deadline_minutes,
                required_capacity_kg=req.required_capacity_kg,
            )
            agent_ctx.logger.info(
                f"[Coordinator] Requesting quote from {carrier_id} at {address}"
            )
            await agent_ctx.send(address, msg)

        # Launch bounded timeout task in background to progress if slow carriers delay
        task = asyncio.create_task(self._quote_timeout_guard(agent_ctx, run_id))
        self.quote_guard_tasks[run_id] = task

    async def _quote_timeout_guard(self, agent_ctx: Context, run_id: str):
        """Advances workflow if quote collection window expires."""
        try:
            await asyncio.sleep(self.quote_timeout_seconds)
        except asyncio.CancelledError:
            return

        run = self.get_run(run_id)
        if run and run.current_state == WorkflowState.COLLECTING_QUOTES:
            agent_ctx.logger.info(
                f"[Coordinator] Quote collection window closed ({len(run.offers)} offers received). Evaluating candidates."
            )
            await self.evaluate_and_challenge_leading_candidate(agent_ctx, run_id)

    async def handle_quote_offer(self, agent_ctx: Context, msg: QuoteOfferMsg):
        """Collects incoming carrier offer with idempotency and duplicate prevention."""
        run = self.get_run(msg.run_id)
        if not run:
            return

        # Check in-memory deduplication
        if any(o.carrier_id == msg.carrier_id for o in run.offers):
            agent_ctx.logger.warning(
                f"[Coordinator] Duplicate quote from {msg.carrier_id} ignored for run {msg.run_id}"
            )
            audit_logger.log_event(
                "DUPLICATE_QUOTE_IGNORED",
                run_id=msg.run_id,
                role="coordinator",
                correlation_id=msg.correlation_id,
                details={"carrier_id": msg.carrier_id},
            )
            return

        offer = CarrierOffer(
            carrier_id=msg.carrier_id,
            carrier_name=msg.carrier_name,
            run_id=msg.run_id,
            price=msg.price,
            eta_minutes=msg.eta_minutes,
            capacity_kg=msg.capacity_kg,
            supported_temp_min_c=msg.supported_temp_min_c,
            supported_temp_max_c=msg.supported_temp_max_c,
            is_simulated=msg.is_simulated,
        )

        # Enforce SQLite idempotency with graceful fallback
        try:
            saved = self.storage.save_offer(offer)
            if not saved:
                agent_ctx.logger.warning(
                    f"[Coordinator] SQLite detected duplicate offer from {msg.carrier_id}"
                )
                return
        except Exception as exc:
            agent_ctx.logger.error(
                f"[Coordinator] SQLite error while saving offer from {msg.carrier_id}: {exc}"
            )

        run.offers.append(offer)
        agent_ctx.logger.info(
            f"[Coordinator] Recorded offer from {msg.carrier_id} (₹{msg.price:,.0f}). Total offers: {len(run.offers)}/{len(self.carrier_addresses)}"
        )
        audit_logger.log_event(
            "QUOTE_OFFER_RECORDED",
            run_id=msg.run_id,
            role="coordinator",
            correlation_id=msg.correlation_id,
            details={"carrier_id": msg.carrier_id, "price": msg.price, "eta_minutes": msg.eta_minutes},
        )

        # Trigger ranking immediately once all target carriers have submitted
        if len(run.offers) >= len(self.carrier_addresses) and run.current_state == WorkflowState.COLLECTING_QUOTES:
            guard_task = self.quote_guard_tasks.pop(run.run_id, None)
            if guard_task and not guard_task.done():
                guard_task.cancel()
            await self.evaluate_and_challenge_leading_candidate(agent_ctx, run.run_id)

    async def evaluate_and_challenge_leading_candidate(self, agent_ctx: Context, run_id: str):
        """Applies deterministic hard filter, ranks candidates, and challenges leading candidate."""
        run = self.get_run(run_id)
        if not run:
            return

        if run.current_state not in (WorkflowState.COLLECTING_QUOTES, WorkflowState.VERIFYING_NEXT):
            return

        if run.current_state == WorkflowState.COLLECTING_QUOTES:
            run.transition_to(WorkflowState.RANKING)
            self._sync_state(run)

        # Apply deterministic hard filter
        eligible, violations = filter_eligible_offers(
            request=run.request,
            offers=run.offers,
            excluded_candidate_ids=run.rejected_candidate_ids,
        )

        if not eligible:
            agent_ctx.logger.warning(
                f"[Coordinator] No eligible carrier offers remain for run {run_id}."
            )
            run.transition_to(WorkflowState.NO_SAFE_MATCH, reason="All candidates failed hard constraints")
            self._finalize_run(run)
            return

        # Deterministic ranking (price ASC, eta ASC)
        ranked = rank_carrier_offers(eligible)
        run.ranked_candidates = ranked
        run.active_candidate_index = 0

        # Challenge top candidate
        await self._challenge_current_candidate(agent_ctx, run)

    async def _challenge_current_candidate(self, agent_ctx: Context, run: RunContext):
        candidate = run.get_next_candidate()
        if not candidate:
            run.transition_to(WorkflowState.NO_SAFE_MATCH, reason="Exhausted feasible candidates")
            self._finalize_run(run)
            return

        run.audit_attempts += 1
        if run.audit_attempts > self.max_audit_attempts:
            run.transition_to(
                WorkflowState.NO_SAFE_MATCH,
                reason=f"Reached maximum audit attempts ({self.max_audit_attempts})",
            )
            self._finalize_run(run)
            return

        run.transition_to(
            WorkflowState.VERIFYING_CANDIDATE,
            reason=f"Audit attempt #{run.audit_attempts} for candidate '{candidate.carrier_id}'",
        )
        self._sync_state(run)

        nonce = f"nonce_{uuid4().hex[:16]}"
        challenge = AuditChallengeMsg(
            run_id=run.run_id,
            candidate_id=candidate.carrier_id,
            nonce=nonce,
            target_temp_min_c=run.request.temp_min_c,
            target_temp_max_c=run.request.temp_max_c,
        )

        agent_ctx.logger.info(
            f"[Coordinator] Challenging '{candidate.carrier_id}' at auditor {self.auditor_address} (nonce={nonce})"
        )
        audit_logger.log_event(
            "AUDIT_CHALLENGE_DISPATCHED",
            run_id=run.run_id,
            role="coordinator",
            correlation_id=challenge.correlation_id,
            details={"candidate_id": candidate.carrier_id, "nonce": nonce},
        )
        await agent_ctx.send(self.auditor_address, challenge)

    async def handle_audit_verdict(self, agent_ctx: Context, msg: AuditVerdictMsg):
        """Processes audit verdict with idempotency; if FAIL, executes bounded failover."""
        run = self.get_run(msg.run_id)
        if not run:
            return

        # Duplicate verdict handling
        if msg.candidate_id in run.audit_verdicts:
            agent_ctx.logger.warning(
                f"[Coordinator] Duplicate audit verdict for '{msg.candidate_id}' in run {msg.run_id} ignored"
            )
            return

        verdict = AuditVerdict(
            candidate_id=msg.candidate_id,
            run_id=msg.run_id,
            nonce=msg.nonce,
            passed=msg.passed,
            reason=msg.reason,
            recorded_temp_c=msg.recorded_temp_c,
            compressor_active=msg.compressor_active,
            signature_valid=msg.signature_valid,
            timestamp_fresh=True,
        )
        run.audit_verdicts[msg.candidate_id] = verdict
        try:
            self.storage.save_audit_verdict(verdict)
        except Exception as exc:
            agent_ctx.logger.error(f"[Coordinator] Failed to persist audit verdict: {exc}")

        agent_ctx.logger.info(
            f"[Coordinator] Received verdict for '{msg.candidate_id}': {'PASS' if msg.passed else 'FAIL'}"
        )
        audit_logger.log_event(
            "AUDIT_VERDICT_RECEIVED",
            run_id=msg.run_id,
            role="coordinator",
            correlation_id=msg.correlation_id,
            details={"candidate_id": msg.candidate_id, "passed": msg.passed, "reason": msg.reason},
        )

        if msg.passed:
            # Candidate passed! Award recommendation
            run.selected_offer = next(
                (o for o in run.offers if o.carrier_id == msg.candidate_id), None
            )
            run.transition_to(
                WorkflowState.RECOMMENDED_FOR_CONFIRMATION,
                reason=f"Candidate '{msg.candidate_id}' verified safe: {msg.reason}",
            )
            self._finalize_run(run)
        else:
            # Candidate failed! Record rejection and failover
            run.record_rejected_candidate(msg.candidate_id, msg.reason)
            run.transition_to(
                WorkflowState.CANDIDATE_REJECTED,
                reason=f"Candidate '{msg.candidate_id}' failed audit: {msg.reason}",
            )
            self._sync_state(run)

            # Check if further candidates or retry budget remains
            if run.audit_attempts >= self.max_audit_attempts:
                run.transition_to(
                    WorkflowState.NO_SAFE_MATCH,
                    reason=f"Maximum audit attempts ({self.max_audit_attempts}) exceeded",
                )
                self._finalize_run(run)
            else:
                run.transition_to(WorkflowState.VERIFYING_NEXT, reason="Evaluating next ranked candidate")
                self._sync_state(run)
                await self._challenge_current_candidate(agent_ctx, run)

    def _sync_state(self, run: RunContext):
        """Syncs in-flight state and latest audit events to SQLite."""
        try:
            self.storage.update_run_state(
                run_id=run.run_id,
                state=run.current_state.value,
                selected_carrier_id=run.selected_offer.carrier_id if run.selected_offer else None,
                retry_count=run.audit_attempts,
            )
            if run.audit_trail:
                self.storage.save_audit_event(run.audit_trail[-1])
        except Exception as exc:
            audit_logger.log_event(
                "STORAGE_SYNC_ERROR",
                run_id=run.run_id,
                role="coordinator",
                details={"error": str(exc)},
            )

    def _finalize_run(self, run: RunContext):
        """Finalizes run in database and notifies completion event."""
        terminal_result = run.build_terminal_result()
        try:
            self.storage.update_run_state(
                run_id=run.run_id,
                state=run.current_state.value,
                selected_carrier_id=terminal_result.selected_carrier_id,
                retry_count=run.audit_attempts,
                terminal_result=terminal_result,
            )
            if run.audit_trail:
                self.storage.save_audit_event(run.audit_trail[-1])
        except Exception as exc:
            audit_logger.log_event(
                "STORAGE_FINALIZE_ERROR",
                run_id=run.run_id,
                role="coordinator",
                details={"error": str(exc)},
            )

        audit_logger.log_event(
            "MISSION_FINALIZED",
            run_id=run.run_id,
            role="coordinator",
            details={
                "terminal_state": run.current_state.value,
                "selected_carrier": terminal_result.selected_carrier_id,
                "rejections": list(run.rejected_candidate_ids),
            },
        )
        if run.run_id in self.completion_events:
            self.completion_events[run.run_id].set()


def create_coordinator_protocol(orchestrator: CoordinatorOrchestrator) -> Protocol:
    """Builds the internal communication protocol for the coordinator."""
    proto = Protocol(name="CoordinatorInternalProtocol", version="1.0.0")

    @proto.on_message(model=QuoteOfferMsg)
    async def on_quote_offer(ctx: Context, sender: str, msg: QuoteOfferMsg):
        await orchestrator.handle_quote_offer(ctx, msg)

    @proto.on_message(model=AuditVerdictMsg)
    async def on_audit_verdict(ctx: Context, sender: str, msg: AuditVerdictMsg):
        await orchestrator.handle_audit_verdict(ctx, msg)

    return proto


def create_coordinator_agent(
    orchestrator: CoordinatorOrchestrator,
    seed: Optional[str] = None,
    port: Optional[int] = None,
    include_chat: bool = True,
    mailbox: bool = False,
    endpoint: Optional[Any] = None,
    report_events: bool = False,
) -> Agent:
    """Instantiates the primary Recovery Coordinator agent."""
    if endpoint is not None:
        agent_endpoint = endpoint
    elif mailbox:
        agent_endpoint = None
    elif port:
        agent_endpoint = [f"http://127.0.0.1:{port}/submit"]
    else:
        agent_endpoint = None

    agent = Agent(
        name="recovery_coordinator",
        seed=seed or "seed_recovery_coordinator_primary_demo",
        port=port,
        endpoint=agent_endpoint,
        mailbox=mailbox,
        handle_messages_concurrently=True,
        report_events=report_events,
    )
    internal_proto = create_coordinator_protocol(orchestrator)
    agent.include(internal_proto)

    if include_chat:
        from freightpact.chat_interface import create_acp_chat_protocol
        chat_proto = create_acp_chat_protocol(orchestrator)
        agent.include(chat_proto)

    return agent

