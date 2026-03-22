"""SQLite event journal -- core storage engine for Watchtower."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Journal:
    """Manages the SQLite event journal database."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        self._run_migrations()

    def _init_schema(self) -> None:
        schema = _SCHEMA_PATH.read_text()
        self._conn.executescript(schema)

    def _run_migrations(self) -> None:
        """Apply additive schema migrations for Phase 2+."""
        import contextlib

        migrations = [
            "ALTER TABLE peer_state ADD COLUMN role TEXT DEFAULT 'leaf'",
            "ALTER TABLE peer_state ADD COLUMN active_finding_count INTEGER DEFAULT 0",
            "ALTER TABLE peer_state ADD COLUMN governor_state TEXT DEFAULT 'unknown'",
        ]
        for sql in migrations:
            with contextlib.suppress(sqlite3.OperationalError):
                self._conn.execute(sql)
        self._conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def prune(self, detail_days: int = 7, summary_days: int = 30) -> None:
        """Remove old events and resolved findings."""
        now = datetime.now(UTC)
        detail_cutoff = (now - timedelta(days=detail_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        summary_cutoff = (now - timedelta(days=summary_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        self._conn.execute("DELETE FROM events WHERE timestamp < ?", (detail_cutoff,))
        self._conn.execute(
            "DELETE FROM findings WHERE active = 0 AND resolved_at < ?",
            (summary_cutoff,),
        )
        self._conn.commit()
