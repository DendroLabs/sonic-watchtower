"""Tests for PeerStateStore."""

from __future__ import annotations

import pytest

from watchtower.peer.state import PeerStateStore
from watchtower.store.journal import Journal


@pytest.fixture()
def journal() -> Journal:
    return Journal(":memory:")


@pytest.fixture()
def store(journal: Journal) -> PeerStateStore:
    return PeerStateStore(journal)


class TestPeerStateStore:
    def test_update_heartbeat_creates_peer(self, store: PeerStateStore) -> None:
        store.update_heartbeat("spine-1", severity="ok", active_finding_count=0)
        peer = store.get_peer("spine-1")
        assert peer is not None
        assert peer["peer_hostname"] == "spine-1"
        assert peer["peer_severity"] == "ok"
        assert peer["active_finding_count"] == 0

    def test_update_heartbeat_updates_existing(self, store: PeerStateStore) -> None:
        store.update_heartbeat("spine-1", severity="ok")
        store.update_heartbeat("spine-1", severity="warning", active_finding_count=3)
        peer = store.get_peer("spine-1")
        assert peer is not None
        assert peer["peer_severity"] == "warning"
        assert peer["active_finding_count"] == 3

    def test_update_heartbeat_all_fields(self, store: PeerStateStore) -> None:
        store.update_heartbeat(
            "switch-b",
            severity="critical",
            active_finding_count=5,
            governor_state="throttled",
            role="central",
        )
        peer = store.get_peer("switch-b")
        assert peer is not None
        assert peer["governor_state"] == "throttled"
        assert peer["role"] == "central"

    def test_get_peer_not_found(self, store: PeerStateStore) -> None:
        assert store.get_peer("nonexistent") is None

    def test_get_all_peers(self, store: PeerStateStore) -> None:
        store.update_heartbeat("spine-1", severity="ok")
        store.update_heartbeat("spine-2", severity="warning")
        store.update_heartbeat("switch-b", severity="ok")
        peers = store.get_all_peers()
        assert len(peers) == 3
        hostnames = [p["peer_hostname"] for p in peers]
        assert hostnames == ["spine-1", "spine-2", "switch-b"]  # sorted

    def test_get_all_peers_empty(self, store: PeerStateStore) -> None:
        assert store.get_all_peers() == []

    def test_update_event_summary(self, store: PeerStateStore) -> None:
        store.update_heartbeat("spine-1", severity="ok")
        store.update_event_summary("spine-1", "link flap on Ethernet0")
        peer = store.get_peer("spine-1")
        assert peer is not None
        assert peer["last_event_summary"] == "link flap on Ethernet0"

    def test_update_event_summary_no_peer(self, store: PeerStateStore) -> None:
        # No error, just no-op (UPDATE on nonexistent row)
        store.update_event_summary("ghost", "something happened")
        assert store.get_peer("ghost") is None

    def test_remove_peer(self, store: PeerStateStore) -> None:
        store.update_heartbeat("spine-1", severity="ok")
        store.remove_peer("spine-1")
        assert store.get_peer("spine-1") is None

    def test_remove_nonexistent_peer(self, store: PeerStateStore) -> None:
        # Should not raise
        store.remove_peer("nonexistent")

    def test_get_stale_peers(self, store: PeerStateStore) -> None:
        store.update_heartbeat("spine-1", severity="ok")
        # All peers just heartbeated, none should be stale with a generous window
        stale = store.get_stale_peers(max_age_seconds=90)
        assert len(stale) == 0

    def test_get_stale_peers_with_old_heartbeat(self, store: PeerStateStore) -> None:
        # Manually insert a peer with an old heartbeat
        store._journal.conn.execute(
            "INSERT INTO peer_state (peer_hostname, last_heartbeat, peer_severity, updated_at)"
            " VALUES (?, ?, ?, ?)",
            ("old-switch", "2020-01-01T00:00:00Z", "ok", "2020-01-01T00:00:00Z"),
        )
        store._journal.conn.commit()
        stale = store.get_stale_peers(max_age_seconds=10)
        assert len(stale) == 1
        assert stale[0]["peer_hostname"] == "old-switch"


class TestPeerTopology:
    def test_update_peer_topology(self, store: PeerStateStore) -> None:
        neighbors = [
            {"local_port": "Ethernet0", "remote_host": "spine-1", "remote_port": "Ethernet4"},
            {"local_port": "Ethernet48", "remote_host": "switch-c", "remote_port": "Ethernet0"},
        ]
        store.update_peer_topology("switch-b", neighbors)
        topo = store.get_peer_topology("switch-b")
        assert len(topo) == 2
        assert topo[0]["local_port"] == "Ethernet0"
        assert topo[0]["neighbor_hostname"] == "spine-1"

    def test_update_peer_topology_replaces(self, store: PeerStateStore) -> None:
        store.update_peer_topology(
            "switch-b",
            [
                {"local_port": "Ethernet0", "remote_host": "spine-1", "remote_port": "Ethernet4"},
            ],
        )
        # Replace with a different topology
        store.update_peer_topology(
            "switch-b",
            [
                {"local_port": "Ethernet4", "remote_host": "spine-2", "remote_port": "Ethernet8"},
            ],
        )
        topo = store.get_peer_topology("switch-b")
        assert len(topo) == 1
        assert topo[0]["local_port"] == "Ethernet4"

    def test_get_peer_topology_empty(self, store: PeerStateStore) -> None:
        assert store.get_peer_topology("nonexistent") == []

    def test_get_fabric_topology(self, store: PeerStateStore) -> None:
        store.update_peer_topology(
            "switch-b",
            [
                {"local_port": "Ethernet0", "remote_host": "spine-1", "remote_port": "Ethernet4"},
            ],
        )
        store.update_peer_topology(
            "switch-c",
            [
                {"local_port": "Ethernet0", "remote_host": "spine-1", "remote_port": "Ethernet8"},
            ],
        )
        fabric = store.get_fabric_topology()
        assert len(fabric) == 2
        # Sorted by peer_hostname, then local_port
        assert fabric[0]["peer_hostname"] == "switch-b"
        assert fabric[1]["peer_hostname"] == "switch-c"

    def test_topology_with_link_state(self, store: PeerStateStore) -> None:
        store.update_peer_topology(
            "switch-b",
            [
                {
                    "local_port": "Ethernet0",
                    "remote_host": "spine-1",
                    "remote_port": "Ethernet4",
                    "link_state": "down",
                },
            ],
        )
        topo = store.get_peer_topology("switch-b")
        assert topo[0]["link_state"] == "down"


class TestSchemaMigration:
    def test_new_columns_exist(self, journal: Journal) -> None:
        """Verify the migration added the new columns to peer_state."""
        row = journal.conn.execute("PRAGMA table_info(peer_state)").fetchall()
        col_names = [r["name"] for r in row]
        assert "role" in col_names
        assert "active_finding_count" in col_names
        assert "governor_state" in col_names

    def test_peer_topology_table_exists(self, journal: Journal) -> None:
        """Verify the peer_topology table was created."""
        row = journal.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='peer_topology'"
        ).fetchone()
        assert row is not None

    def test_migration_idempotent(self, journal: Journal) -> None:
        """Running migrations again should not raise."""
        journal._run_migrations()
        journal._run_migrations()
        # Should complete without error
