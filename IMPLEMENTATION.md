# FreightPact — Implementation Guide

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

**Project:** FreightPact — Autonomous Cold-Chain Shipment Recovery Coordinator  
**Event:** Fetch-A-Thon 3.0 (Fetch.ai Innovation Lab)  
**Living Document:** This guide is continuously kept in sync with the codebase as tasks are implemented and tested.

---

## 1. Executive Summary & Problem Statement

In cold-chain logistics (pharmaceuticals, vaccines, biologics), a refrigerated vehicle breakdown presents an immediate spoilage risk. A recovery dispatcher must secure replacement transport under strict constraints:
- Specific temperature band (e.g., +2°C to +8°C).
- Tight arrival deadline (e.g., within 45 minutes).
- Fixed incident budget ceiling.
- Verified physical equipment readiness (active cooling compressor, verified sensor integrity).

### The FreightPact Innovation
Existing systems either rely on manual phone calls or blindly accept the cheapest digital marketplace quote without verifying physical readiness. In cold chain, a quote without verified telemetry is a disaster waiting to happen.

FreightPact implements **Verification-Gated Selection with Bounded Automatic Failover**:
1. Collects candidate quotes from independent carrier agents.
2. Applies deterministic hard constraints (budget, deadline, capacity, temperature range).
3. Ranks candidates deterministically (lowest price first, ETA as tie-breaker).
4. Submits a fresh cryptographic challenge (nonce + run ID) to an independent Telemetry Auditor before awarding the contract.
5. If the leading candidate fails auditor checks (e.g., carrier quoting low price but vehicle reefer box at 13.8°C with compressor inactive), the candidate is recorded as rejected for that specific run and excluded from reconsideration.
6. The orchestrator automatically evaluates the next feasible candidate (e.g., candidate with 4.2°C and active cooling) within a bounded retry limit.
7. Outputs an auditable recommendation trail natively inside ASI:One with Interactive Cards and a markdown/text fallback.

---

## 2. Technical Prerequisites & Verified Environment

### Python Environment
- **Target Python Version:** Python 3.10+ (Recommended: Python 3.11 or 3.12).
- **Core Framework:** `uagents` (>=0.25.0, current PyPI release 0.26.1).
- **Protocol Package:** `uagents-core` (contains `uagents_core.contrib.protocols.chat`).
- **Validation & Models:** `pydantic` (v2).
- **Environment Management:** `python-dotenv`.
- **Testing:** `pytest` (>=8.0.0), `pytest-asyncio`.

### Host Environment Notes
- **Operating System:** macOS / Linux.
- **Constraints:** No Docker, no Bun, no external frontend framework. The primary user interface is native ASI:One.
- **Execution Mode:** Local Python virtual environment (`.venv`) using `pip`.

---

## 3. Architecture & Protocol Boundaries

FreightPact maintains a strict separation between external discovery/chat protocols and internal multi-agent coordination:

```
[ ASI:One / Agentverse User ]
              │
              │ ACP (Agent Chat Protocol)
              │ TextContent + MetadataContent (Interactive Cards)
              ▼
  ┌─────────────────────────────────────────────────────────┐
  │   FreightPact Recovery Coordinator (Primary Agent)      │
  │   - Registered on Agentverse with mailbox/endpoint       │
  │   - Owns workflow state machine & run persistence       │
  │   - Deterministic constraint filter & candidate ranking │
  └───────────────┬─────────────────────────┬───────────────┘
                  │                         │
      Internal uAgents Protocol             │ Internal uAgents Protocol
      (QuoteRequest / QuoteOffer)           │ (AuditChallenge / AuditVerdict)
                  │                         │
                  ▼                         ▼
   ┌─────────────────────────────┐   ┌─────────────────────────────┐
   │ Carrier Agents              │   │ Independent Telemetry       │
   │ - Beta Carrier (Cheapest,   │   │ Auditor Agent               │
   │   compromised unit)         │   │ - Fresh nonce verification  │
   │ - Alpha Carrier (Verified,  │   │ - Cryptographic HMAC check  │
   │   safe unit)                │   │ - Telemetry constraint rule │
   │ - Gamma Carrier (Backup)    │   │ - Signed synthetic payload  │
   └─────────────────────────────┘   └─────────────────────────────┘
```

