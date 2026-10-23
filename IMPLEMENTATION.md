# FreightPact — Implementation Guide

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

**Project:** FreightPact — Autonomous Cold-Chain Shipment Recovery Coordinator  
**Event:** Fetch-A-Thon 3.0 (Fetch.ai Innovation Lab)  
**Living Document:** This guide is continuously kept in sync with the codebase as tasks are implemented and tested.

---

## 1. Executive Summary & Problem Statement

In cold-chain logistics (pharmaceuticals, vaccines, biologics), a refrigerated vehicle breakdown presents an immediate spoilage risk. A recovery dispatcher must secure replacement transport under strict constraints:
- Specific temperature band (e.g., +2.0°C to +8.0°C for GDP compliance).
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
- **Target Python Version:** Python 3.10+ (Tested and verified on Python 3.13 on Windows 11).
- **Core Framework:** `uagents` (pinned 0.20.1 in `requirements.txt`).
- **Protocol Package:** `uagents-core` (contains `uagents_core.contrib.protocols.chat`).
- **Validation & Models:** `pydantic` (v2).
- **Environment Management:** `python-dotenv`.
- **Testing:** `pytest` (>=8.0.0), `pytest-asyncio`.

### Host Environment Notes
- **Operating System:** Windows, macOS, or Linux.
- **Constraints:** No Docker, no Bun, no external frontend framework required. The primary user interface is native ASI:One via ACP.
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
  │   - Registered on Agentverse with Mailbox runner         │
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
     - Predefined schema kinds: `review`, `detail`, `carousel`, `form`.
     - JSON-stringified `card_payload` (< 64 KB, max depth < 8).
   - Provides a plain-text markdown fallback for clients without card rendering support.

2. **Internal Multi-Agent Communication:**
   - Internal agents communicate using typed `uagents.Model` schemas.
   - Does **not** pretend internal messages are ACP; they are structured agent-to-agent protocol messages with correlation IDs.

3. **Deterministic Policy Module:**
   - Pure Python business logic handles filtering and ranking.
   - LLMs are never used for critical numeric threshold validation or cryptographic comparisons.

---

## 4. Agent Identities & Deployment Topology

| Agent Role | Agent Identifier | Deterministic Address | Deployment Topology |
|---|---|---|---|
| **Recovery Coordinator** | `recovery_coordinator` | `agent1q23pcy5gumdt6jv7985mnwtqwca9j2evj5qm0y3q76p6e4t7hyxdcsphnm3` | **Agentverse Mailbox** (local process polling Agentverse) |
| **Carrier Alpha** | `carrier_alpha` | `agent1qtlq065203362a937c4v4pkhgwhgfl5q540y9j6drl7yfl7m3u0v2z9yv2g` | Local in-process Bureau worker |
| **Carrier Beta** | `carrier_beta` | `agent1qvd87p2583g35q6j4s0j6v477q07p0u80u28k820v45w09r5q6j2q5m929s` | Local in-process Bureau worker |
| **Carrier Gamma** | `carrier_gamma` | `agent1qwn096203362a937c4v4pkhgwhgfl5q540y9j6drl7yfl7m3u0v2w84v5h` | Local in-process Bureau worker |
| **Telemetry Auditor** | `telemetry_auditor` | `agent1qd640jsaac95j6vrjlswwqs8gw65dp9p65g3rln483zw50pl03z5gj7t684` | Local in-process Bureau worker |

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
| `HMAC_FIXTURE_SECRET` | Yes | None | Symmetric secret key for signing demo telemetry payloads |
| `DATABASE_PATH` | No | `freightpact.db` | Local SQLite database filepath |
| `MAX_AUDIT_ATTEMPTS` | No | `3` | Maximum carrier candidates to audit before terminal `NO_SAFE_MATCH` |
| `AGENT_PORT_START` | No | `8050` | Base local port for agent HTTP communication |

---

## 6. Implementation Phases & Verification Milestones

- **Phase 0:** Requirements research, SDK verification, and baseline architectural documentation. *(Completed)*
- **Phase 1:** Minimal runnable Python foundation (config loading, pinned dependencies, tests). *(Completed & Verified)*
- **Phase 2:** Pure domain models, deterministic constraint filtering, ranking, and state machine. *(Completed & Verified)*
- **Phase 3:** Cryptographic telemetry fixture (HMAC signing, freshness checks, nonce replay protection). *(Completed & Verified)*
- **Phase 4:** Local multi-agent message exchange (Coordinator, Carrier workers, Auditor). *(Completed & Verified)*
- **Phase 5:** Persistence, idempotency, audit trail, and robust failure recovery. *(Completed & Verified)*
- **Phase 6:** User-facing ACP interface (Agent Chat Protocol with text fallback). *(Completed & Verified)*
- **Phase 7:** ASI:One Interactive Cards (`MetadataContent` builder for intake form, carousel comparison, telemetry failure detail, and final review confirmation). *(Completed & Verified)*
- **Phase 8:** End-to-end local simulation and verification tests. *(Completed & Verified; standalone runner `python -m freightpact.demo`)*
- **Phase 9:** Agentverse registration and ASI:One live onboarding documentation. *(Documented in `docs/AGENTVERSE_SETUP.md`)*
- **Phase 10:** Security, privacy, and claims audit. *(Completed & Verified in `tests/test_security_audit.py`)*
- **Phase 11:** Interactive Card visual polish with universal predefined schemas and non-recursive overview flow. *(Completed & Verified)*
- **Phase 12:** Historical Research Dataset Validation engine auditing 526 Nepal vaccine carrier logger files from Mendeley Data (CC BY 4.0). *(Completed & Verified in `tests/test_historical_validator.py` and `tools/validate_nepal_thermal.py`)*
- **Phase 13:** README overhaul, submission pack readiness, and test verification (98 passing tests). *(Completed & Verified)*

---

## 7. Submission & Verification Checklist

- [x] Primary Coordinator Agent created and address documented (`agent1q23pcy5gumdt6jv7985mnwtqwca9j2evj5qm0y3q76p6e4t7hyxdcsphnm3`).
- [x] Agent Chat Protocol (ACP) verified and functional.
- [x] Primary workflow completely operable inside ASI:One.
- [x] Real multi-agent message coordination (Coordinator ↔ Carriers ↔ Auditor).
- [x] Realistic failure recovery demonstrated (cheapest candidate Beta rejected for bad telemetry; Alpha selected).
- [x] Interactive Cards render in ASI:One with graceful text fallback.
- [x] No hardcoded keys, seeds, or fake live tracking claims.
- [x] 98 automated tests passing across 14 test modules.
- [x] Real research dataset validation integrated (Nepal thermal study, DOI: 10.17632/j277dvs69g.1, CC BY 4.0).
- [x] Public GitHub repository with badges:
  ```markdown
  ![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
  ![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)
  ```
- [ ] `TODO`: Operator exports public ASI:One Shared Chat URL.
- [ ] `TODO`: Operator copies Agentverse Profile URL.
- [ ] `TODO`: Operator records 3–5 min Demo Video.

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
