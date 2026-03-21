"""Event storage -- raw events from local detection, peers, and syslog."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from watchtower.store.journal import Journal


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventStore:
    """Stores and queries raw events."""

    def __init__(self, journal: Journal):
        self._journal = journal

    def record(
        self,
        source: str,
        category: str,
        severity: str,
        port: str | None = None,
        raw_data: dict | None = None,
    ) -> int:
        """Record a new event. Returns the event ID."""
        now = _utcnow()
        raw_json = json.dumps(raw_data) if raw_data else None

        cursor = self._journal.conn.execute(
            "INSERT INTO events (timestamp, source, category, severity, port, raw_data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now, source, category, severity, port, raw_json, now),
        )
        self._journal.conn.commit()
        return cursor.lastrowid

    def get_recent(self, seconds: int = 3600, severity: str | None = None) -> list[dict]:
        """Get events from the last N seconds."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        if severity:
            rows = self._journal.conn.execute(
                "SELECT id, timestamp, source, category, severity, port, raw_data "
                "FROM events WHERE timestamp >= ? AND severity = ? ORDER BY timestamp DESC",
                (cutoff, severity),
            ).fetchall()
        else:
            rows = self._journal.conn.execute(
                "SELECT id, timestamp, source, category, severity, port, raw_data "
                "FROM events WHERE timestamp >= ? ORDER BY timestamp DESC",
                (cutoff,),
            ).fetchall()

        results = []
        for row in rows:
            r = dict(row)
            if r["raw_data"]:
                r["raw_data"] = json.loads(r["raw_data"])
            results.append(r)
        return results

    def get_by_port(self, port: str, seconds: int = 3600) -> list[dict]:
        """Get recent events for a specific port."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows = self._journal.conn.execute(
            "SELECT id, timestamp, source, category, severity, port, raw_data "
            "FROM events WHERE port = ? AND timestamp >= ? ORDER BY timestamp DESC",
            (port, cutoff),
        ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            if r["raw_data"]:
                r["raw_data"] = json.loads(r["raw_data"])
            results.append(r)
        return results

    def count_recent(self, seconds: int = 3600) -> dict:
        """Count recent events by severity."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows = self._journal.conn.execute(
            "SELECT severity, COUNT(*) as count FROM events "
            "WHERE timestamp >= ? GROUP BY severity",
            (cutoff,),
        ).fetchall()
        counts = {"info": 0, "warning": 0, "critical": 0}
        for row in rows:
            counts[row["severity"]] = row["count"]
        counts["total"] = sum(counts.values())
        return counts
