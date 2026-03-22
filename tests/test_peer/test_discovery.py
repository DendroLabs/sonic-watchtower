"""Tests for LLDP-based peer discovery."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from watchtower.peer.discovery import PeerDiscovery
from watchtower.store.journal import Journal
from watchtower.store.topology import TopologyStore


@pytest.fixture()
def journal() -> Journal:
    return Journal(":memory:")


@pytest.fixture()
def topology(journal: Journal) -> TopologyStore:
    return TopologyStore(journal)


@pytest.fixture()
def discovery(topology: TopologyStore) -> PeerDiscovery:
    return PeerDiscovery(topology, peer_port=5950)


class TestPeerDiscovery:
    def test_no_neighbors(self, discovery: PeerDiscovery) -> None:
        assert discovery.discover_peers() == []

    @patch("watchtower.peer.discovery.socket.getaddrinfo")
    def test_discovers_single_peer(
        self,
        mock_getaddrinfo: object,
        discovery: PeerDiscovery,
        topology: TopologyStore,
    ) -> None:
        mock_getaddrinfo.return_value = [  # type: ignore[attr-defined]
            (2, 1, 6, "", ("10.0.0.1", 5950))
        ]
        topology.update_neighbor("Ethernet0", "spine-1", "Ethernet4")

        peers = discovery.discover_peers()
        assert len(peers) == 1
        assert peers[0].hostname == "spine-1"
        assert peers[0].address == "10.0.0.1"
        assert peers[0].port == 5950

    @patch("watchtower.peer.discovery.socket.getaddrinfo")
    def test_deduplicates_same_neighbor_on_multiple_ports(
        self,
        mock_getaddrinfo: object,
        discovery: PeerDiscovery,
        topology: TopologyStore,
    ) -> None:
        mock_getaddrinfo.return_value = [  # type: ignore[attr-defined]
            (2, 1, 6, "", ("10.0.0.1", 5950))
        ]
        topology.update_neighbor("Ethernet0", "spine-1", "Ethernet4")
        topology.update_neighbor("Ethernet4", "spine-1", "Ethernet8")

        peers = discovery.discover_peers()
        assert len(peers) == 1

    @patch("watchtower.peer.discovery.socket.getaddrinfo")
    def test_discovers_multiple_peers(
        self,
        mock_getaddrinfo: object,
        discovery: PeerDiscovery,
        topology: TopologyStore,
    ) -> None:
        def resolve(hostname: str, port: int, *args: object, **kwargs: object) -> list:
            addrs = {"spine-1": "10.0.0.1", "switch-b": "10.0.0.2"}
            addr = addrs.get(hostname, "127.0.0.1")
            return [(2, 1, 6, "", (addr, port))]

        mock_getaddrinfo.side_effect = resolve  # type: ignore[attr-defined]
        topology.update_neighbor("Ethernet0", "spine-1", "Ethernet4")
        topology.update_neighbor("Ethernet48", "switch-b", "Ethernet12")

        peers = discovery.discover_peers()
        assert len(peers) == 2
        hostnames = {p.hostname for p in peers}
        assert hostnames == {"spine-1", "switch-b"}

    @patch("watchtower.peer.discovery.socket.getaddrinfo")
    def test_skips_unresolvable_hostname(
        self,
        mock_getaddrinfo: object,
        discovery: PeerDiscovery,
        topology: TopologyStore,
    ) -> None:
        import socket as _socket

        mock_getaddrinfo.side_effect = _socket.gaierror("Name resolution failed")  # type: ignore[attr-defined]
        topology.update_neighbor("Ethernet0", "unknown-switch", "Ethernet0")

        peers = discovery.discover_peers()
        assert len(peers) == 0

    @patch("watchtower.peer.discovery.socket.getaddrinfo")
    def test_skips_empty_hostname(
        self,
        mock_getaddrinfo: object,
        discovery: PeerDiscovery,
        topology: TopologyStore,
    ) -> None:
        topology.update_neighbor("Ethernet0", "", "Ethernet0")
        peers = discovery.discover_peers()
        assert len(peers) == 0
        mock_getaddrinfo.assert_not_called()  # type: ignore[attr-defined]
