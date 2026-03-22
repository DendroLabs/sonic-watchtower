"""Topology diff analyzer -- detects topology changes."""

from __future__ import annotations

from dataclasses import dataclass

from watchtower.analyzers.base import BaseAnalyzer
from watchtower.store.journal import Journal
from watchtower.store.topology import TopologyStore


@dataclass
class TopologyChange:
    """Represents a single topology change."""

    change_type: str  # "neighbor_added", "neighbor_removed", "neighbor_changed"
    local_port: str
    neighbor_hostname: str
    neighbor_port: str
    old_neighbor_hostname: str | None = None  # for "changed" type
    old_neighbor_port: str | None = None


class TopologyDiffAnalyzer(BaseAnalyzer):
    """Detects topology changes by comparing current LLDP data against stored topology.

    Detects:
    - New neighbor appeared (link came up or new device connected)
    - Neighbor disappeared (link went down or device removed)
    - Neighbor changed (different device on same port -- recabling)
    """

    def __init__(self, journal: Journal, topology_store: TopologyStore):
        super().__init__(journal)
        self._topology = topology_store
        self._last_snapshot: dict[str, dict[str, str]] | None = None

    def analyze(self, current_lldp: dict | None = None) -> list[TopologyChange]:
        """Compare current LLDP data against last known snapshot.

        Args:
            current_lldp: Dict mapping local port -> neighbor info.
                If None, reads from topology store.

        Returns:
            List of topology changes detected.
        """
        if current_lldp is not None:
            current = {
                port: {
                    "neighbor_hostname": info.get("neighbor_hostname", ""),
                    "neighbor_port": info.get("neighbor_port", ""),
                }
                for port, info in current_lldp.items()
                if info.get("neighbor_hostname")
            }
        else:
            entries = self._topology.get_all()
            current = {
                e["local_port"]: {
                    "neighbor_hostname": e["neighbor_hostname"],
                    "neighbor_port": e["neighbor_port"],
                }
                for e in entries
            }

        if self._last_snapshot is None:
            # First run -- no diff, just record
            self._last_snapshot = current
            return []

        changes: list[TopologyChange] = []
        old = self._last_snapshot

        # Check for new and changed neighbors
        for port, new_info in current.items():
            if port not in old:
                changes.append(TopologyChange(
                    change_type="neighbor_added",
                    local_port=port,
                    neighbor_hostname=new_info["neighbor_hostname"],
                    neighbor_port=new_info["neighbor_port"],
                ))
            elif (old[port]["neighbor_hostname"] != new_info["neighbor_hostname"]
                  or old[port]["neighbor_port"] != new_info["neighbor_port"]):
                changes.append(TopologyChange(
                    change_type="neighbor_changed",
                    local_port=port,
                    neighbor_hostname=new_info["neighbor_hostname"],
                    neighbor_port=new_info["neighbor_port"],
                    old_neighbor_hostname=old[port]["neighbor_hostname"],
                    old_neighbor_port=old[port]["neighbor_port"],
                ))

        # Check for removed neighbors
        for port, old_info in old.items():
            if port not in current:
                changes.append(TopologyChange(
                    change_type="neighbor_removed",
                    local_port=port,
                    neighbor_hostname=old_info["neighbor_hostname"],
                    neighbor_port=old_info["neighbor_port"],
                ))

        self._last_snapshot = current
        return changes
