"""Independent Telemetry Auditor Agent factory and verification handlers."""

from __future__ import annotations

from typing import Optional
from uagents import Agent, Context, Protocol
from freightpact.fixtures import generate_telemetry_payload
from freightpact.protocols import AuditChallengeMsg, AuditVerdictMsg
from freightpact.telemetry import NonceReplayTracker, verify_telemetry_payload


def create_auditor_protocol(secret_key: str) -> Protocol:
    """Creates the auditor protocol using the cryptographic fixture secret."""
    proto = Protocol(name="TelemetryAuditorProtocol", version="1.0.0")
    nonce_tracker = NonceReplayTracker()

    @proto.on_message(model=AuditChallengeMsg, replies=AuditVerdictMsg)
    async def handle_audit_challenge(ctx: Context, sender: str, msg: AuditChallengeMsg):
        ctx.logger.info(
            f"[Auditor] Received verification challenge for '{msg.candidate_id}' in run {msg.run_id}"
        )

        # Independently fetch the candidate's sensor telemetry payload from the fixture
        payload = generate_telemetry_payload(
            candidate_id=msg.candidate_id,
            run_id=msg.run_id,
            nonce=msg.nonce,
            secret_key=secret_key,
        )

        if not payload:
            verdict_msg = AuditVerdictMsg(
                correlation_id=msg.correlation_id,
                run_id=msg.run_id,
                candidate_id=msg.candidate_id,
                nonce=msg.nonce,
                passed=False,
                reason=f"Candidate '{msg.candidate_id}' has no registered equipment telemetry stream",
                recorded_temp_c=-999.0,
                compressor_active=False,
                signature_valid=False,
            )
            await ctx.send(sender, verdict_msg)
            return

        # Perform strict cryptographic and cold-chain verification
        verdict = verify_telemetry_payload(
            payload=payload,
            expected_candidate_id=msg.candidate_id,
            expected_run_id=msg.run_id,
            expected_nonce=msg.nonce,
            target_temp_min_c=msg.target_temp_min_c,
            target_temp_max_c=msg.target_temp_max_c,
            secret_key=secret_key,
            nonce_tracker=nonce_tracker,
        )

        verdict_msg = AuditVerdictMsg(
            correlation_id=msg.correlation_id,
            run_id=msg.run_id,
            candidate_id=msg.candidate_id,
            nonce=msg.nonce,
            passed=verdict.passed,
            reason=verdict.reason,
            recorded_temp_c=verdict.recorded_temp_c,
            compressor_active=verdict.compressor_active,
            signature_valid=verdict.signature_valid,
        )

        ctx.logger.info(
            f"[Auditor] Rendered verdict for '{msg.candidate_id}': "
            f"{'PASS' if verdict.passed else 'FAIL'} ({verdict.reason})"
        )
        await ctx.send(sender, verdict_msg)

    return proto


def create_auditor_agent(
    secret_key: str,
    seed: Optional[str] = None,
    port: Optional[int] = None,
    enable_agent_inspector: bool = True,
) -> Agent:
    """Instantiates an independent Telemetry Auditor agent."""
    agent = Agent(
        name="telemetry_auditor",
        seed=seed or "seed_telemetry_auditor_verification_demo",
        port=port,
        endpoint=[f"http://127.0.0.1:{port}/submit"] if port else None,
        enable_agent_inspector=enable_agent_inspector,
    )

    proto = create_auditor_protocol(secret_key)
    agent.include(proto)
    return agent
