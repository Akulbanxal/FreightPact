# FreightPact — Architecture Decisions (DECISIONS.md)

This document records the architectural choices, trade-offs, and explicit simulation boundaries adopted for FreightPact.

---

## 1. Decision Records

### ADR-01: Framework Selection — Python uAgents & ACP
- **Context:** Fetch-A-Thon 3.0 requires agents to be registered on Agentverse and discoverable through ASI:One via the Agent Chat Protocol (ACP).
- **Decision:** Build FreightPact using Fetch.ai's official `uagents` (>=0.25.0) framework and `uagents_core.contrib.protocols.chat`.
- **Rationale:** 
  - Direct native support for Agentverse mailbox registration and cryptographic agent addressing.
  - Full compatibility with ASI:One's chat protocol specification and `MetadataContent` interactive cards.
  - Native Python async runtime without requiring Docker, Bun, or external node processes.
- **Alternatives Considered:** 
  - CrewAI / LangGraph with an ACP bridge: Rejected due to unnecessary wrapper complexity and risk of protocol desynchronization.

### ADR-02: Protocol Boundaries — External ACP vs Internal Multi-Agent Messages
- **Context:** The hackathon requires primary user interaction via ASI:One, while the core problem involves multi-agent coordination between coordinator, carriers, and auditor.
- **Decision:** Use official ACP (`ChatMessage`, `TextContent`, `MetadataContent`) strictly for the Coordinator's external communication with ASI:One / Agentverse. Use structured `uagents.Model` protocols for internal agent-to-agent communication (Coordinator ↔ Carrier agents and Coordinator ↔ Auditor agent).
- **Rationale:** 
  - Avoids shoehorning internal machine-to-machine structured queries into conversational chat envelopes.
  - Accurately reports internal architecture without falsely claiming internal messages are ACP.

### ADR-03: Deterministic Constraints & Ranking Policy
- **Context:** Deciding which carrier to select must be auditable and reliable in emergency logistics.
- **Decision:** Implement constraint validation and candidate ranking as pure Python functions rather than LLM prompts or complex auction algorithms.
- **Rules:**
  1. **Hard Filter:** Exclude candidates where `quote_price > max_budget`, `eta_minutes > deadline_minutes`, `capacity_kg < required_capacity`, or supported temperature band does not cover target range.
  2. **Ranking:** Sort remaining eligible candidates by lowest price first; use shortest ETA as tie-breaker.
- **Rationale:** Cold-chain logistics requires deterministic, reproducible guarantees. LLM hallucinations in threshold filtering are unacceptable in safety-critical logistics.

### ADR-04: Cryptographic Telemetry Verification
- **Context:** A carrier can claim any temperature or availability. We need independent verification before awarding a contract.
- **Decision:** Telemetry verification is executed by an independent Telemetry Auditor agent using HMAC-SHA256 signatures with nonce challenge and timestamp freshness checks.
- **Mechanism:**
  - Coordinator sends a verification challenge with a fresh cryptographically random nonce (`uuid4()`).
  - Auditor retrieves candidate telemetry fixture and verifies:
    - Signature validity (`hmac.compare_digest`).
    - Nonce matches fresh challenge (replay protection).
    - Timestamp is within acceptable freshness window (e.g., < 300 seconds).
    - Reefer box temperature is within target range (+2°C to +8°C).
    - Compressor status is `ACTIVE`.
- **Trade-off:** In this hackathon MVP, the telemetry is a synthetic fixture signed with a shared fixture secret, not a physical hardware HSM or live CAN bus IoT feed. This boundary is clearly labelled.

### ADR-05: ASI:One Interactive Cards & Plain-Text Fallback
- **Context:** ASI:One supports Interactive Cards via `MetadataContent` in ACP. Some clients or CLI tests do not render rich cards.
- **Decision:** Renders interactive cards using verified Agentverse schemas:
  - `MetadataContent` with `card_protocol_version: "1"`, `requires_card_interaction: "true"`, and standard kinds (`review`, `detail`, `carousel`).
  - Always pair every `MetadataContent` block with a formatted `TextContent` block providing complete markdown narration.
- **Rationale:** Ensures seamless accessibility in rich ASI:One clients while maintaining 100% usability in text-only clients, curl tests, or CLI testbeds.

### ADR-06: Bounded Failover & Terminal States
- **Context:** If the top-ranked candidate fails verification, the coordinator must not freeze or enter an infinite loop.
- **Decision:** Enforce a maximum audit attempt limit (`MAX_AUDIT_ATTEMPTS = 3`).
- **Behavior:**
  - If Candidate 1 (Beta) fails audit -> status `CANDIDATE_REJECTED` -> state transitions to `VERIFYING_NEXT`.
  - Candidate 1 is permanently added to the rejected set for that run ID and excluded from reconsideration.
  - Candidate 2 (Alpha) is evaluated. If Alpha passes -> state transitions to `RECOMMENDED_FOR_CONFIRMATION`.
  - If all candidates are exhausted or fail -> state transitions to `NO_SAFE_MATCH`.
  - Runs are strictly isolated by unique `run_id` so rejection state does not leak into future runs.

### ADR-08: Untrusted Client Action Validation & Parameter Integrity
- **Context:** User card interactions in ASI:One or chat clients return actions (`confirm_award`, `reject_award`) with candidate parameters (`carrier_id`, `run_id`). A malicious or compromised client could attempt parameter tampering (e.g., confirming an unverified or rejected carrier, or confirming a run that has not completed verification).
- **Decision:** Validate all interactive card actions strictly on the server:
  1. The target `run_id` must exist and be in state `RECOMMENDED_FOR_CONFIRMATION`.
  2. The target `carrier_id` must strictly match the run's cryptographically audited `selected_offer.carrier_id`. Any mismatch is rejected with a security alert.
  3. Actions on completed or failed runs are rejected.
- **Rationale:** Treats all client-returned card selections as untrusted external input.

---

## 2. Explicit Simulation Boundaries & MVP Disclosures

> [!CAUTION]
> **Production Readiness Notice:**
> FreightPact is a verified **Hackathon Proof-of-Concept / MVP** demonstrating autonomous multi-agent verification and failover on Fetch.ai uAgents. It is **NOT production-ready for real commercial pharmaceutical cold-chain logistics** without independent integration with live telematics hardware (BLE/CAN bus), enterprise TMS/ERP systems, carrier contractual liability agreements, and hardware secure module (HSM) attestation.

| Feature | Production Reality | FreightPact MVP Implementation | Reason for MVP Choice |
|---|---|---|---|
| **Carrier Quotes** | Real dynamic marketplace API or TMS bidding | Deterministic scenario fixtures (Beta ₹38k, Alpha ₹41.5k, Gamma ₹43k) | Predictable, reproducible evaluation of failover logic |
| **Telemetry Sensor Source** | Physical IoT sensors (e.g., TempTale, BLE, CAN bus) | Deterministic synthetic sensor payloads with realistic drift | Eliminates dependency on physical cold-storage hardware |
| **Telemetry Authenticity** | Hardware Secure Element / TPM asymmetric signing | HMAC-SHA256 symmetric signature using environment secret | Demonstrates genuine cryptographic audit without proprietary hardware |
| **ETA & Routing** | Live Google Maps / HERE routing API with traffic | Deterministic scenario ETA values (32m, 38m, 40m) | Eliminates external rate limits and flaky API failures during judging |
| **Dispatch & Settlement** | Legal carrier contract, EDI dispatch, banking settlement | Operational recommendation awaiting user confirmation | Hackathon scope boundary: avoids claiming false transactions or unverified live dispatch |
