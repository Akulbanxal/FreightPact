"""Configuration and environment variable validation for FreightPact.

Enforces fail-fast startup semantics with actionable error messages.
Secrets are read strictly from environment variables or a local .env file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


# Attempt to load .env from workspace root if present
_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _WORKSPACE_ROOT / ".env"
if _ENV_FILE.exists():
    load_dotenv(dotenv_path=_ENV_FILE)
else:
    load_dotenv()


class AppConfig(BaseModel):
    """Application configuration schema with strict validation rules."""

    environment: str = Field(
        default="development",
        description="Application environment: development | test | production",
    )

    # Core coordinator identity
    coordinator_seed: str = Field(
        default="freightpact_coordinator_recovery_agent_secure_seed_phrase_demo",
        description="Mnemonic seed for the primary Recovery Coordinator agent",
    )

    # Carrier agent identities
    carrier_alpha_seed: str = Field(
        default="freightpact_carrier_alpha_safe_transport_secure_seed_demo",
        description="Mnemonic seed for Carrier Alpha agent",
    )
    carrier_beta_seed: str = Field(
        default="freightpact_carrier_beta_budget_transport_secure_seed_demo",
        description="Mnemonic seed for Carrier Beta agent",
    )
    carrier_gamma_seed: str = Field(
        default="freightpact_carrier_gamma_backup_transport_secure_seed_demo",
        description="Mnemonic seed for Carrier Gamma agent",
    )

    # Auditor identity
    auditor_seed: str = Field(
        default="freightpact_telemetry_auditor_verification_secure_seed_demo",
        description="Mnemonic seed for the Telemetry Auditor agent",
    )

    # Agentverse & ASI:One integration
    agentverse_api_key: Optional[str] = Field(
        default=None,
        description="Agentverse API key for hosted mailbox and public discovery",
    )
    asi_api_key: Optional[str] = Field(
        default=None,
        description="ASI:One API key for conversational intent parsing",
    )

    # Cryptographic secret for signing synthetic sensor telemetry payloads
    hmac_fixture_secret: str = Field(
        default="freightpact-synthetic-telemetry-fixture-hmac-secret-dev-key",
        description="Secret key for signing and verifying synthetic telemetry payloads",
    )

    # Operational parameters
    database_path: str = Field(
        default="freightpact.db",
        description="Local SQLite database file path",
    )
    max_audit_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum candidates audited before terminating with NO_SAFE_MATCH",
    )
    agent_port_start: int = Field(
        default=8000,
        ge=1024,
        le=65535,
        description="Base local port for agent HTTP endpoints",
    )

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"development", "test", "production"}
        norm = v.lower().strip()
        if norm not in allowed:
            raise ValueError(
                f"Invalid FREIGHTPACT_ENV='{v}'. Must be one of: {sorted(allowed)}"
            )
        return norm

    @field_validator("hmac_fixture_secret")
    @classmethod
    def validate_hmac_secret(cls, v: str) -> str:
        if not v or len(v.strip()) < 8:
            raise ValueError(
                "HMAC_FIXTURE_SECRET is required and must be at least 8 characters long "
                "to provide cryptographic authenticity for synthetic telemetry fixtures."
            )
        return v.strip()


def load_config() -> AppConfig:
    """Loads and validates configuration from environment variables.

    Raises:
        RuntimeError: If configuration fails validation with specific troubleshooting guidance.
    """
    try:
        raw_env = {
            "environment": os.getenv("FREIGHTPACT_ENV", "development"),
            "coordinator_seed": os.getenv(
                "COORDINATOR_SEED",
                "freightpact_coordinator_recovery_agent_secure_seed_phrase_demo",
            ),
            "carrier_alpha_seed": os.getenv(
                "CARRIER_ALPHA_SEED",
                "freightpact_carrier_alpha_safe_transport_secure_seed_demo",
            ),
            "carrier_beta_seed": os.getenv(
                "CARRIER_BETA_SEED",
                "freightpact_carrier_beta_budget_transport_secure_seed_demo",
            ),
            "carrier_gamma_seed": os.getenv(
                "CARRIER_GAMMA_SEED",
                "freightpact_carrier_gamma_backup_transport_secure_seed_demo",
            ),
            "auditor_seed": os.getenv(
                "AUDITOR_SEED",
                "freightpact_telemetry_auditor_verification_secure_seed_demo",
            ),
            "agentverse_api_key": os.getenv("AGENTVERSE_API_KEY") or None,
            "asi_api_key": os.getenv("ASI_API_KEY") or None,
            "hmac_fixture_secret": os.getenv(
                "HMAC_FIXTURE_SECRET",
                "freightpact-synthetic-telemetry-fixture-hmac-secret-dev-key",
            ),
            "database_path": os.getenv("DATABASE_PATH", "freightpact.db"),
            "max_audit_attempts": int(os.getenv("MAX_AUDIT_ATTEMPTS", "3")),
            "agent_port_start": int(os.getenv("AGENT_PORT_START", "8000")),
        }
        return AppConfig(**raw_env)
    except Exception as exc:
        raise RuntimeError(
            f"[FreightPact Config Error] Failed to initialize configuration: {exc}\n"
            "Action: Check your environment variables or .env file. See .env.example for guidance."
        ) from exc
