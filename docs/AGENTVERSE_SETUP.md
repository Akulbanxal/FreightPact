# Agentverse Setup & ASI:One Onboarding Guide

This document provides the exact, verified manual onboarding procedure to register the primary **FreightPact Recovery Coordinator** on Fetch.ai Agentverse and test end-to-end conversation via ASI:One or the Agentverse web chat interface.

> [!IMPORTANT]
> **Zero-Secret Commitment & Real Verification Rule:**
> - Never paste your mnemonic seed phrases, Agentverse API keys, or ASI:One API keys into chat or git.
> - Keep all secrets strictly in your local `.env` file.
> - Do not invent agent addresses, profile URLs, or shared chat links. Placeholders are clearly marked until you execute the manual steps below.
> - The primary workflow is considered **unverified in ASI:One** until the manual web test is confirmed by the human operator.

---

## 1. Prerequisites: Accounts & Secrets You Must Create

You must create or configure the following yourself (automated tools cannot and must not bypass human verification or CAPTCHA):

1. **Agentverse Account:**
   - Register or log in at [https://agentverse.ai](https://agentverse.ai).
   - Complete any required email verification.
2. **Agentverse API Key:**
   - Navigate to your Agentverse profile: **Profile (top right) -> API Keys**.
   - Generate a new API key.
3. **Local Environment Variables:**
   - In your local project directory, copy `.env.example` to `.env`:
     ```bash
     cp .env.example .env
     ```
   - Edit `.env` and set:
     ```bash
     COORDINATOR_SEED="<your_secure_unique_coordinator_mnemonic_seed>"
     AGENTVERSE_API_KEY="<your_agentverse_api_token>"
     HMAC_FIXTURE_SECRET="<your_secret_signing_key_at_least_8_chars>"
     ```
   > [!TIP]
   > You can inspect your deterministic coordinator agent address before registering by running:
   > ```bash
   > .venv/bin/python -m freightpact.run_coordinator --info
   > ```

---

## 2. Agentverse Registration: Step-by-Step Procedure

Agentverse supports two agent connectivity models:
1. **Hosted Mailbox Agent (Recommended):** The agent runs locally on your machine and continuously polls Agentverse for incoming messages. No public IP, DNS, or port-forwarding is required.
2. **Custom Webhook Endpoint:** Requires a public HTTPS endpoint (e.g. via cloud server or ngrok).

The simplest, official route is the **Mailbox Agent**.

### Step 1: Create the Mailbox Agent on Agentverse
1. Log in to [Agentverse](https://agentverse.ai).
2. Go to **My Agents** from the sidebar.
3. Click **+ New Agent**.
4. Select **Local Agent (Mailbox)**.
5. In the creation modal:
   - **Agent Name:** `FreightPact Recovery Coordinator`
   - Agentverse will display a configuration snippet containing your Mailbox connection key.

### Step 2: Run the Coordinator Locally in Mailbox Mode
Start the primary coordinator connected to your mailbox:
```bash
.venv/bin/python -m freightpact.run_coordinator --mailbox
```

**Expected Startup Output:**
```text
==============================================================================
🚀 STARTING FREIGHTPACT RECOVERY COORDINATOR AGENT
==============================================================================
Agent Name:    recovery_coordinator
Agent Address: agent1qd4x548g8ur7paxuegsy7wzcnxq6tremct95zz8ujexcggj8fxnevdnvcrz
Mode:          Agentverse Mailbox (polling Agentverse for incoming ACP messages)
Requirement:   Ensure this address is registered on Agentverse as a Mailbox agent.
==============================================================================
INFO: [recovery_coordinator]: Starting mailbox client for https://agentverse.ai
```

### Step 3: Confirm Online Status & Copy URLs
1. Refresh the **My Agents** dashboard on [Agentverse](https://agentverse.ai).
2. Find `FreightPact Recovery Coordinator`.
3. Check the status indicator: it should turn **Green (Online / Active)**.
4. Open the agent's details page.
5. Record your real values in Section 5 below:
   - Copy the **Agent Address** (e.g. `agent1q...`).
   - Copy the **Agentverse Profile URL** from the browser address bar.

### Step 4: Verify Agent Chat Protocol (ACP) Manifest
1. In the agent details view on Agentverse, navigate to the **Protocols** tab.
2. Ensure `AgentChatProtocol` (version `0.3.0` / spec digest) is listed as active.
3. FreightPact automatically includes `chat_protocol_spec` from `uagents_core.contrib.protocols.chat`.

---

## 3. Testing via Agentverse Chat Tab & ASI:One

Once your coordinator agent is online, execute the following manual tests.

### Test 1: Intake Prompt on Incomplete Incident
1. Open the agent's **Chat** tab in Agentverse or search for the coordinator address in [ASI:One](https://asi1.ai).
2. Send an incomplete incident message:
   ```text
   Shipment VAX-8821 breakdown near Gurugram
   ```
3. **Verify Response:**
   - An immediate `ChatAcknowledgement` receipt is processed.
   - The coordinator replies with the cold-chain intake prompt listing missing parameters:
     - Target Temperature Band
     - Maximum Budget Ceiling
     - Recovery Deadline
   - An interactive intake form card is rendered (in clients supporting cards).

### Test 2: Primary Multi-Agent Recovery & Automatic Failover
1. In the same chat, send the complete incident scenario:
   ```text
   Emergency! Reefer unit breakdown on shipment VAX-8821 carrying Vaccines at Sector 18 Gurugram.
   Temperature rising fast! Target band: 2°C to 8°C. Budget ceiling ₹48,000, deadline 45 minutes, capacity 1000kg.
   ```
2. **Observe Real Multi-Agent Orchestration in Terminal:**
   - Coordinator broadcasts `QuoteRequestMsg` to Carrier Beta, Alpha, and Gamma.
   - Carrier Beta quotes lowest price (₹38,000) and is challenged first.
   - Telemetry Auditor returns **FAIL** (13.8°C > 8.0°C and compressor inactive).
   - Coordinator rejects Beta and challenges Carrier Alpha (₹41,500).
   - Telemetry Auditor returns **PASS** (4.2°C, compressor active, valid HMAC signature).
   - Coordinator finalizes run state as `RECOMMENDED_FOR_CONFIRMATION`.
3. **Verify Output in ASI:One / Agentverse UI:**
   - Complete Markdown report with evaluated quotes, audit rejection details, and recommendation.
   - Interactive Review Card rendered with `Confirm & Dispatch Carrier` and `Decline Recommendation` CTAs.
   - Explicit disclosure: `SIMULATION — awaiting user confirmation`.

### Test 3: Confirm Award Action Return
1. Click **Confirm & Dispatch Carrier** on the interactive review card (or type `Confirm dispatch`).
2. **Verify Response:**
   - Coordinator validates the action server-side.
   - Returns confirmation message:
     ```text
     🎉 Carrier Award Confirmed & Dispatched!
     - Shipment ID: VAX-8821
     - Awarded Carrier: carrier_alpha
     - Dispatch Status: Operational lock activated. Real-time telemetry monitoring initiated.
     ```
   - Run state is updated to `CONFIRMED_DISPATCHED` in the local SQLite database.

---

## 4. How to Produce the Official Shared Chat URL in ASI:One

For the Fetch-A-Thon submission:
1. In the [ASI:One](https://asi1.ai) chat conversation where you completed Test 2 and Test 3 above:
2. Look at the top-right corner of the chat window for the **Share** or **Export** button.
3. Click **Create Public Link** / **Share Chat**.
4. Copy the generated link (format: `https://asi1.ai/chat/share/...` or `https://fetch.ai/...`).
5. Paste the real URL into Section 5 below and into your submission form.

---

## 5. Live Project Registry & Placeholders

Fill in these placeholders once you complete the steps above. Do not commit actual seed phrases or API tokens.

| Item | Value / Placeholder | Status |
|---|---|---|
| **Primary Coordinator Address** | `agent1qd4x548g8ur7paxuegsy7wzcnxq6tremct95zz8ujexcggj8fxnevdnvcrz` *(default demo seed)* or `<YOUR_LOCAL_COORDINATOR_ADDRESS>` | Derived from local seed |
| **Agentverse Profile URL** | `https://agentverse.ai/agents/details/<YOUR_AGENT_UUID>` | ⏳ Awaiting manual operator registration |
| **ASI:One Shared Chat URL** | `https://asi1.ai/chat/share/<YOUR_SHARED_CHAT_ID>` | ⏳ Awaiting manual operator execution |
| **End-to-End Workflow Status** | ⏳ **Unverified in live ASI:One** until operator completes manual web test | Documented |

---

## 6. Common Failures & Recovery Steps

### Issue 1: "Agent mailbox not found: create one using the agent inspector"
- **Cause:** The agent address running locally does not match the mailbox created on Agentverse, or the mailbox was deleted.
- **Recovery:**
  1. Run `.venv/bin/python -m freightpact.run_coordinator --info` and note the `Coordinator Agent Address`.
  2. On Agentverse, create a new Mailbox agent with that exact address, or update `COORDINATOR_SEED` in `.env` to match the seed phrase of the mailbox you created.

### Issue 2: "Failed to connect to mailbox server: ClientConnectorError"
- **Cause:** Temporary network disconnection or firewall blocking outbound HTTPS requests to `agentverse.ai`.
- **Recovery:** Verify internet connectivity and check that outbound traffic to port 443 is permitted. The agent automatically retries connecting.

### Issue 3: "Invalid FREIGHTPACT_ENV or HMAC_FIXTURE_SECRET"
- **Cause:** `.env` file is missing or has a secret shorter than 8 characters.
- **Recovery:** Ensure `HMAC_FIXTURE_SECRET` has at least 8 characters in your `.env` file.

### Issue 4: Interactive Card renders as plain text
- **Cause:** Some mobile clients or external chat wrappers do not yet support interactive card metadata envelopes.
- **Recovery:** FreightPact automatically includes complete markdown text fallback in `TextContent` inside the same `ChatMessage`, ensuring all details remain readable.

---

## 7. Automated Local Verification Commands

To verify that the coordinator agent, internal protocols, and card envelopes are completely functional before going live:

```bash
# 1. Run full automated test suite (63 tests)
.venv/bin/pytest tests/ -v

# 2. Run standalone local multi-agent simulation demo
.venv/bin/python -m freightpact.demo

# 3. Print coordinator agent address and identity info
.venv/bin/python -m freightpact.run_coordinator --info
```
