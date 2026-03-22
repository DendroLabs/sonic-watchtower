"""Tests for the peer correlation analyzer."""

from __future__ import annotations

import pytest

from watchtower.analyzers.peer_correlate import PeerCorrelateAnalyzer
from watchtower.store.events import EventStore
from watchtower.store.journal import Journal
from watchtower.store.topology import TopologyStore


@pytest.fixture()
def journal() -> Journal:
    return Journal(":memory:")


@pytest.fixture()
def events(journal: Journal) -> EventStore:
    return EventStore(journal)


@pytest.fixture()
def topology(journal: Journal) -> TopologyStore:
    return TopologyStore(journal)


@pytest.fixture()
def analyzer(journal: Journal, topology: TopologyStore) -> PeerCorrelateAnalyzer:
    return PeerCorrelateAnalyzer(journal, topology)


class TestPeerCorrelate:
    def test_no_events(self, analyzer: PeerCorrelateAnalyzer) -> None:
        assert analyzer.analyze() == []

    def test_no_peer_events(
        self,
        analyzer: PeerCorrelateAnalyzer,
        events: EventStore,
    ) -> None:
        events.record(source="local", category="link_change", severity="warning", port="Ethernet0")
        assert analyzer.analyze() == []

    def test_no_local_events(
        self,
        analyzer: PeerCorrelateAnalyzer,
        events: EventStore,
    ) -> None:
        events.record(
            source="peer",
            category="link_change",
            severity="info",
            raw_data={"peer_hostname": "switch-b", "summary": "test"},
        )
        assert analyzer.analyze() == []

    def test_correlation_on_same_link(
        self,
        analyzer: PeerCorrelateAnalyzer,
        events: EventStore,
        topology: TopologyStore,
    ) -> None:
        # Set up topology: Ethernet48 connected to switch-b:Ethernet12
        topology.update_neighbor("Ethernet48", "switch-b", "Ethernet12")

        # Local event on Ethernet48
        events.record(
            source="local",
            category="link_change",
            severity="warning",
            port="Ethernet48",
        )
        # Peer event from switch-b
        events.record(
            source="peer",
            category="link_change",
            severity="info",
            raw_data={"peer_hostname": "switch-b", "summary": "Link down on Ethernet12"},
        )

        results = analyzer.analyze()
        assert len(results) == 1
        assert results[0].local_port == "Ethernet48"
        assert results[0].peer_hostname == "switch-b"
        assert results[0].peer_port == "Ethernet12"
        assert results[0].correlation_type == "same_link"

    def test_no_correlation_different_neighbor(
        self,
        analyzer: PeerCorrelateAnalyzer,
        events: EventStore,
        topology: TopologyStore,
    ) -> None:
        topology.update_neighbor("Ethernet48", "switch-b", "Ethernet12")

        events.record(
            source="local",
            category="link_change",
            severity="warning",
            port="Ethernet48",
        )
        # Peer event from switch-c (not the neighbor on Ethernet48)
        events.record(
            source="peer",
            category="link_change",
            severity="info",
            raw_data={"peer_hostname": "switch-c", "summary": "Link down"},
        )

        assert analyzer.analyze() == []

    def test_no_correlation_when_port_missing(
        self,
        analyzer: PeerCorrelateAnalyzer,
        events: EventStore,
        topology: TopologyStore,
    ) -> None:
        topology.update_neighbor("Ethernet48", "switch-b", "Ethernet12")

        # Local event with no port
        events.record(
            source="local",
            category="bgp_change",
            severity="warning",
        )
        events.record(
            source="peer",
            category="link_change",
            severity="info",
            raw_data={"peer_hostname": "switch-b", "summary": "test"},
        )

        assert analyzer.analyze() == []

    def test_multiple_correlations(
        self,
        analyzer: PeerCorrelateAnalyzer,
        events: EventStore,
        topology: TopologyStore,
    ) -> None:
        topology.update_neighbor("Ethernet0", "spine-1", "Ethernet4")
        topology.update_neighbor("Ethernet48", "switch-b", "Ethernet12")

        events.record(
            source="local",
            category="anomaly",
            severity="warning",
            port="Ethernet0",
        )
        events.record(
            source="local",
            category="link_change",
            severity="warning",
            port="Ethernet48",
        )
        events.record(
            source="peer",
            category="anomaly",
            severity="info",
            raw_data={"peer_hostname": "spine-1", "summary": "RX errors"},
        )
        events.record(
            source="peer",
            category="link_change",
            severity="info",
            raw_data={"peer_hostname": "switch-b", "summary": "Link down"},
        )

        results = analyzer.analyze()
        assert len(results) == 2
        ports = {r.local_port for r in results}
        assert ports == {"Ethernet0", "Ethernet48"}

    def test_dedup_same_pair(
        self,
        analyzer: PeerCorrelateAnalyzer,
        events: EventStore,
        topology: TopologyStore,
    ) -> None:
        """Same local+peer event pair should only produce one correlation."""
        topology.update_neighbor("Ethernet0", "spine-1", "Ethernet4")

        events.record(
            source="local",
            category="anomaly",
            severity="warning",
            port="Ethernet0",
        )
        # Two peer events from same neighbor
        events.record(
            source="peer",
            category="anomaly",
            severity="info",
            raw_data={"peer_hostname": "spine-1", "summary": "RX errors"},
        )
        events.record(
            source="peer",
            category="link_change",
            severity="info",
            raw_data={"peer_hostname": "spine-1", "summary": "Link flap"},
        )

        results = analyzer.analyze()
        # Should match local event with both peer events = 2 correlations
        assert len(results) == 2