1. **External Chat Interface (ACP):**
   - Implements `chat_protocol_spec` from `uagents_core.contrib.protocols.chat`.
   - Handles `ChatMessage`, `TextContent`, `StartSessionContent`, `EndSessionContent`, and `ChatAcknowledgement`.
   - Renders interactive cards using `MetadataContent` with:
     - `card_protocol_version: "1"`
     - `requires_card_interaction: "true"`
     - `card_kind: "review"` / `"detail"` / `"carousel"` / `"form"`
     - `card_payload`: JSON stringified payload (< 64 KB).
   - Provides a plain-text markdown fallback for clients without card rendering support.

2. **Internal Multi-Agent Communication:**
   - Internal agents communicate using typed `uagents.Model` schemas.
   - Does **not** pretend internal messages are ACP; they are structured agent-to-agent protocol messages with correlation IDs.

3. **Deterministic Policy Module:**
   - Pure Python business logic handles filtering and ranking.
   - LLMs are never used for critical numeric threshold validation or cryptographic comparisons.

---

## 4. Repository Structure

```text
FreightPact/
├── README.md                          # Project overview, badges, addresses, quickstart
├── IMPLEMENTATION.md                  # This living guide
├── TASKS.md                           # Phased task list with completion status
├── DECISIONS.md                       # Architectural decisions and explicit simulation boundaries
├── requirements.txt                   # Pinned compatible Python packages
├── .env.example                       # Environment template with placeholders
├── .gitignore                         # Python/IDE ignore rules
├── src/
│   └── freightpact/
│       ├── __init__.py                # Package version and exports
│       ├── config.py                  # Pydantic Settings / env validation
│       ├── models.py                  # Typed domain models (Request, Offer, Telemetry, Verdict)
│       ├── states.py                  # Workflow state machine and transitions
│       ├── policy.py                  # Hard filters and deterministic checks
│       ├── ranking.py                 # Deterministic candidate ranking rule
│       ├── telemetry.py               # Telemetry verification and HMAC signature check
│       ├── fixtures.py                # Synthetic demo carriers and sensor fixtures (labelled)
│       ├── storage.py                 # SQLite run persistence and idempotency
│       ├── audit_log.py               # Structured audit event logger
│       ├── cards.py                   # ASI:One Interactive Card builder & text fallback
│       ├── coordinator_agent.py       # Primary ACP agent orchestrator
│       ├── carrier_agent.py           # Independent carrier worker agents
│       └── auditor_agent.py           # Independent auditor worker agent
├── tests/
│   ├── __init__.py
│   ├── test_config.py                 # Environment validation tests
│   ├── test_models.py                 # Pydantic schema validation tests
│   ├── test_policy.py                 # Hard constraint filtering tests
│   ├── test_ranking.py                # Deterministic sorting and tie-breaker tests
│   ├── test_telemetry.py              # HMAC signature, nonce replay, freshness tests
│   ├── test_state_machine.py          # State transitions and run isolation tests
│   ├── test_storage.py                # Persistence, duplicate messages, and idempotency tests
│   ├── test_cards.py                  # Interactive card schema and text fallback tests
│   └── test_end_to_end.py             # Multi-agent mock/local integration flow
└── docs/
    ├── ARCHITECTURE.md                # Sequence diagrams & state machine specification
    ├── AGENTVERSE_SETUP.md            # Manual guide for Agentverse registration & mailbox
    ├── DEMO_SCRIPT.md                 # 3-5 minute demo script for presentation/video
    ├── SUBMISSION_CHECKLIST.md        # Matrix matching hackpack requirements to evidence
    └── TROUBLESHOOTING.md             # Common errors and resolutions
```

---

## 5. Environment Variables & Configuration

All configurations are read from environment variables or a local `.env` file. Secrets are never hardcoded or committed to git.

| Variable Name | Required | Default | Purpose |
|---|---|---|---|
| `FREIGHTPACT_ENV` | No | `development` | Environment mode (`development`, `test`, `production`) |
| `COORDINATOR_SEED` | Yes (for deployment) | None | Stable secret seed phrase for the primary coordinator agent |
| `CARRIER_ALPHA_SEED` | No | None | Secret seed phrase for Carrier Alpha agent |
| `CARRIER_BETA_SEED` | No | None | Secret seed phrase for Carrier Beta agent |
| `CARRIER_GAMMA_SEED` | No | None | Secret seed phrase for Carrier Gamma agent |
| `AUDITOR_SEED` | No | None | Secret seed phrase for Telemetry Auditor agent |
| `AGENTVERSE_API_KEY` | No | None | Agentverse API token for mailbox registration |
| `ASI_API_KEY` | No | None | ASI:One API key for conversational intent parsing |
| `HMAC_FIXTURE_SECRET` | Yes | None | Symmetric secret key for signing demo telemetry payloads |
| `DATABASE_PATH` | No | `freightpact.db` | Local SQLite database filepath |
| `MAX_AUDIT_ATTEMPTS` | No | `3` | Maximum carrier candidates to audit before terminal `NO_SAFE_MATCH` |
| `AGENT_PORT_START` | No | `8000` | Base local port for agent HTTP communication |

