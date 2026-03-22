"""Tests for the gRPC server and client -- in-process, no TLS."""

from __future__ import annotations

from concurrent import futures
from typing import Any

import grpc
import pytest

from watchtower.peer.client import PeerClient
from watchtower.peer.proto import watchtower_pb2_grpc
from watchtower.peer.server import WatchtowerServicer


class MockHandler:
    """Mock handler that records calls and returns canned responses."""

    def __init__(self) -> None:
        self.heartbeats: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.topologies: list[dict[str, Any]] = []
        self.findings: list[dict[str, Any]] = []
        self.seen_finding_ids: set[str] = set()

    def handle_heartbeat(
        self,
        hostname: str,
        role: str,
        uptime_seconds: int,
        current_severity: str,
        active_finding_count: int,
        governor_state: str,
    ) -> dict[str, Any]:
        self.heartbeats.append(
            {
                "hostname": hostname,
                "role": role,
                "uptime_seconds": uptime_seconds,
                "current_severity": current_severity,
                "active_finding_count": active_finding_count,
                "governor_state": governor_state,
            }
        )
        return {
            "hostname": "test-server",
            "role": "leaf",
            "uptime_seconds": 1000,
            "current_severity": "ok",
            "active_finding_count": 0,
            "governor_state": "full",
        }

    def handle_event(
        self,
        hostname: str,
        timestamp: str,
        event_type: str,
        affected_port: str,
        summary: str,
        metrics: dict[str, str],
    ) -> bool:
        self.events.append(
            {
                "hostname": hostname,
                "timestamp": timestamp,
                "event_type": event_type,
                "affected_port": affected_port,
                "summary": summary,
                "metrics": metrics,
            }
        )
        return True

    def handle_topology(
        self,
        hostname: str,
        neighbors: list[dict[str, str]],
    ) -> bool:
        self.topologies.append({"hostname": hostname, "neighbors": neighbors})
        return True

    def handle_finding(
        self,
        hostname: str,
        origin_hostname: str,
        timestamp: str,
        severity: str,
        summary: str,
        affected_scope: str,
        ttl: int,
        finding_id: str,
    ) -> tuple[bool, bool]:
        already_seen = finding_id in self.seen_finding_ids
        self.seen_finding_ids.add(finding_id)
        self.findings.append(
            {
                "hostname": hostname,
                "origin_hostname": origin_hostname,
                "timestamp": timestamp,
                "severity": severity,
                "summary": summary,
                "affected_scope": affected_scope,
                "ttl": ttl,
                "finding_id": finding_id,
            }
        )
        return True, already_seen


@pytest.fixture()
def handler() -> MockHandler:
    return MockHandler()


@pytest.fixture()
def server_and_client(handler: MockHandler) -> tuple[grpc.Server, PeerClient]:
    """Start a gRPC server on an ephemeral port and return (server, client)."""
    servicer = WatchtowerServicer(handler)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    watchtower_pb2_grpc.add_WatchtowerPeerServicer_to_server(servicer, server)
    port = server.add_insecure_port("[::]:0")  # ephemeral port
    server.start()

    client = PeerClient(
        hostname="test-server",
        address="localhost",
        port=port,
        channel=grpc.insecure_channel(f"localhost:{port}"),
    )

    yield server, client  # type: ignore[misc]

    client.close()
    server.stop(grace=1)


@pytest.fixture()
def client(server_and_client: tuple[grpc.Server, PeerClient]) -> PeerClient:
    return server_and_client[1]


class TestHeartbeat:
    def test_heartbeat_exchange(
        self,
        client: PeerClient,
        handler: MockHandler,
    ) -> None:
        result = client.send_heartbeat(
            hostname="switch-a",
            role="leaf",
            uptime_seconds=500,
            current_severity="warning",
            active_finding_count=2,
            governor_state="throttled",
        )
        assert result is not None
        assert result["hostname"] == "test-server"
        assert result["current_severity"] == "ok"
        assert result["governor_state"] == "full"
        assert len(handler.heartbeats) == 1
        assert handler.heartbeats[0]["hostname"] == "switch-a"
        assert handler.heartbeats[0]["current_severity"] == "warning"

    def test_heartbeat_to_dead_server(self) -> None:
        client = PeerClient(
            hostname="dead",
            address="localhost",
            port=1,  # nothing listening
        )
        result = client.send_heartbeat(hostname="switch-a", timeout=0.5)
        assert result is None
        client.close()


