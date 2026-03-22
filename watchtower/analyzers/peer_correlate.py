"""Peer correlation analyzer -- matches local events with peer events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from watchtower.analyzers.base import BaseAnalyzer
from watchtower.store.events import EventStore
from watchtower.store.journal import Journal
from watchtower.store.topology import TopologyStore


@dataclass
class CorrelationResult:
    """A correlation between a local event and a peer event."""

    local_event: dict[str, Any]
    peer_event: dict[str, Any]
    local_port: str
    peer_hostname: str
    peer_port: str
    correlation_type: str  # "same_link", "same_neighbor"


class PeerCorrelateAnalyzer(BaseAnalyzer):
    """Matches local events with peer events by timestamp and port.

    Looks for patterns like:
    - Local link_change on Ethernet48 + peer event on same link from switch-b
    - Local anomaly on a port + peer reporting errors on the same link
    """

    def __init__(self, journal: Journal, topology_store: TopologyStore):
        super().__init__(journal)
        self._events = EventStore(journal)
        self._topology = topology_store

    def analyze(self, time_window_seconds: int = 300) -> list[CorrelationResult]:
        """Find local events that have matching peer events.

        For each recent local event with a port, looks up the LLDP neighbor
        on that port and checks if a peer event from that neighbor arrived
        within the time window.
        """
        local_events = self._events.get_recent(seconds=time_window_seconds)
        peer_events = [e for e in local_events if e["source"] == "peer"]
        local_only = [e for e in local_events if e["source"] == "local"]

        if not local_only or not peer_events:
            return []

        # Build a lookup: peer_hostname -> list of peer events
        peer_by_host: dict[str, list[dict]] = {}
        for pe in peer_events:
            raw = pe.get("raw_data", {})
            if isinstance(raw, dict):
                hostname = raw.get("peer_hostname", "")
                if hostname:
                    peer_by_host.setdefault(hostname, []).append(pe)

        results: list[CorrelationResult] = []
        seen: set[tuple[int, int]] = set()  # (local_id, peer_id) dedup

        for local_ev in local_only:
            port = local_ev.get("port")
            if not port:
                continue

            # Look up LLDP neighbor on this port
            neighbor = self._topology.get_neighbor(port)
            if not neighbor:
                continue

            peer_hostname = neighbor["neighbor_hostname"]
            matching_peer_events = peer_by_host.get(peer_hostname, [])

            for pe in matching_peer_events:
                pair = (local_ev["id"], pe["id"])
                if pair in seen:
                    continue
                seen.add(pair)

                results.append(CorrelationResult(
                    local_event=local_ev,
                    peer_event=pe,
                    local_port=port,
                    peer_hostname=peer_hostname,
                    peer_port=neighbor["neighbor_port"],
                    correlation_type="same_link",
                ))

        return results
