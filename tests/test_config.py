"""Tests for FreightPact package setup and configuration loading."""

import os
import pytest
from freightpact import __version__
from freightpact.config import AppConfig, load_config


def test_package_metadata():
    """Verify package version is defined and accessible."""
    assert __version__ == "0.1.0"


def test_default_config_loading():
    """Verify default configuration loads cleanly with safe demo defaults."""
    config = load_config()
    assert isinstance(config, AppConfig)
    assert config.environment in {"development", "test", "production"}
    assert config.max_audit_attempts == 3
    assert config.agent_port_start == 8000
    assert len(config.hmac_fixture_secret) >= 8


def test_invalid_environment_raises_actionable_error(monkeypatch):
    """Verify invalid environment string triggers actionable validation error."""
    monkeypatch.setenv("FREIGHTPACT_ENV", "unsupported_staging_env")
    with pytest.raises(RuntimeError) as exc_info:
        load_config()
    assert "[FreightPact Config Error]" in str(exc_info.value)
    assert "Invalid FREIGHTPACT_ENV" in str(exc_info.value)


def test_short_hmac_secret_rejected(monkeypatch):
    """Verify insecure HMAC fixture secret is rejected."""
    monkeypatch.setenv("HMAC_FIXTURE_SECRET", "short")
    with pytest.raises(RuntimeError) as exc_info:
        load_config()
    assert "[FreightPact Config Error]" in str(exc_info.value)
    assert "at least 8 characters" in str(exc_info.value)


def test_custom_valid_config(monkeypatch):
    """Verify custom valid configuration variables are respected."""
    monkeypatch.setenv("FREIGHTPACT_ENV", "test")
    monkeypatch.setenv("MAX_AUDIT_ATTEMPTS", "5")
    monkeypatch.setenv("AGENT_PORT_START", "9000")
    monkeypatch.setenv("HMAC_FIXTURE_SECRET", "custom-super-secret-fixture-key")

    config = load_config()
    assert config.environment == "test"
    assert config.max_audit_attempts == 5
    assert config.agent_port_start == 9000
    assert config.hmac_fixture_secret == "custom-super-secret-fixture-key"
