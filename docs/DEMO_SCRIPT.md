# FreightPact — 3 to 5 Minute Demo Script

> **Scenario:** Emergency cold-chain recovery of a compromised temperature-sensitive vaccine shipment (`VAX-8821`).  
> **Key Mechanism:** Multi-agent quote collection, deterministic ranking, cryptographic telemetry audit failure on lowest bidder, autonomous failover to compliant backup carrier, and interactive card confirmation.

---

## 0. Demo Overview & Timing

| Segment | Duration | Focus |
| :--- | :--- | :--- |
| **1. Problem & Context** | 45 sec | Cold-chain spoilage risks & why marketplace quotes alone are dangerous. |
| **2. Emergency Incident Trigger** | 45 sec | Dispatcher submits incident via natural language or ACP Interactive Card. |
| **3. Autonomous Multi-Agent RFQ** | 45 sec | 3 independent carrier agents submit competing offers simultaneously. |
| **4. Telemetry Audit & Failover** | 60 sec | Lowest bidder (Beta) fails sensor audit; orchestrator fails over to Alpha. |
| **5. Confirmation & Transparency** | 45 sec | Final recommendation review card & truthful simulation disclosure. |

---

## Segment 1: The Cold-Chain Spoilage Problem (0:00 - 0:45)

**Speaker:**
> *"Welcome to FreightPact. In pharmaceutical cold-chain logistics, temperature excursions destroy millions of dollars of vaccines and biologics every year. When a refrigerated transit vehicle breaks down, dispatchers have minutes to secure a replacement vehicle.*
>
> *Today's digital freight marketplaces have a dangerous flaw: they rank quotes based on price and claimed capability alone. A carrier might submit the lowest bid, but if their reefer cooling compressor is failing or their storage bay is at 14°C, accepting that quote ruins the cargo.*
>
> *FreightPact solves this with **Verification-Gated Selection**: an autonomous coordinator that never awards a recovery contract until an independent Telemetry Auditor cryptographically verifies the carrier's real equipment readiness."*

---

## Segment 2: Incident Intake & Constraints (0:45 - 1:30)

**Speaker:**
> *"Let's look at our emergency incident. A refrigerated van carrying 1,000 kg of pediatric vaccines between Gurugram and New Delhi has suffered an engine failure.*
>
> *Our constraints:*
> - *Safe Temperature Band: +2.0°C to +8.0°C*
> - *Maximum Budget: ₹48,000 INR*
> - *Maximum Arrival Deadline: 45 minutes*
>
> *In ASI:One, the dispatcher can either input this in plain English or submit via our compact Incident Intake Card."*

**Action:**
Show incident parameters:
```json
{
  "shipment_id": "VAX-8821",
  "cargo_class": "vaccines",
  "temp_min_c": 2.0,
  "temp_max_c": 8.0,
  "max_budget": 48000.0,
  "deadline_minutes": 45,
  "required_capacity_kg": 1000.0
}
```

---

## Segment 3: Autonomous Multi-Agent Coordination (1:30 - 2:15)

**Speaker:**
> *"The Recovery Coordinator immediately broadcasts structured `QuoteRequestMsg` messages across our carrier network. Three independent carrier agents respond with their bids in real time:*
>
> 1. *`carrier_beta` bids ₹38,000 with a 32-minute ETA.*
> 2. *`carrier_alpha` bids ₹41,500 with a 38-minute ETA.*
> 3. *`carrier_gamma` bids ₹43,000 with a 40-minute ETA.*
>
> *Under traditional marketplace rules, `carrier_beta` would win instantly because it is the cheapest by ₹3,500. But FreightPact does not trust unverified claims."*

---

## Segment 4: Cryptographic Sensor Audit & Failover (2:15 - 3:15)

**Speaker:**
> *"Before awarding `carrier_beta`, the Coordinator dispatches a cryptographic challenge with a unique, one-time nonce to the `TelemetryAuditor` agent.*
>
> *The Auditor evaluates the vehicle's signed sensor telemetry payload. The result is an immediate rejection:*
> - *`carrier_beta`'s box is at **13.8°C** — well above our 8.0°C ceiling.*
> - *Its cooling compressor is **INACTIVE (FAULT)**.*
>
> *Notice what happens next: the Coordinator does not halt or fail. It logs `carrier_beta` as rejected for this run, prevents any replay attacks, and automatically fails over to the next compliant candidate: `carrier_alpha`.*
>
> *The Auditor verifies `carrier_alpha`:*
> - *Temperature: **+4.2°C** (perfectly within the 2°C to 8°C band)*
> - *Compressor: **ACTIVE***
> - *HMAC-SHA256 Signature: **VALID and Fresh***
>
> *Verification passes!"*

**Action:**
Point to console/audit log output:
```
[AUDIT] AUDIT_VERDICT_RECEIVED: carrier_beta | passed=false (13.8°C > 8.0°C, compressor FAULT)
[AUDIT] AUDIT_CHALLENGE_DISPATCHED: carrier_alpha | nonce_28d686dc9005407f
[AUDIT] AUDIT_VERDICT_RECEIVED: carrier_alpha | passed=true (4.2°C, compressor ACTIVE, valid HMAC)
```

---

## Segment 5: Operator Review & Simulation Disclosure (3:15 - 4:00)

**Speaker:**
> *"Inside ASI:One, the dispatcher receives a clear, high-density operations card:*
> - *Clear color coding: Red 🔴 for Beta's rejection reason, Green 🟢 for Alpha's verified proof.*
> - *Full audit trace with rates, ETAs, and cryptographic verification status.*
> - *An explicit interactive card prompting the operator to authorize or reject the recovery dispatch.*
>
> *Finally, we provide transparent simulation disclosure: this demonstration uses synthetic fixtures to guarantee safe, deterministic hackathon evaluation. No real vehicles are dispatched and no bank accounts are charged.*
>
> *That is FreightPact: autonomous, multi-agent logistics recovery with cryptographic verification."*

---

## Reproducing the Demo Locally

Run the in-process demo command:
```bash
.venv/bin/python -m freightpact.demo
```
This executes the exact sequence above using real Fetch.ai `uagents` messaging.
