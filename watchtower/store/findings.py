"""Findings storage and lifecycle management."""

from __future__ import annotations

import json
import itertools
from datetime import datetime, timezone

from watchtower.store.journal import Journal

_counter = itertools.count(1)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_finding_id() -> str:
    """Generate a unique finding ID like f-20260319-0042."""
    now = datetime.now(timezone.utc)
    date_part = now.strftime("%Y%m%d")
    seq = next(_counter)
    return f"f-{date_part}-{seq:04d}"


class FindingsStore:
    """Manages findings lifecycle: create, resolve, query."""

    def __init__(self, journal: Journal):
        self._journal = journal

    def create(
        self,
        severity: str,
        summary: str,
        detail: str | None = None,
        related_events: list[int] | None = None,
        finding_id: str | None = None,
    ) -> str:
        """Create a new active finding. Returns the finding_id."""
        if finding_id is None:
            finding_id = _generate_finding_id()

        now = _utcnow()
        related_json = json.dumps(related_events) if related_events else None

        self._journal.conn.execute(
            "INSERT INTO findings (finding_id, timestamp, severity, summary, detail, "
            "related_events, active, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (finding_id, now, severity, summary, detail, related_json, now),
        )
        self._journal.conn.commit()
        return finding_id

    def resolve(self, finding_id: str):
        """Mark a finding as resolved."""
        now = _utcnow()
        self._journal.conn.execute(
            "UPDATE findings SET active = 0, resolved_at = ? WHERE finding_id = ?",
            (now, finding_id),
        )
        self._journal.conn.commit()

    def get(self, finding_id: str) -> dict | None:
        """Get a single finding by its ID."""
        row = self._journal.conn.execute(
            "SELECT finding_id, timestamp, severity, summary, detail, "
            "related_events, active, resolved_at FROM findings WHERE finding_id = ?",
            (finding_id,),
        ).fetchone()

        if row is None:
            return None

        result = dict(row)
        if result["related_events"]:
            result["related_events"] = json.loads(result["related_events"])
        return result

    def get_active(self, severity: str | None = None) -> list[dict]:
        """Get all active findings, optionally filtered by severity."""
        if severity:
            rows = self._journal.conn.execute(
                "SELECT finding_id, timestamp, severity, summary, detail, "
                "related_events, active FROM findings WHERE active = 1 AND severity = ? "
                "ORDER BY timestamp DESC",
                (severity,),
            ).fetchall()
        else:
            rows = self._journal.conn.execute(
                "SELECT finding_id, timestamp, severity, summary, detail, "
                "related_events, active FROM findings WHERE active = 1 "
                "ORDER BY CASE severity "
                "  WHEN 'critical' THEN 0 "
                "  WHEN 'warning' THEN 1 "
                "  WHEN 'info' THEN 2 END, "
                "timestamp DESC"
            ).fetchall()

        results = []
        for row in rows:
            r = dict(row)
            if r["related_events"]:
                r["related_events"] = json.loads(r["related_events"])
            results.append(r)
        return results

    def get_history(self, days: int = 7) -> list[dict]:
        """Get resolved findings from the last N days."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self._journal.conn.execute(
            "SELECT finding_id, timestamp, severity, summary, detail, "
            "resolved_at FROM findings WHERE active = 0 AND resolved_at >= ? "
            "ORDER BY resolved_at DESC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_active(self) -> dict:
        """Count active findings by severity."""
        rows = self._journal.conn.execute(
            "SELECT severity, COUNT(*) as count FROM findings "
            "WHERE active = 1 GROUP BY severity"
        ).fetchall()
        counts = {"critical": 0, "warning": 0, "info": 0}
        for row in rows:
            counts[row["severity"]] = row["count"]
        counts["total"] = sum(counts.values())
        return counts
