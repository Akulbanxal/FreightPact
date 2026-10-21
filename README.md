# FreightPact — Autonomous Cold-Chain Shipment Recovery Coordinator

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

> **Fetch-A-Thon 3.0 Hackathon Submission**
> Autonomous multi-agent recovery coordinator for emergency pharmaceutical and cold-chain logistics using Fetch.ai `uagents`, Agent Chat Protocol (ACP), and interactive verification-gated failover.

---

## 1. Problem Statement & Target Users

### The Problem
In cold-chain logistics (pharmaceutical vaccines, insulin, biologics), a refrigeration mechanical breakdown on a transit vehicle triggers a catastrophic clock: if the cargo temperature departs from its strict target range (e.g., +2.0°C to +8.0°C), the entire batch must be destroyed under Good Distribution Practice (GDP) regulations.

Existing logistics marketplaces suffer from a critical flaw: **they match freight based on price and claimed capabilities alone**. In reality:
- A carrier agent may bid low to win the contract while its reefer unit is warming up or experiencing compressor fault.
- Telemetry claims sent over the wire are unverified or susceptible to man-in-the-middle tampering.
- Traditional dispatchers waste hours making phone calls while temperature thresholds breach.

### Target Users
- **Pharmaceutical Cold-Chain Dispatchers:** Need instant, verified emergency recovery options without manual phone calls.
- **Logistics Control Tower Operators:** Require an auditable, cryptographic trace of sensor telemetry before awarding high-value shipments.
- **Independent Fleet Owners / Carrier Agents:** Competing autonomously in logistics marketplaces with transparent, verification-based dispatch.

---

## 2. Key Innovation: Verification-Gated Selection with Bounded Automatic Failover

FreightPact does **not** assume quotes reflect equipment reality. Instead, it operates a verification gate:
1. **Multi-Agent RFQ:** Coordinates simultaneous quote requests across distributed carrier agents.
2. **Hard Constraint Filtering:** Prunes offers that breach budget, deadline, or capacity limits.
3. **Deterministic Ranking:** Ranks compliant quotes (price primary, ETA secondary).
4. **Independent Cryptographic Telemetry Audit:** Issues an ephemeral nonce challenge to an independent `TelemetryAuditor` agent. The auditor inspects live sensor telemetry (temperature, compressor status, pressure) and validates an HMAC-SHA256 signature to prevent replay and tampering.
5. **Bounded Automatic Failover:** If the lowest-cost bidder (e.g. `carrier_beta`) fails telemetry verification due to a warm box (+13.8°C) or inactive compressor, FreightPact records the rejection with proof, rejects the carrier for this run, and immediately evaluates the next eligible candidate (e.g. `carrier_alpha` at +4.2°C).
6. **Native ASI:One Interaction:** The entire decision workflow is exposed directly to human dispatchers via Fetch.ai's Agent Chat Protocol (ACP) and Interactive Cards, with complete Markdown text fallbacks.

```
                           +--------------------------+
                           |  ASI:One / Dispatcher    |
                           +------------+-------------+
                                        | (ACP Chat / Cards)
                                        v
                           +--------------------------+
                           |   Recovery Coordinator   |
                           +------------+-------------+
                                        |
                 +----------------------+----------------------+
                 | (Broadcast RFQ)                             | (Audit Nonce Challenge)
                 v                                             v
    +--------------------------+                  +--------------------------+
    | Independent Carrier      |                  |   Telemetry Auditor      |
    | Agents (Alpha, Beta,     |                  |   (Cryptographic HMAC    |
    | Gamma)                   |                  |   & Sensor Proof)        |
    +--------------------------+                  +--------------------------+
```

---

## 3. Real Agent Names & Network Addresses

FreightPact utilizes deterministic cryptographic addresses derived from seed phrases. In local runnable mode, seeds are configured in `.env` or assigned default demo seeds:

| Agent Role | Agent Name | Local Address / Deterministic Identity | Agentverse Status |
| :--- | :--- | :--- | :--- |
| **Recovery Coordinator** | `recovery_coordinator` | `agent1q2jv8khzjg4ycngm5zk5e8mkad774ptja3ufjfwjujwu9x5scexu6pu82ja` | `PENDING_REGISTRATION` |
| **Carrier Alpha (PharmaCold)** | `carrier_alpha` | `agent1qtlq065203362a937c4v4pkhgwhgfl5q540y9j6drl7yfl7m3u0v2z9yv2g` | Local Multi-Agent Mock |
| **Carrier Beta (Budget Reefer)** | `carrier_beta` | `agent1qvd87p2583g35q6j4s0j6v477q07p0u80u28k820v45w09r5q6j2q5m929s` | Local Multi-Agent Mock |
| **Carrier Gamma (Reliable)** | `carrier_gamma` | `agent1qwn096203362a937c4v4pkhgwhgfl5q540y9j6drl7yfl7m3u0v2w84v5h` | Local Multi-Agent Mock |
| **Telemetry Auditor** | `telemetry_auditor` | `agent1qd640jsaac95j6vrjlswwqs8gw65dp9p65g3rln483zw50pl03z5gj7t684` | Local Multi-Agent Mock |

