"""Command-line entry point to run the primary FreightPact Recovery Coordinator agent.

Supports two deployment modes:
1. Local HTTP mode (default): Binds to a local port with an HTTP endpoint or inspector.
2. Agentverse Mailbox mode: Connects to an Agentverse Mailbox for external messaging
   from ASI:One or the Agentverse web chat interface without needing a public IP/domain.

Usage:
  # Local HTTP mode (inspector accessible at http://127.0.0.1:8000)
  python -m freightpact.run_coordinator

  # Agentverse Mailbox mode (requires agent registered as mailbox agent on Agentverse)
  python -m freightpact.run_coordinator --mailbox

  # Print agent address and identity details for Agentverse registration
  python -m freightpact.run_coordinator --info
"""

import argparse
import sys
from uagents import Bureau
from freightpact.auditor_agent import create_auditor_agent
from freightpact.carrier_agent import create_carrier_agent
from freightpact.config import load_config
from freightpact.coordinator_agent import CoordinatorOrchestrator, create_coordinator_agent
from freightpact.storage import StorageRepository


def main():
    parser = argparse.ArgumentParser(
        description="Run the FreightPact Recovery Coordinator Agent on Agentverse / ASI:One"
    )
    parser.add_argument(
        "--mailbox",
        action="store_true",
        help="Connect to Agentverse using a hosted Mailbox (recommended for ASI:One)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Local HTTP port (defaults to AGENT_PORT_START in .env, e.g. 8000)",
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Print agent addresses, manifest digests, and exit",
    )
    args = parser.parse_args()

    config = load_config()

    # Pre-derive addresses for worker agents (disable secondary inspectors so coordinator owns port)
    carrier_beta = create_carrier_agent(
        "carrier_beta", seed=config.carrier_beta_seed, enable_agent_inspector=False
    )
    carrier_alpha = create_carrier_agent(
        "carrier_alpha", seed=config.carrier_alpha_seed, enable_agent_inspector=False
    )
    carrier_gamma = create_carrier_agent(
        "carrier_gamma", seed=config.carrier_gamma_seed, enable_agent_inspector=False
    )
    auditor = create_auditor_agent(
        secret_key=config.hmac_fixture_secret, seed=config.auditor_seed, enable_agent_inspector=False
    )

    carrier_addresses = {
        "carrier_beta": str(carrier_beta.address),
        "carrier_alpha": str(carrier_alpha.address),
        "carrier_gamma": str(carrier_gamma.address),
    }

    storage = StorageRepository(db_path=config.database_path)
    orchestrator = CoordinatorOrchestrator(
        auditor_address=str(auditor.address),
        carrier_addresses=carrier_addresses,
        max_audit_attempts=config.max_audit_attempts,
        storage=storage,
    )

    port = args.port or config.agent_port_start
    coordinator = create_coordinator_agent(
        orchestrator=orchestrator,
        seed=config.coordinator_seed,
        port=port if not args.mailbox else None,
        include_chat=True,
        mailbox=args.mailbox,
    )

    if args.info:
        print("=" * 78)
        print("FREIGHTPACT AGENT IDENTITY & ADDRESS INFO")
        print("=" * 78)
        print(f"Coordinator Agent Name:    recovery_coordinator")
        print(f"Coordinator Agent Address: {coordinator.address}")
        print(f"Carrier Beta Address:      {carrier_beta.address}")
        print(f"Carrier Alpha Address:     {carrier_alpha.address}")
        print(f"Carrier Gamma Address:     {carrier_gamma.address}")
        print(f"Telemetry Auditor Address: {auditor.address}")
        print(f"Mailbox Mode Configured:   {args.mailbox}")
        print(f"Configured Database Path:  {config.database_path}")
        print("=" * 78)
        return

    print("=" * 78)
    print("🚀 STARTING FREIGHTPACT RECOVERY COORDINATOR AGENT")
    print("=" * 78)
    print(f"Coordinator Name:    recovery_coordinator")
    print(f"Coordinator Address: {coordinator.address}")
    print(f"Carrier Beta:        {carrier_beta.address} (₹38k / 13.8°C Fault)")
    print(f"Carrier Alpha:       {carrier_alpha.address} (₹41.5k / 4.2°C Valid)")
    print(f"Carrier Gamma:       {carrier_gamma.address} (₹43k / 4.8°C Backup)")
    print(f"Telemetry Auditor:   {auditor.address} (HMAC-SHA256)")
    if args.mailbox:
        print("Mode:                Agentverse Mailbox (polling Agentverse for incoming ACP messages)")
        print("Requirement:         Ensure this address is registered on Agentverse as a Mailbox agent.")
    else:
        print(f"Mode:                Local HTTP Server on port {port}")
        print(f"Inspector URL:       http://127.0.0.1:{port}/agent_info")
    print("=" * 78)

    # Run in Bureau so the local worker agents and auditor receive messages
    bureau = Bureau(port=port)
    bureau.add(coordinator)
    bureau.add(carrier_beta)
    bureau.add(carrier_alpha)
    bureau.add(carrier_gamma)
    bureau.add(auditor)
    bureau.run()


if __name__ == "__main__":
    main()