class TestShareEvent:
    def test_share_event(
        self,
        client: PeerClient,
        handler: MockHandler,
    ) -> None:
        accepted = client.share_event(
            hostname="switch-a",
            timestamp="2026-03-21T14:00:00Z",
            event_type="link_change",
            affected_port="Ethernet48",
            summary="Link down on Ethernet48",
            metrics={"old_state": "up", "new_state": "down"},
        )
        assert accepted is True
        assert len(handler.events) == 1
        ev = handler.events[0]
        assert ev["hostname"] == "switch-a"
        assert ev["event_type"] == "link_change"
        assert ev["affected_port"] == "Ethernet48"
        assert ev["metrics"] == {"old_state": "up", "new_state": "down"}

    def test_share_event_no_metrics(
        self,
        client: PeerClient,
        handler: MockHandler,
    ) -> None:
        accepted = client.share_event(
            hostname="switch-a",
            timestamp="2026-03-21T14:00:00Z",
            event_type="anomaly",
            affected_port="Ethernet0",
            summary="RX errors above p99",
        )
        assert accepted is True
        assert handler.events[0]["metrics"] == {}

    def test_share_event_to_dead_server(self) -> None:
        client = PeerClient(hostname="dead", address="localhost", port=1)
        result = client.share_event(
            hostname="switch-a",
            timestamp="2026-03-21T14:00:00Z",
            event_type="link_change",
            affected_port="Ethernet0",
            summary="test",
            timeout=0.5,
        )
        assert result is False
        client.close()


class TestShareTopology:
    def test_share_topology(
        self,
        client: PeerClient,
        handler: MockHandler,
    ) -> None:
        neighbors = [
            {
                "local_port": "Ethernet0",
                "remote_host": "spine-1",
                "remote_port": "Ethernet4",
                "link_state": "up",
            },
            {
                "local_port": "Ethernet48",
                "remote_host": "switch-b",
                "remote_port": "Ethernet12",
                "link_state": "up",
            },
        ]
        accepted = client.share_topology(
            hostname="switch-a",
            neighbors=neighbors,
        )
        assert accepted is True
        assert len(handler.topologies) == 1
        assert len(handler.topologies[0]["neighbors"]) == 2

    def test_share_empty_topology(
        self,
        client: PeerClient,
        handler: MockHandler,
    ) -> None:
        accepted = client.share_topology(hostname="switch-a", neighbors=[])
        assert accepted is True


class TestShareFinding:
    def test_share_finding(
        self,
        client: PeerClient,
        handler: MockHandler,
    ) -> None:
        accepted, already_seen = client.share_finding(
            hostname="switch-a",
            origin_hostname="switch-a",
            timestamp="2026-03-21T14:00:00Z",
            severity="warning",
            summary="Link errors on Ethernet48",
            affected_scope="Ethernet48",
            ttl=3,
            finding_id="f-20260321-0001",
        )
        assert accepted is True
        assert already_seen is False
        assert len(handler.findings) == 1

    def test_share_finding_dedup(
        self,
        client: PeerClient,
        handler: MockHandler,
    ) -> None:
        # First share
        client.share_finding(
            hostname="switch-a",
            origin_hostname="switch-a",
            timestamp="2026-03-21T14:00:00Z",
            severity="warning",
            summary="test",
            affected_scope="Ethernet48",
            ttl=3,
            finding_id="f-20260321-0001",
        )
        # Second share of same finding
        accepted, already_seen = client.share_finding(
            hostname="switch-b",
            origin_hostname="switch-a",
            timestamp="2026-03-21T14:00:00Z",
            severity="warning",
            summary="test",
            affected_scope="Ethernet48",
            ttl=2,
            finding_id="f-20260321-0001",
        )
        assert accepted is True
        assert already_seen is True

    def test_share_finding_with_ttl_1(
        self,
        client: PeerClient,
        handler: MockHandler,
    ) -> None:
        accepted, already_seen = client.share_finding(
            hostname="switch-c",
            origin_hostname="switch-a",
            timestamp="2026-03-21T14:00:00Z",
            severity="critical",
            summary="Optic failure",
            affected_scope="Ethernet0",
            ttl=1,
            finding_id="f-20260321-0002",
        )
        assert accepted is True
        assert already_seen is False
        assert handler.findings[0]["ttl"] == 1

    def test_share_finding_to_dead_server(self) -> None:
        client = PeerClient(hostname="dead", address="localhost", port=1)
        accepted, already_seen = client.share_finding(
            hostname="switch-a",
            origin_hostname="switch-a",
            timestamp="2026-03-21T14:00:00Z",
            severity="warning",
            summary="test",
            affected_scope="Ethernet0",
            ttl=3,
            finding_id="f-20260321-0003",
            timeout=0.5,
        )
        assert accepted is False
        assert already_seen is False
        client.close()


class TestMultipleRPCs:
    def test_sequential_rpcs(
        self,
        client: PeerClient,
        handler: MockHandler,
    ) -> None:
        """Multiple RPCs to the same server in sequence."""
        client.send_heartbeat(hostname="switch-a")
        client.share_event(
            hostname="switch-a",
            timestamp="2026-03-21T14:00:00Z",
            event_type="anomaly",
            affected_port="Ethernet0",
            summary="test",
        )
        client.share_topology(hostname="switch-a", neighbors=[])
        client.share_finding(
            hostname="switch-a",
            origin_hostname="switch-a",
            timestamp="2026-03-21T14:00:00Z",
            severity="info",
            summary="test",
            affected_scope="",
            ttl=1,
            finding_id="f-0001",
        )
        assert len(handler.heartbeats) == 1
        assert len(handler.events) == 1
        assert len(handler.topologies) == 1
        assert len(handler.findings) == 1
