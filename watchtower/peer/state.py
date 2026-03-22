"""Peer state storage -- tracks known peer Watchtower instances."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from watchtower.store.journal import Journal


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class PeerStateStore:
    """Manages the peer_state table for tracking neighbor Watchtower instances."""

    def __init__(self, journal: Journal):
        self._journal = journal

    def update_heartbeat(
        self,
        hostname: str,
        severity: str,
        active_finding_count: int = 0,
        governor_state: str = "unknown",
        role: str = "leaf",
    ) -> None:
        """Insert or update peer state from a heartbeat."""
        now = _utcnow()
        self._journal.conn.execute(
            "INSERT INTO peer_state"
            " (peer_hostname, last_heartbeat, peer_severity, active_finding_count,"
            "  governor_state, role, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(peer_hostname)"
            " DO UPDATE SET last_heartbeat = ?, peer_severity = ?,"
            "  active_finding_count = ?, governor_state = ?, role = ?, updated_at = ?",
            (
                hostname, now, severity, active_finding_count,
                governor_state, role, now,
                now, severity, active_finding_count, governor_state, role, now,
            ),
        )
        self._journal.conn.commit()

    def update_event_summary(self, hostname: str, summary: str) -> None:
        """Update the last event summary for a peer."""
        now = _utcnow()
        self._journal.conn.execute(
            "UPDATE peer_state SET last_event_summary = ?, updated_at = ?"
            " WHERE peer_hostname = ?",
            (summary, now, hostname),
        )
        self._journal.conn.commit()

    def get_peer(self, hostname: str) -> dict | None:
        """Get state for a single peer."""
        row = self._journal.conn.execute(
            "SELECT peer_hostname, last_heartbeat, last_event_summary,"
            " peer_severity, role, active_finding_count, governor_state, updated_at"
            " FROM peer_state WHERE peer_hostname = ?",
            (hostname,),
        ).fetchone()
        return dict(row) if row else None

    def get_all_peers(self) -> list[dict]:
        """Get state for all known peers."""
        rows = self._journal.conn.execute(
            "SELECT peer_hostname, last_heartbeat, last_event_summary,"
            " peer_severity, role, active_finding_count, governor_state, updated_at"
            " FROM peer_state ORDER BY peer_hostname"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stale_peers(self, max_age_seconds: int = 90) -> list[dict]:
        """Get peers whose last heartbeat is older than max_age_seconds."""
        cutoff = (datetime.now(UTC) - timedelta(seconds=max_age_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows = self._journal.conn.execute(
            "SELECT peer_hostname, last_heartbeat, peer_severity, updated_at"
            " FROM peer_state WHERE last_heartbeat < ?",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def remove_peer(self, hostname: str) -> None:
        """Remove a peer from the state table."""
        self._journal.conn.execute(
            "DELETE FROM peer_state WHERE peer_hostname = ?", (hostname,)
        )
        self._journal.conn.commit()

    def update_peer_topology(
        self,
        peer_hostname: str,
        neighbors: list[dict],
    ) -> None:
        """Replace the stored topology fragment for a peer."""
        now = _utcnow()
        self._journal.conn.execute(
            "DELETE FROM peer_topology WHERE peer_hostname = ?", (peer_hostname,)
        )
        for n in neighbors:
            self._journal.conn.execute(
                "INSERT INTO peer_topology"
                " (peer_hostname, local_port, neighbor_hostname, neighbor_port,"
                "  link_state, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    peer_hostname,
                    n["local_port"],
                    n["remote_host"],
                    n["remote_port"],
                    n.get("link_state", "up"),
                    now,
                ),
            )
        self._journal.conn.commit()

    def get_peer_topology(self, peer_hostname: str) -> list[dict]:
        """Get stored topology fragment for a peer."""
        rows = self._journal.conn.execute(
            "SELECT peer_hostname, local_port, neighbor_hostname, neighbor_port,"
            " link_state, updated_at"
            " FROM peer_topology WHERE peer_hostname = ? ORDER BY local_port",
            (peer_hostname,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_fabric_topology(self) -> list[dict]:
        """Get all peer topology fragments for the fabric view."""
        rows = self._journal.conn.execute(
            "SELECT peer_hostname, local_port, neighbor_hostname, neighbor_port,"
            " link_state, updated_at"
            " FROM peer_topology ORDER BY peer_hostname, local_port"
        ).fetchall()
        return [dict(r) for r in rows]