---

## 6. Implementation Phases
- **Phase 0:** Requirements research, SDK verification, and baseline architectural documentation. *(Completed)*
- **Phase 1:** Minimal runnable Python foundation (config loading, pinned dependencies, tests). *(Completed & Verified)*
- **Phase 2:** Pure domain models, deterministic constraint filtering, ranking, and state machine. *(Completed & Verified)*
- **Phase 3:** Cryptographic telemetry fixture (HMAC signing, freshness checks, nonce replay protection). *(Completed & Verified)*
- **Phase 4:** Local multi-agent message exchange (Coordinator, Carrier workers, Auditor). *(Completed & Verified)*
- **Phase 5:** Persistence, idempotency, audit trail, and robust failure recovery. *(Completed & Verified)*
- **Phase 6:** User-facing ACP interface (Agent Chat Protocol with text fallback). *(Completed & Verified)*
- **Phase 7:** ASI:One Interactive Cards (`MetadataContent` builder for intake form, carousel comparison, telemetry failure detail, and final review confirmation). *(Completed & Verified with 61 tests)*
- **Phase 8:** End-to-end local simulation and verification tests. *(Completed & Verified with 63 tests; standalone runner `python -m freightpact.demo`)*
- **Phase 9:** Agentverse registration and ASI:One live onboarding documentation. *(Documentation updated in docs/AGENTVERSE_SETUP.md)*
- **Phase 10:** Security, privacy, and claims audit. *(Completed & Verified with 70 tests in `tests/test_security_audit.py`; Live Agentverse & ASI:One web verification marked PENDING)*
- **Phase 11:** UI / card presentation polish.
- **Phase 12:** README, demo script, and hackpack submission pack.
- **Phase 13:** Final judge acceptance audit.
- **Phase 14:** Optional Payment Protocol assessment.

---

## 7. Submission & Verification Checklist

- [ ] At least one agent registered on Agentverse.
- [ ] Agent Chat Protocol (ACP) verified and functional.
- [ ] Primary workflow completely operable inside ASI:One.
- [ ] Real multi-agent message coordination (Coordinator ↔ Carriers ↔ Auditor).
- [ ] Realistic failure recovery demonstrated (cheapest candidate Beta rejected for bad telemetry; Alpha selected).
- [ ] Interactive Cards render in ASI:One with graceful text fallback.
- [ ] No hardcoded keys, seeds, or fake live tracking claims.
- [ ] Public GitHub repository with badges:
  ```markdown
  ![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
  ![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)
  ```
- [ ] Deliverables: Public ASI:One Shared Chat URL, Agentverse Profile URL, Public Repo URL, 3–5 min Demo Video.

---

## 8. Explicit MVP Limitations & Simulation Disclosures

> [!CAUTION]
> **Production Readiness Notice:**
> FreightPact is a verified **Hackathon Proof-of-Concept / MVP** for Fetch-A-Thon 3.0. It is **NOT production-ready for real commercial pharmaceutical logistics** without independently audited enterprise integrations, live telematics hardware (BLE/CAN bus), legal SLA contracts, and hardware security module (HSM) attestation.

To ensure transparency for judges and users:
1. **Quotes & Availability:** Demo carrier quotes (Beta ₹38,000 / 32m, Alpha ₹41,500 / 38m, Gamma ₹43,000 / 40m) are deterministic synthetic scenario fixtures. Sources are labelled as `SIMULATED_FIXTURE:{carrier_id}` in all data structures.
2. **Telemetry Payloads:** Cold-chain temperature data, compressor states, and timestamps are generated from synthetic sensor fixtures.
3. **Cryptographic Signatures:** Payload signatures are generated via HMAC-SHA256 with a fixture secret loaded from environment; they verify authenticity relative to the demo signing key, not physical hardware attestation or hardware security modules (HSM).
4. **Freshness & Nonce Replay:** Auditing verifies payload timestamp freshness within a 300-second window and enforces single-use challenge nonces via `NonceReplayTracker` per `run_id`.
5. **No Blind Fallbacks:** Unknown carrier IDs return `None` and are never defaulted to Carrier Alpha.
6. **Dispatch & Settlement:** The final output is an operational recommendation sandbox award awaiting operator confirmation; no real physical trucks are dispatched, and no live payment/escrow takes place in the MVP.
