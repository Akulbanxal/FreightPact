# FreightPact — Troubleshooting Guide

This guide details common operational and developer issues, error messages, and verified recovery procedures for FreightPact across installation, secrets, mailboxes, ACP protocols, Interactive Cards, timeouts, and tests.

---

## 1. Environment & Installation Issues

### `[Errno 1] error while attempting to bind on address ('0.0.0.0', 8000): operation not permitted`
- **Cause:** Default port 8000 is occupied or restricted by sandbox permissions.
- **Resolution:**
  - In integration tests, ephemeral dynamic ports are automatically used via `socket.bind(('127.0.0.1', 0))`.
  - For standalone runs, set `AGENT_PORT_START` in `.env` to an open local port range (e.g. `8050`).

### `AttributeError: module 'uagents' has no attribute '__version__'`
- **Cause:** `uagents` exposes version information through package metadata rather than a module-level `__version__` attribute.
- **Resolution:** Check versions using `importlib.metadata.version("uagents")` or via `pip show uagents`.

### `TypeError: Object of type 'FieldInfo' is not JSON serializable`
- **Cause:** `uagents.Model` derives from Pydantic V1 internally for schema digestion (`Model.build_schema_digest()`). When using Pydantic V2 in the project root, importing `Field` from top-level `pydantic` causes schema serialization failures.
- **Resolution:** In internal protocol definitions (`src/freightpact/protocols.py`), `Field` is conditionally imported from `pydantic.v1` with fallback to `pydantic`.

---

## 2. Telemetry Verification & Cryptographic Failures

### `Cryptographic signature verification failed: payload tampered or invalid fixture secret`
- **Cause:** The HMAC-SHA256 digest recomputed by the Telemetry Auditor does not match the signature on the telemetry payload.
- **Resolution:**
  - Verify that `HMAC_FIXTURE_SECRET` is identical across coordinator, carriers, and auditor environments.
  - Verify canonical string ordering:
    `candidate_id|run_id|nonce|timestamp_iso|recorded_temp_c|compressor_active|reefer_battery_pct|pressure_bar`.
  - Ensure timestamps are formatted with UTC ISO-8601 (`.isoformat()`).

### `Stale telemetry: timestamp age exceeds freshness window`
- **Cause:** The telemetry payload timestamp is more than 300 seconds older than the audit check time.
- **Resolution:** Check system clock synchronization. In tests or demo scripts, always generate fresh timestamps using `datetime.now(timezone.utc)`.

### `Replay detected: nonce has already been spent in run`
- **Cause:** An audit challenge was issued using a nonce that was already audited during the current run ID.
- **Resolution:** Challenge nonces must be uniquely generated per audit attempt using `uuid4().hex`. Nonces cannot be reused across candidates.

---

## 3. Agentverse Mailbox & Networking Issues

### Mailbox Connection Dropped or `ConnectionRefusedError`
- **Cause:** Outbound WebSocket connection to Agentverse mailbox server interrupted.
- **Resolution:**
  - Check internet connectivity.
  - Ensure `AGENTVERSE_API_KEY` in `.env` is valid and not expired.
  - Test agent address resolution using `.venv/bin/python -m freightpact.run_coordinator --info`.

### `Agent registration status updated to inactive`
- **Cause:** The agent process gracefully shut down or the Bureau context was cancelled.
- **Resolution:** This is standard lifecycle behavior upon termination. When running a persistent agent, avoid passing Ctrl+C or running in transient test runners.

---

## 4. Agent Chat Protocol (ACP) & Interactive Cards

### Cards Not Rendering in Chat Client
- **Cause:** The chat client does not support ACP `MetadataContent` or card protocol version 1.
- **Resolution:**
  - FreightPact automatically transmits a structured, formatted markdown `TextContent` block alongside `MetadataContent`.
  - Check the raw message to verify the fallback text displays correctly.

### `Card payload exceeds maximum allowed size (65536 bytes)`
- **Cause:** The serialized card JSON payload exceeded the 64 KB limit enforced by ACP specification.
- **Resolution:**
  - Ensure logs or extensive telemetry histories are truncated before injecting into card fields.
  - FreightPact's card builders produce compact payloads between 800 and 1,500 bytes.

### User Card Action Rejected (`Action rejected: candidate does not match audited selection`)
- **Cause:** A user attempted to submit a confirmation action referencing an unauthorized or unverified carrier.
- **Resolution:** Server-side card validation prevents forged carrier awards. The coordinator only accepts confirmation actions for the currently audited and selected candidate.

---

## 5. Workflow & Timeout Failures

### `No eligible carrier offers remain for run` -> State: `NO_SAFE_MATCH`
- **Cause:** All received carrier offers failed either hard constraint filtering (price > budget, ETA > deadline, insufficient capacity) or cold-chain telemetry verification.
- **Expected Behavior:** This is a safe terminal state. In cold-chain logistics, FreightPact never awards a contract to a non-compliant or compromised carrier.

### `Reached maximum audit attempts (3)` -> State: `NO_SAFE_MATCH`
- **Cause:** More than `MAX_AUDIT_ATTEMPTS` candidates failed verification sequentially.
- **Resolution:** Check carrier profiles or adjust `MAX_AUDIT_ATTEMPTS` in `.env` if more candidates should be evaluated before safe exit.

### Duplicate quote or audit verdict received
- **Cause:** Asynchronous network retries delivered identical messages twice.
- **Resolution:** SQLite persistence enforces `UNIQUE(run_id, carrier_id)` on quotes and `UNIQUE(run_id, candidate_id, nonce)` on verdicts. Duplicate messages are logged as `DUPLICATE_QUOTE_IGNORED` and discarded safely.
