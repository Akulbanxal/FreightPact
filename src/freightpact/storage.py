"""Lightweight SQLite persistence layer for FreightPact workflow state and idempotency.

Persists:
- Runs and terminal outcomes
- Carrier offers and idempotency keys
- Telemetry audit verdicts
- State transitions and audit events
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Generator, List, Optional
from freightpact.models import AuditEvent, AuditVerdict, CarrierOffer, RecoveryRequest, RecoveryResult


class StorageError(Exception):
    """Raised when database operations fail."""
    pass


class StorageRepository:
    """SQLite-backed workflow repository with idempotency enforcement."""

    def __init__(self, db_path: str = "freightpact.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
        except Exception as exc:
            raise StorageError(f"Failed to connect to SQLite at '{self.db_path}': {exc}") from exc
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def close(self) -> None:
        """Explicit close hook for API compatibility."""
        pass

    def _init_db(self):
        """Initializes database tables and unique indexes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Runs table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    shipment_id TEXT NOT NULL,
                    cargo_class TEXT NOT NULL,
                    origin_location TEXT NOT NULL,
                    temp_min_c REAL NOT NULL,
                    temp_max_c REAL NOT NULL,
                    max_budget REAL NOT NULL,
                    deadline_minutes INTEGER NOT NULL,
                    current_state TEXT NOT NULL,
                    selected_carrier_id TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    terminal_result_json TEXT
                )
                """
            )

            # Offers table with unique constraint on (run_id, carrier_id) to prevent duplicate quotes
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS offers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    carrier_id TEXT NOT NULL,
                    carrier_name TEXT NOT NULL,
                    price REAL NOT NULL,
                    eta_minutes INTEGER NOT NULL,
                    capacity_kg REAL NOT NULL,
                    supported_temp_min_c REAL NOT NULL,
                    supported_temp_max_c REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, carrier_id)
                )
                """
            )

            # Verdicts table with unique constraint on (run_id, candidate_id, nonce) for idempotency
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_verdicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    recorded_temp_c REAL NOT NULL,
                    compressor_active INTEGER NOT NULL,
                    signature_valid INTEGER NOT NULL,
                    checked_at TEXT NOT NULL,
                    UNIQUE(run_id, candidate_id, nonce)
                )
                """
            )

            # Audit events table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    message TEXT NOT NULL,
                    candidate_id TEXT,
                    details_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save_run(self, request: RecoveryRequest, initial_state: str = "RECEIVED") -> None:
        """Persists a new recovery mission run."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, shipment_id, cargo_class, origin_location,
                    temp_min_c, temp_max_c, max_budget, deadline_minutes,
                    current_state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.run_id,
                    request.shipment_id,
                    request.cargo_class.value,
                    request.origin_location,
                    request.temp_min_c,
                    request.temp_max_c,
                    request.max_budget,
                    request.deadline_minutes,
                    initial_state,
                    now,
                    now,
                ),
            )
            conn.commit()

    def update_run_state(
        self,
        run_id: str,
        state: str,
        selected_carrier_id: Optional[str] = None,
        retry_count: int = 0,
        terminal_result: Optional[RecoveryResult] = None,
    ) -> None:
        """Updates the status and terminal result of a run."""
        now = datetime.now(timezone.utc).isoformat()
        result_json = terminal_result.model_dump_json() if terminal_result else None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE runs SET
                    current_state = ?,
                    selected_carrier_id = ?,
                    retry_count = ?,
                    updated_at = ?,
                    terminal_result_json = coalesce(?, terminal_result_json)
                WHERE run_id = ?
                """,
                (
                    state,
                    selected_carrier_id,
                    retry_count,
                    now,
                    result_json,
                    run_id,
                ),
            )
            conn.commit()

    def save_offer(self, offer: CarrierOffer) -> bool:
        """Persists a carrier offer. Returns True if saved, False if duplicate ignored."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO offers (
                        run_id, carrier_id, carrier_name, price, eta_minutes,
                        capacity_kg, supported_temp_min_c, supported_temp_max_c, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        offer.run_id,
                        offer.carrier_id,
                        offer.carrier_name,
                        offer.price,
                        offer.eta_minutes,
                        offer.capacity_kg,
                        offer.supported_temp_min_c,
                        offer.supported_temp_max_c,
                        now,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                # Duplicate offer for same run and carrier ignored
                return False

    def save_audit_verdict(self, verdict: AuditVerdict) -> bool:
        """Persists an audit verdict. Returns True if saved, False if duplicate ignored."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO audit_verdicts (
                        run_id, candidate_id, nonce, passed, reason,
                        recorded_temp_c, compressor_active, signature_valid, checked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        verdict.run_id,
                        verdict.candidate_id,
                        verdict.nonce,
                        1 if verdict.passed else 0,
                        verdict.reason,
                        verdict.recorded_temp_c,
                        1 if verdict.compressor_active else 0,
                        1 if verdict.signature_valid else 0,
                        now,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                # Duplicate verdict with same nonce ignored
                return False

    def save_audit_event(self, event: AuditEvent) -> None:
        """Persists an audit event."""
        now = event.created_at.isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO audit_events (
                    event_id, run_id, state, message, candidate_id, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.run_id,
                    event.state,
                    event.message,
                    event.candidate_id,
                    json.dumps(event.details),
                    now,
                ),
            )
            conn.commit()

    def get_run_summary(self, run_id: str) -> Optional[Dict]:
        """Retrieves summary of a persisted run."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)