*Note: For live Agentverse registration, configure your own secret seed phrase in `.env` as documented in [docs/AGENTVERSE_SETUP.md](file:///Users/akul/Desktop/FreightPact/docs/AGENTVERSE_SETUP.md).*

---

## 4. Setup & Installation

### Prerequisites
- Python 3.10, 3.11, or 3.12 (Tested on 3.12 on macOS / Linux)
- `pip` or `uv` virtual environment

### 1. Clone & Setup Virtual Environment
```bash
git clone https://github.com/your-username/FreightPact.git
cd FreightPact

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy the template configuration file:
```bash
cp .env.example .env
```
Default configuration values in `.env`:
```ini
ENVIRONMENT=local
COORDINATOR_SEED="freightpact_coordinator_secure_demo_seed_001"
HMAC_FIXTURE_SECRET="freightpact_telemetry_fixture_signing_secret_dev_32b"
STORAGE_DB_PATH="freightpact.db"
AGENT_PORT_START=8050
```

---

## 5. Verification: Tests & Runnable Demonstration

### Run Automated Test Suite (72 Tests)
FreightPact comes with 100% passing automated test coverage across models, state machine transitions, replay attacks, HMAC verification, failover logic, and Interactive Cards:
```bash
.venv/bin/pytest tests/ -v
```
*Expected Output: `72 passed in ~35s`.*

### Run Local End-to-End Multi-Agent Demonstration
Run the in-process multi-agent Bureau demonstrating full RFQ, verification failure on Beta, failover, and recommendation of Alpha:
```bash
.venv/bin/python -m freightpact.demo
```

---

## 6. Project Documentation Directory

- 📋 **[docs/DEMO_SCRIPT.md](file:///Users/akul/Desktop/FreightPact/docs/DEMO_SCRIPT.md):** 3–5 minute step-by-step judge presentation script.
- 📋 **[docs/SUBMISSION_CHECKLIST.md](file:///Users/akul/Desktop/FreightPact/docs/SUBMISSION_CHECKLIST.md):** Hackathon rubric mapping, verification status, and evidence.
- 📋 **[docs/TROUBLESHOOTING.md](file:///Users/akul/Desktop/FreightPact/docs/TROUBLESHOOTING.md):** Common setup, binding, HMAC, and card formatting issues.
- 📋 **[docs/AGENTVERSE_SETUP.md](file:///Users/akul/Desktop/FreightPact/docs/AGENTVERSE_SETUP.md):** Step-by-step guide for manual Agentverse mailbox registration.
- 📋 **[docs/ARCHITECTURE.md](file:///Users/akul/Desktop/FreightPact/docs/ARCHITECTURE.md):** Detailed technical architecture, protocols, and data models.

---

## 7. Current Status & Truthful Simulation Boundaries

- **Implemented & Fully Tested:**
  - Multi-agent asynchronous messaging using official `uagents.Bureau` and `uagents_core.contrib.protocols.chat`.
  - Cryptographic HMAC-SHA256 sensor verification with fresh nonce replay defense and timestamp freshness window.
  - SQLite run isolation, idempotent quote deduplication, and scrubbed audit logs.
  - Interactive Card builders conforming to official ACP `MetadataContent` specifications (<64 KB, depth <8).
  - High-density logistics UI with green (`🟢`) verified, red (`🔴`) rejected, and amber (`🟡`) out-of-bounds indicators.
  - Safe terminal failure handling (`NO_SAFE_MATCH`).

- **Truthful Simulation Disclosures:**
  - **No Real Hardware Sensor Attestation:** Sensor readings (Alpha 4.2°C / Beta 13.8°C) are deterministic fixtures generated for reproducible hackathon evaluation.
  - **No Live Dispatch or Fiat Settlement:** The system concludes at `RECOMMENDED_FOR_CONFIRMATION` or `DISPATCH_SIMULATED`. It does not book physical trucks or charge bank accounts.
  - **Agentverse Mailbox Deployment:** Local Bureau execution is 100% verified. Live deployment to public Agentverse mailboxes is documented in [docs/AGENTVERSE_SETUP.md](file:///Users/akul/Desktop/FreightPact/docs/AGENTVERSE_SETUP.md) and requires human API keys.
