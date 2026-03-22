"""Tests for PeerManager -- lifecycle, gossip, dedup, integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from watchtower.config import PeerConfig, WatchtowerConfig
from watchtower.governor import ResourceGovernor
from watchtower.peer import PeerManager
from watchtower.peer.client import PeerClient
from watchtower.store.events import EventStore
from watchtower.store.findings import FindingsStore
from watchtower.store.journal import Journal
from watchtower.store.topology import TopologyStore


@pytest.fixture()
def journal() -> Journal:
    return Journal(":memory:")


@pytest.fixture()
def config() -> WatchtowerConfig:
    return WatchtowerConfig(
        hostname="test-switch",
        peer=PeerConfig(
            enabled=True,
            port=0,  # will be overridden
            heartbeat_interval=1,
            topology_share_interval=1,
            finding_ttl=3,
        ),
    )


@pytest.fixture()
def manager(config: WatchtowerConfig, journal: Journal) -> PeerManager:
    topology = TopologyStore(journal)
    events = EventStore(journal)
    findings = FindingsStore(journal)
    governor = ResourceGovernor()
    return PeerManager(
        config=config,
        journal=journal,
        topology_store=topology,
        events=events,
        findings=findings,
        governor=governor,
    )


class TestPeerManagerLifecycle:
    def test_disabled_by_config(self, journal: Journal) -> None:
        config = WatchtowerConfig(peer=PeerConfig(enabled=False))
        mgr = PeerManager(
            config=config, journal=journal,
            topology_store=TopologyStore(journal),
            events=EventStore(journal),
            findings=FindingsStore(journal),
            governor=ResourceGovernor(),
        )
        assert not mgr.enabled

    def test_disabled_when_tls_missing(self, manager: PeerManager) -> None:
        # Default TLS paths don't exist, so start() should disable
        manager.start()
        assert not manager.enabled

    def test_start_insecure(self, manager: PeerManager) -> None:
        manager._config.peer.port = 0  # ephemeral
        # Use add_insecure_port manually to get a port
        manager.start_insecure()
        assert manager.enabled
        manager.stop()

    def test_stop_closes_clients(self, manager: PeerManager) -> None:
        # Add a mock client
        client = PeerClient(hostname="test", address="localhost", port=1)
        manager._clients["test"] = client
        manager.stop()
        assert len(manager.clients) == 0


class TestHeartbeatHandler:
    def test_handle_heartbeat_records_state(self, manager: PeerManager) -> None:
        result = manager.handle_heartbeat(
            hostname="spine-1",
            role="leaf",
            uptime_seconds=100,
            current_severity="warning",
            active_finding_count=2,
            governor_state="throttled",
        )
        # Should return our status
        assert result["hostname"] == "test-switch"
        # Should have recorded peer state
        peer = manager.peer_state.get_peer("spine-1")
        assert peer is not None
        assert peer["peer_severity"] == "warning"
        assert peer["active_finding_count"] == 2

    def test_handle_heartbeat_severity_reflects_findings(
        self, manager: PeerManager, journal: Journal,
    ) -> None:
        findings = FindingsStore(journal)
        findings.create(severity="warning", summary="test warning")
        result = manager.handle_heartbeat(
            hostname="spine-1", role="leaf", uptime_seconds=0,
            current_severity="ok", active_finding_count=0, governor_state="full",
        )
        assert result["current_severity"] == "warning"


class TestEventHandler:
    def test_handle_event_records_in_store(
        self, manager: PeerManager, journal: Journal,
    ) -> None:
        manager.handle_event(
            hostname="switch-b",
            timestamp="2026-03-21T14:00:00Z",
            event_type="link_change",
            affected_port="Ethernet48",
            summary="Link down on Ethernet48",
            metrics={"old_state": "up"},
        )
        events = EventStore(journal)
        recent = events.get_recent(seconds=60)
        assert len(recent) == 1
        assert recent[0]["source"] == "peer"
        assert recent[0]["category"] == "link_change"


class TestTopologyHandler:
    def test_handle_topology_stores_fragment(self, manager: PeerManager) -> None:
        manager.handle_topology(
            hostname="switch-b",
            neighbors=[
                {"local_port": "Ethernet0", "remote_host": "spine-1",
                 "remote_port": "Ethernet4", "link_state": "up"},
            ],
        )
        topo = manager.peer_state.get_peer_topology("switch-b")
        assert len(topo) == 1
        assert topo[0]["neighbor_hostname"] == "spine-1"


class TestFindingHandler:
    def test_handle_finding_new(self, manager: PeerManager) -> None:
        accepted, already_seen = manager.handle_finding(
            hostname="switch-b",
            origin_hostname="switch-b",
            timestamp="2026-03-21T14:00:00Z",
            severity="warning",
            summary="Link errors",
            affected_scope="Ethernet48",
            ttl=3,
            finding_id="f-20260321-0001",
        )
        assert accepted is True
        assert already_seen is False

    def test_handle_finding_dedup(self, manager: PeerManager) -> None:
        manager.handle_finding(
            hostname="switch-b", origin_hostname="switch-b",
            timestamp="2026-03-21T14:00:00Z", severity="warning",
            summary="Link errors", affected_scope="Ethernet48",
            ttl=3, finding_id="f-20260321-0001",
        )
        _, already_seen = manager.handle_finding(
            hostname="switch-c", origin_hostname="switch-b",
            timestamp="2026-03-21T14:00:00Z", severity="warning",
            summary="Link errors", affected_scope="Ethernet48",
            ttl=2, finding_id="f-20260321-0001",
        )
        assert already_seen is True

    def test_finding_gossip_excludes_sender(self, manager: PeerManager) -> None:
        """When a finding arrives with TTL > 1, gossip to other peers but not sender."""
        # Set up two mock clients
        calls_b: list[str] = []
        calls_c: list[str] = []

        class FakeClient:
            def __init__(self, name: str, log: list[str]) -> None:
                self.hostname = name
                self._log = log

            def share_finding(self, **kwargs: object) -> tuple[bool, bool]:
                self._log.append("called")
                return True, False

            def close(self) -> None:
                pass

        manager._clients["switch-b"] = FakeClient("switch-b", calls_b)  # type: ignore[assignment]
        manager._clients["switch-c"] = FakeClient("switch-c", calls_c)  # type: ignore[assignment]

        # Finding arrives from switch-b with TTL=3
        manager.handle_finding(
            hostname="switch-b", origin_hostname="switch-b",
            timestamp="2026-03-21T14:00:00Z", severity="warning",
            summary="test", affected_scope="Ethernet0",
            ttl=3, finding_id="f-20260321-0099",
        )
        # Should gossip to switch-c but NOT back to switch-b
        assert len(calls_b) == 0
        assert len(calls_c) == 1

    def test_finding_no_gossip_at_ttl_1(self, manager: PeerManager) -> None:
        """Findings with TTL=1 should not be re-gossiped."""
        calls: list[str] = []

        class FakeClient:
            hostname = "switch-c"
            def share_finding(self, **kwargs: object) -> tuple[bool, bool]:
                calls.append("called")
                return True, False
            def close(self) -> None:
                pass

        manager._clients["switch-c"] = FakeClient()  # type: ignore[assignment]

        manager.handle_finding(
            hostname="switch-b", origin_hostname="switch-b",
            timestamp="2026-03-21T14:00:00Z", severity="warning",
            summary="test", affected_scope="Ethernet0",
            ttl=1, finding_id="f-20260321-0100",
        )
        assert len(calls) == 0


class TestShareFinding:
    def test_share_finding_only_warning_and_above(self, manager: PeerManager) -> None:
        """Info-severity findings should not be gossiped."""
        calls: list[str] = []

        class FakeClient:
            hostname = "switch-c"
            def share_finding(self, **kwargs: object) -> tuple[bool, bool]:
                calls.append("called")
                return True, False
            def close(self) -> None:
                pass

        manager._clients["switch-c"] = FakeClient()  # type: ignore[assignment]

        manager.share_finding(
            finding_id="f-0001", severity="info",
            summary="something minor", affected_scope="",
        )
        assert len(calls) == 0

    def test_share_finding_sends_to_peers(self, manager: PeerManager) -> None:
        calls: list[dict] = []

        class FakeClient:
            hostname = "switch-c"
            def share_finding(self, **kwargs: object) -> tuple[bool, bool]:
                calls.append(dict(kwargs))
                return True, False
            def close(self) -> None:
                pass

        manager._clients["switch-c"] = FakeClient()  # type: ignore[assignment]

        manager.share_finding(
            finding_id="f-0001", severity="warning",
            summary="Link errors", affected_scope="Ethernet48",
        )
        assert len(calls) == 1
        assert calls[0]["finding_id"] == "f-0001"
        assert calls[0]["ttl"] == 3


class TestDedup:
    def test_seen_findings_bounded(self, manager: PeerManager) -> None:
        """The seen set should not grow beyond _MAX_SEEN_FINDINGS."""
        from watchtower.peer import _MAX_SEEN_FINDINGS

        for i in range(_MAX_SEEN_FINDINGS + 100):
            manager._mark_seen(f"f-{i:06d}")
        assert len(manager._seen_finding_ids) == _MAX_SEEN_FINDINGS
        # Oldest should be evicted
        assert not manager._has_seen("f-000000")
        assert manager._has_seen(f"f-{_MAX_SEEN_FINDINGS + 99:06d}")


class TestRefreshPeers:
    @patch("watchtower.peer.discovery.socket.getaddrinfo")
    def test_refresh_creates_clients(
        self, mock_getaddrinfo: object, manager: PeerManager,
    ) -> None:
        mock_getaddrinfo.return_value = [  # type: ignore[attr-defined]
            (2, 1, 6, "", ("10.0.0.1", 5950))
        ]
        manager._topology_store.update_neighbor("Ethernet0", "spine-1", "Ethernet4")
        manager.refresh_peers()
        assert "spine-1" in manager.clients

    @patch("watchtower.peer.discovery.socket.getaddrinfo")
    def test_refresh_removes_stale_clients(
        self, mock_getaddrinfo: object, manager: PeerManager,
    ) -> None:
        mock_getaddrinfo.return_value = [  # type: ignore[attr-defined]
            (2, 1, 6, "", ("10.0.0.1", 5950))
        ]
        # Add a client manually that is not in topology
        manager._clients["old-switch"] = PeerClient(
            hostname="old-switch", address="localhost", port=1,
        )
        manager._topology_store.update_neighbor("Ethernet0", "spine-1", "Ethernet4")
        manager.refresh_peers()
        assert "spine-1" in manager.clients
        assert "old-switch" not in manager.clients
