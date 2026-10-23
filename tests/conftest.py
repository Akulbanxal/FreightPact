"""Pytest fixtures and test configuration for FreightPact."""

from unittest.mock import AsyncMock, patch
import pytest


@pytest.fixture(autouse=True)
def mock_uagents_network_calls():
    """Isolates test suite from external Almanac and Agentverse remote network calls.

    During local integration tests, agents communicate in-process within a local
    Bureau. Disabling external HTTP calls to agentverse.ai prevents network latency
    and remote timeouts from delaying local agent startup tasks.
    """
    with patch("uagents.agent.update_agent_status", new_callable=AsyncMock) as mock_status, \
         patch("uagents.agent.is_registered_on_agentverse", new_callable=AsyncMock, return_value=False) as mock_reg:
        yield mock_status, mock_reg
