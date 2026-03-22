"""Peer protocol -- PeerManager orchestrates gRPC server, clients, and gossip."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from concurrent import futures
from pathlib import Path
from typing import Any

import grpc

from watchtower.config import WatchtowerConfig
from watchtower.governor import GovernorState, ResourceGovernor
from watchtower.peer.client import PeerClient
from watchtower.peer.discovery import PeerDiscovery, PeerEndpoint
from watchtower.peer.proto import watchtower_pb2_grpc
from watchtower.peer.server import WatchtowerServicer
from watchtower.peer.state import PeerStateStore
from watchtower.store.events import EventStore
from watchtower.store.findings import FindingsStore
from watchtower.store.journal import Journal
from watchtower.store.topology import TopologyStore

logger = logging.getLogger("watchtower.peer")

# Max finding IDs to track for deduplication
_MAX_SEEN_FINDINGS = 1000


class PeerManager:
    """Orchestrates all peer protocol operations.

    Manages the gRPC server, client connections, peer discovery,
    heartbeats, event/finding gossip, and topology sharing.
    """

    def __init__(
        self,
        config: WatchtowerConfig,
        journal: Journal,
        topology_store: TopologyStore,
        events: EventStore,
        findings: FindingsStore,
        governor: ResourceGovernor,
    ):
        self._config = config
        self._journal = journal
        self._topology_store = topology_store
        self._events = events
        self._findings = findings
        self._governor = governor

        self._peer_state = PeerStateStore(journal)
        self._discovery = PeerDiscovery(topology_store, config.peer.port)
        self._clients: dict[str, PeerClient] = {}
        self._server: grpc.Server | None = None
        self._enabled = config.peer.enabled
        self._start_time = time.time()

        # Deduplication: bounded OrderedDict acts as an LRU set
        self._seen_finding_ids: OrderedDict[str, None] = OrderedDict()

        # Timing for periodic operations
        self._last_heartbeat_time: float = 0.0
        self._last_topology_share_time: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def peer_state(self) -> PeerStateStore:
        return self._peer_state

    @property
    def clients(self) -> dict[str, PeerClient]:
        return self._clients

    def start(self) -> None:
        """Start the gRPC server. Disables gracefully if TLS certs are missing."""
        if not self._enabled:
            logger.info("Peer protocol disabled by configuration")
            return

        tls = self._config.peer.tls
        tls_available = all(
            Path(p).exists() for p in [tls.cert, tls.key, tls.ca]
        )

        if not tls_available:
            logger.warning(
                "TLS certs not found -- peer protocol disabled (local-only mode)"
            )
            self._enabled = False
            return

        self._start_server(tls_available=True)

    def start_insecure(self) -> None:
        """Start the gRPC server without TLS (for testing only)."""
        if not self._enabled:
            return
        self._start_server(tls_available=False)

    def _start_server(self, tls_available: bool) -> None:
        """Start the gRPC server in daemon threads."""
        servicer = WatchtowerServicer(self)
        self._server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="peer-grpc"),
        )
        watchtower_pb2_grpc.add_WatchtowerPeerServicer_to_server(servicer, self._server)

        port = self._config.peer.port
        if tls_available:
            tls = self._config.peer.tls
            ca = Path(tls.ca).read_bytes()
            cert = Path(tls.cert).read_bytes()
            key = Path(tls.key).read_bytes()
            credentials = grpc.ssl_server_credentials(
                [(key, cert)],
                root_certificates=ca,
                require_client_auth=True,
            )
            self._server.add_secure_port(f"[::]:{port}", credentials)
        else:
            self._server.add_insecure_port(f"[::]:{port}")

        self._server.start()
        logger.info(
            "Peer gRPC server started on port %d (TLS=%s)", port, tls_available
        )

    def stop(self) -> None:
        """Shut down the gRPC server and close all client connections."""
        if self._server is not None:
            self._server.stop(grace=2)
            self._server = None
            logger.info("Peer gRPC server stopped")

        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def refresh_peers(self) -> None:
        """Re-discover peers from LLDP topology and manage client connections."""
        if not self._enabled:
            return

        endpoints = self._discovery.discover_peers()
        discovered: set[str] = set()

        for ep in endpoints:
            discovered.add(ep.hostname)
            if ep.hostname not in self._clients:
                self._connect_peer(ep)

        # Remove clients for peers no longer in topology
        stale = set(self._clients.keys()) - discovered
        for hostname in stale:
            logger.info("Removing stale peer: %s", hostname)
            self._clients[hostname].close()
            del self._clients[hostname]

    def _connect_peer(self, endpoint: PeerEndpoint) -> None:
        """Create a client connection to a discovered peer."""
        tls = self._config.peer.tls
        tls_available = all(
            Path(p).exists() for p in [tls.cert, tls.key, tls.ca]
        )

        try:
            client = PeerClient(
                hostname=endpoint.hostname,
                address=endpoint.address,
                port=endpoint.port,
                tls_config=tls if tls_available else None,
            )
            self._clients[endpoint.hostname] = client
            logger.info(
                "Connected to peer: %s (%s:%d)",
                endpoint.hostname, endpoint.address, endpoint.port,
            )
        except Exception:
            logger.warning("Failed to connect to peer: %s", endpoint.hostname)

    # ── Periodic operations (called from poll cycle) ──

    def send_heartbeats(self) -> None:
        """Send heartbeats to all connected peers if enough time has elapsed."""
        if not self._enabled:
            return

        now = time.time()
        if now - self._last_heartbeat_time < self._config.peer.heartbeat_interval:
            return
        self._last_heartbeat_time = now

        for hostname, client in list(self._clients.items()):
            result = client.send_heartbeat(
                hostname=self._config.hostname,
                role=self._config.hierarchy.role,
                uptime_seconds=int(now - self._start_time),
                current_severity=self._get_current_severity(),
                active_finding_count=self._findings.count_active().get("total", 0),
                governor_state=self._governor.state.value,
            )
            if result is not None:
                self._peer_state.update_heartbeat(
                    hostname=hostname,
                    severity=result["current_severity"],
                    active_finding_count=result["active_finding_count"],
                    governor_state=result["governor_state"],
                    role=result["role"],
                )

    def share_topology_fragment(self) -> None:
        """Share our local topology with peers if enough time has elapsed."""
        if not self._enabled:
            return

        now = time.time()
        if now - self._last_topology_share_time < self._config.peer.topology_share_interval:
            return
        self._last_topology_share_time = now

        entries = self._topology_store.get_all()
        neighbors = [
            {
                "local_port": e["local_port"],
                "remote_host": e["neighbor_hostname"],
                "remote_port": e["neighbor_port"],
                "link_state": "up",
            }
            for e in entries
        ]

        for client in self._clients.values():
            client.share_topology(
                hostname=self._config.hostname,
                neighbors=neighbors,
            )

    def share_event(
        self, event_type: str, port: str, summary: str,
        metrics: dict[str, str] | None = None,
    ) -> None:
        """Share a local event with all direct peers (1-hop only)."""
        if not self._enabled or self._governor.state == GovernorState.DORMANT:
            return

        from datetime import UTC, datetime
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        for client in self._clients.values():
            client.share_event(
                hostname=self._config.hostname,
                timestamp=timestamp,
                event_type=event_type,
                affected_port=port,
                summary=summary,
                metrics=metrics or {},
            )

    def share_finding(
        self, finding_id: str, severity: str, summary: str,
        affected_scope: str = "",
    ) -> None:
        """Share a new local finding with peers using TTL-limited gossip."""
        if not self._enabled or self._governor.state == GovernorState.DORMANT:
            return

        # Only gossip findings with severity >= warning
        if severity not in ("warning", "critical"):
            return

        self._mark_seen(finding_id)

        from datetime import UTC, datetime
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        for client in self._clients.values():
            client.share_finding(
                hostname=self._config.hostname,
                origin_hostname=self._config.hostname,
                timestamp=timestamp,
                severity=severity,
                summary=summary,
                affected_scope=affected_scope,
                ttl=self._config.peer.finding_ttl,
                finding_id=finding_id,
            )

    def get_peer_statuses(self) -> list[dict]:
        """Get status of all known peers for CLI display."""
        return self._peer_state.get_all_peers()

    # ── PeerMessageHandler interface (called by WatchtowerServicer) ──

    def handle_heartbeat(
        self, hostname: str, role: str, uptime_seconds: int,
        current_severity: str, active_finding_count: int, governor_state: str,
    ) -> dict[str, Any]:
        self._peer_state.update_heartbeat(
            hostname=hostname,
            severity=current_severity,
            active_finding_count=active_finding_count,
            governor_state=governor_state,
            role=role,
        )
        return {
            "hostname": self._config.hostname,
            "role": self._config.hierarchy.role,
            "uptime_seconds": int(time.time() - self._start_time),
            "current_severity": self._get_current_severity(),
            "active_finding_count": self._findings.count_active().get("total", 0),
            "governor_state": self._governor.state.value,
        }

    def handle_event(
        self, hostname: str, timestamp: str, event_type: str,
        affected_port: str, summary: str, metrics: dict[str, str],
    ) -> bool:
        self._events.record(
            source="peer",
            category=event_type,
            severity="info",
            port=affected_port if affected_port else None,
            raw_data={
                "peer_hostname": hostname,
                "timestamp": timestamp,
                "summary": summary,
                "metrics": metrics,
            },
        )
        self._peer_state.update_event_summary(hostname, summary)
        return True

    def handle_topology(
        self, hostname: str, neighbors: list[dict[str, str]],
    ) -> bool:
        self._peer_state.update_peer_topology(hostname, neighbors)
        return True

    def handle_finding(
        self, hostname: str, origin_hostname: str, timestamp: str,
        severity: str, summary: str, affected_scope: str,
        ttl: int, finding_id: str,
    ) -> tuple[bool, bool]:
        if self._has_seen(finding_id):
            return True, True

        self._mark_seen(finding_id)

        # Record as peer event
        self._events.record(
            source="peer",
            category="peer_finding",
            severity=severity,
            raw_data={
                "peer_hostname": hostname,
                "origin_hostname": origin_hostname,
                "finding_id": finding_id,
                "summary": summary,
                "affected_scope": affected_scope,
                "ttl": ttl,
            },
        )
        self._peer_state.update_event_summary(hostname, f"[finding] {summary}")

        # Re-gossip if TTL > 1
        if ttl > 1:
            self._gossip_finding(
                sender_hostname=hostname,
                origin_hostname=origin_hostname,
                timestamp=timestamp,
                severity=severity,
                summary=summary,
                affected_scope=affected_scope,
                ttl=ttl - 1,
                finding_id=finding_id,
            )

        return True, False

    # ── Internal helpers ──

    def _gossip_finding(
        self, sender_hostname: str, origin_hostname: str,
        timestamp: str, severity: str, summary: str,
        affected_scope: str, ttl: int, finding_id: str,
    ) -> None:
        """Re-share a received finding to other peers (excluding sender)."""
        for peer_hostname, client in self._clients.items():
            if peer_hostname == sender_hostname:
                continue
            client.share_finding(
                hostname=self._config.hostname,
                origin_hostname=origin_hostname,
                timestamp=timestamp,
                severity=severity,
                summary=summary,
                affected_scope=affected_scope,
                ttl=ttl,
                finding_id=finding_id,
            )

    def _get_current_severity(self) -> str:
        """Determine our overall severity based on active findings."""
        counts = self._findings.count_active()
        if counts.get("critical", 0) > 0:
            return "critical"
        if counts.get("warning", 0) > 0:
            return "warning"
        if counts.get("info", 0) > 0:
            return "info"
        return "ok"

    def _mark_seen(self, finding_id: str) -> None:
        """Mark a finding_id as seen, evicting old entries if at capacity."""
        if finding_id in self._seen_finding_ids:
            self._seen_finding_ids.move_to_end(finding_id)
            return
        if len(self._seen_finding_ids) >= _MAX_SEEN_FINDINGS:
            self._seen_finding_ids.popitem(last=False)
        self._seen_finding_ids[finding_id] = None

    def _has_seen(self, finding_id: str) -> bool:
        return finding_id in self._seen_finding_ids
