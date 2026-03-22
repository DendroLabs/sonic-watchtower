"""Tests for the topology diff analyzer."""

from __future__ import annotations

import pytest

from watchtower.analyzers.topology_diff import TopologyDiffAnalyzer
from watchtower.store.journal import Journal
from watchtower.store.topology import TopologyStore


@pytest.fixture()
def journal() -> Journal:
    return Journal(":memory:")


@pytest.fixture()
def topology(journal: Journal) -> TopologyStore:
    return TopologyStore(journal)


@pytest.fixture()
def analyzer(journal: Journal, topology: TopologyStore) -> TopologyDiffAnalyzer:
    return TopologyDiffAnalyzer(journal, topology)


class TestTopologyDiff:
    def test_first_run_no_changes(self, analyzer: TopologyDiffAnalyzer) -> None:
        lldp = {
            "Ethernet0": {"neighbor_hostname": "spine-1", "neighbor_port": "Ethernet4"},
        }
        changes = analyzer.analyze(current_lldp=lldp)
        assert changes == []  # first run, just records baseline

    def test_no_changes(self, analyzer: TopologyDiffAnalyzer) -> None:
        lldp = {
            "Ethernet0": {"neighbor_hostname": "spine-1", "neighbor_port": "Ethernet4"},
        }
        analyzer.analyze(current_lldp=lldp)  # baseline
        changes = analyzer.analyze(current_lldp=lldp)  # same data
        assert changes == []

    def test_neighbor_added(self, analyzer: TopologyDiffAnalyzer) -> None:
        lldp1 = {
            "Ethernet0": {"neighbor_hostname": "spine-1", "neighbor_port": "Ethernet4"},
        }
        analyzer.analyze(current_lldp=lldp1)

        lldp2 = {
            "Ethernet0": {"neighbor_hostname": "spine-1", "neighbor_port": "Ethernet4"},
            "Ethernet48": {"neighbor_hostname": "switch-b", "neighbor_port": "Ethernet12"},
        }
        changes = analyzer.analyze(current_lldp=lldp2)
        assert len(changes) == 1
        assert changes[0].change_type == "neighbor_added"
        assert changes[0].local_port == "Ethernet48"
        assert changes[0].neighbor_hostname == "switch-b"

    def test_neighbor_removed(self, analyzer: TopologyDiffAnalyzer) -> None:
        lldp1 = {
            "Ethernet0": {"neighbor_hostname": "spine-1", "neighbor_port": "Ethernet4"},
            "Ethernet48": {"neighbor_hostname": "switch-b", "neighbor_port": "Ethernet12"},
        }
        analyzer.analyze(current_lldp=lldp1)

        lldp2 = {
            "Ethernet0": {"neighbor_hostname": "spine-1", "neighbor_port": "Ethernet4"},
        }
        changes = analyzer.analyze(current_lldp=lldp2)
        assert len(changes) == 1
        assert changes[0].change_type == "neighbor_removed"
        assert changes[0].local_port == "Ethernet48"
        assert changes[0].neighbor_hostname == "switch-b"

    def test_neighbor_changed(self, analyzer: TopologyDiffAnalyzer) -> None:
        lldp1 = {
            "Ethernet0": {"neighbor_hostname": "spine-1", "neighbor_port": "Ethernet4"},
        }
        analyzer.analyze(current_lldp=lldp1)

        lldp2 = {
            "Ethernet0": {"neighbor_hostname": "spine-2", "neighbor_port": "Ethernet8"},
        }
        changes = analyzer.analyze(current_lldp=lldp2)
        assert len(changes) == 1
        assert changes[0].change_type == "neighbor_changed"
        assert changes[0].local_port == "Ethernet0"
        assert changes[0].neighbor_hostname == "spine-2"
        assert changes[0].old_neighbor_hostname == "spine-1"
        assert changes[0].old_neighbor_port == "Ethernet4"

    def test_multiple_changes(self, analyzer: TopologyDiffAnalyzer) -> None:
        lldp1 = {
            "Ethernet0": {"neighbor_hostname": "spine-1", "neighbor_port": "Ethernet4"},
            "Ethernet4": {"neighbor_hostname": "spine-2", "neighbor_port": "Ethernet8"},
        }
        analyzer.analyze(current_lldp=lldp1)

        lldp2 = {
            "Ethernet4": {"neighbor_hostname": "spine-2", "neighbor_port": "Ethernet8"},
            "Ethernet48": {"neighbor_hostname": "switch-c", "neighbor_port": "Ethernet0"},
        }
        changes = analyzer.analyze(current_lldp=lldp2)
        change_types = {(c.change_type, c.local_port) for c in changes}
        assert ("neighbor_removed", "Ethernet0") in change_types
        assert ("neighbor_added", "Ethernet48") in change_types

    def test_empty_hostname_skipped(self, analyzer: TopologyDiffAnalyzer) -> None:
        lldp1 = {
            "Ethernet0": {"neighbor_hostname": "", "neighbor_port": "Ethernet4"},
        }
        analyzer.analyze(current_lldp=lldp1)
        # Empty hostnames should be filtered out, so no diff
        lldp2: dict = {}
        changes = analyzer.analyze(current_lldp=lldp2)
        assert changes == []

    def test_reads_from_store_when_no_lldp_provided(
        self,
        analyzer: TopologyDiffAnalyzer,
        topology: TopologyStore,
    ) -> None:
        topology.update_neighbor("Ethernet0", "spine-1", "Ethernet4")
        # First call sets baseline from store
        changes = analyzer.analyze()
        assert changes == []

    def test_sequential_diffs(self, analyzer: TopologyDiffAnalyzer) -> None:
        """Changes are tracked relative to the previous call, not the first."""
        lldp1 = {
            "Ethernet0": {"neighbor_hostname": "spine-1", "neighbor_port": "Ethernet4"},
        }
        analyzer.analyze(current_lldp=lldp1)

        lldp2 = {
            "Ethernet0": {"neighbor_hostname": "spine-1", "neighbor_port": "Ethernet4"},
            "Ethernet48": {"neighbor_hostname": "switch-b", "neighbor_port": "Ethernet12"},
        }
        changes1 = analyzer.analyze(current_lldp=lldp2)
        assert len(changes1) == 1
        assert changes1[0].change_type == "neighbor_added"

        # Now remove Ethernet48 again
        changes2 = analyzer.analyze(current_lldp=lldp1)
        assert len(changes2) == 1
        assert changes2[0].change_type == "neighbor_removed"
