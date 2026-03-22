"""Peer events collector -- reads peer state from the local SQLite journal."""

from __future__ import annotations

from watchtower.peer.state import PeerStateStore


class PeerEventsCollector:
    """Collects peer state data from the local journal.

    Unlike other collectors, this reads from SQLite (not Redis).
    Peer state is populated by incoming gRPC messages from neighbors.
    """

    def __init__(self, peer_state: PeerStateStore):
        self._peer_state = peer_state

    def collect(self, hostname: str | None = None) -> dict:
        """Return peer state data.

        Args:
            hostname: Specific peer hostname. If None, returns all peers.

        Returns:
            Dict mapping peer hostnames to their state.
        """
        if hostname:
            peer = self._peer_state.get_peer(hostname)
            if peer is None:
                return {}
            return {hostname: peer}

        peers = self._peer_state.get_all_peers()
        return {p["peer_hostname"]: p for p in peers}
