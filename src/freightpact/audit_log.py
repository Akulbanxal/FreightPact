"""Structured audit logging for FreightPact.

Enforces:
1. Scrubbing of sensitive secrets, seeds, and API keys.
2. Structured output with run_id, correlation_id, timestamp, and agent role.
3. Thread-safe log emissions.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Secret patterns to sanitize from logs
_SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(seed['\"]?\s*[:=]\s*['\"])([^'\"]+)(['\"])"),
    re.compile(r"(?i)(secret['\"]?\s*[:=]\s*['\"])([^'\"]+)(['\"])"),
    re.compile(r"(?i)(key['\"]?\s*[:=]\s*['\"])([^'\"]+)(['\"])"),
    re.compile(r"(?i)(token['\"]?\s*[:=]\s*['\"])([^'\"]+)(['\"])"),
]


def sanitize_log_message(msg: str) -> str:
    """Masks potential secret values in log text."""
    sanitized = msg
    for pattern in _SENSITIVE_PATTERNS:
        sanitized = pattern.sub(r"\1***REDACTED***\3", sanitized)
    return sanitized


class AuditLogger:
    """Structured audit logger for FreightPact agents."""

    def __init__(self, logger_name: str = "freightpact.audit"):
        self._logger = logging.getLogger(logger_name)

    def log_event(
        self,
        event_name: str,
        run_id: str,
        role: str,
        correlation_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        level: int = logging.INFO,
    ) -> Dict[str, Any]:
        """Emits a structured JSON audit log entry."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_name,
            "run_id": run_id,
            "role": role,
            "correlation_id": correlation_id or "none",
            "details": details or {},
        }

        # Sanitize any accidental secrets in details dictionary
        raw_json = json.dumps(entry, default=str)
        clean_json = sanitize_log_message(raw_json)
        self._logger.log(level, f"[AUDIT] {clean_json}")
        return json.loads(clean_json)


# Global default auditor
audit_logger = AuditLogger()
