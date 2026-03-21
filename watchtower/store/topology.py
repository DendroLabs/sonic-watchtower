"""Topology graph storage -- tracks LLDP neighbors."""

from __future__ import annotations

from datetime import datetime, timezone

from watchtower.store.journal import Journal


class TopologyStore:
    """Stores and queries the local LLDP-derived topology."""

    def __init__(self, journal: Journal):
        self._journal = journal

    def update_neighbor(self, local_port: str, neighbor_hostname: str, neighbor_port: str):
        """Insert or update a neighbor entry."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        self._journal.conn.execute(
            "INSERT INTO topology (local_port, neighbor_hostname, neighbor_port, last_seen, first_seen) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(local_port, neighbor_hostname, neighbor_port) "
            "DO UPDATE SET last_seen = ?",
            (local_port, neighbor_hostname, neighbor_port, now, now, now),
        )
        self._journal.conn.commit()

    def get_neighbor(self, local_port: str) -> dict | None:
        """Get the neighbor on a given local port."""
        row = self._journal.conn.execute(
            "SELECT local_port, neighbor_hostname, neighbor_port, last_seen, first_seen "
            "FROM topology WHERE local_port = ?",
            (local_port,),
        ).fetchone()
        return dict(row) if row else None

    def get_all(self) -> list[dict]:
        """Get all topology entries."""
        rows = self._journal.conn.execute(
            "SELECT local_port, neighbor_hostname, neighbor_port, last_seen, first_seen "
            "FROM topology ORDER BY local_port"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_ports_to_host(self, hostname: str) -> list[dict]:
        """Get all local ports connected to a specific neighbor."""
        rows = self._journal.conn.execute(
            "SELECT local_port, neighbor_hostname, neighbor_port, last_seen "
            "FROM topology WHERE neighbor_hostname = ?",
            (hostname,),
        ).fetchall()
        return [dict(r) for r in rows]

    def remove_stale(self, max_age_seconds: int = 300):
        """Remove topology entries not seen within max_age_seconds."""
        cutoff = datetime.now(timezone.utc)
        from datetime import timedelta
        cutoff = (cutoff - timedelta(seconds=max_age_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._journal.conn.execute(
            "DELETE FROM topology WHERE last_seen < ?", (cutoff,)
        )
        self._journal.conn.commit()
