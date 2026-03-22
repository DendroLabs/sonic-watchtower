"""gRPC client for connecting to peer Watchtower instances."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import grpc

from watchtower.config import TLSConfig
from watchtower.peer.proto import watchtower_pb2, watchtower_pb2_grpc

logger = logging.getLogger("watchtower.peer.client")

# Default timeout for RPCs (seconds)
_DEFAULT_TIMEOUT = 5.0


def make_channel_credentials(tls: TLSConfig) -> grpc.ChannelCredentials:
    """Load client-side mTLS credentials from disk."""
    ca = Path(tls.ca).read_bytes()
    cert = Path(tls.cert).read_bytes()
    key = Path(tls.key).read_bytes()
    return grpc.ssl_channel_credentials(
        root_certificates=ca,
        private_key=key,
        certificate_chain=cert,
    )


class PeerClient:
    """gRPC client for a single peer Watchtower instance."""

    def __init__(
        self,
        hostname: str,
        address: str,
        port: int,
        channel: grpc.Channel | None = None,
        tls_config: TLSConfig | None = None,
    ):
        self.hostname = hostname
        self.address = address
        self.port = port
        self._owns_channel = channel is None

        if channel is not None:
            self._channel = channel
        elif tls_config is not None:
            credentials = make_channel_credentials(tls_config)
            self._channel = grpc.secure_channel(f"{address}:{port}", credentials)
        else:
            self._channel = grpc.insecure_channel(f"{address}:{port}")

        self._stub = watchtower_pb2_grpc.WatchtowerPeerStub(self._channel)

    def send_heartbeat(
        self,
        hostname: str,
        role: str = "leaf",
        uptime_seconds: int = 0,
        current_severity: str = "ok",
        active_finding_count: int = 0,
        governor_state: str = "full",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> dict[str, Any] | None:
        """Send a heartbeat and return the peer's response, or None on failure."""
        try:
            request = watchtower_pb2.HeartbeatRequest(
                hostname=hostname,
                role=role,
                uptime_seconds=uptime_seconds,
                current_severity=current_severity,
                active_finding_count=active_finding_count,
                governor_state=governor_state,
            )
            response = self._stub.Heartbeat(request, timeout=timeout)
            return {
                "hostname": response.hostname,
                "role": response.role,
                "uptime_seconds": response.uptime_seconds,
                "current_severity": response.current_severity,
                "active_finding_count": response.active_finding_count,
                "governor_state": response.governor_state,
            }
        except grpc.RpcError as e:
            logger.debug("Heartbeat to %s failed: %s", self.hostname, e)
            return None

    def share_event(
        self,
        hostname: str,
        timestamp: str,
        event_type: str,
        affected_port: str,
        summary: str,
        metrics: dict[str, str] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> bool:
        """Share a local event with this peer. Returns True if accepted."""
        try:
            request = watchtower_pb2.EventShareRequest(
                hostname=hostname,
                timestamp=timestamp,
                event_type=event_type,
                affected_port=affected_port,
                summary=summary,
                metrics=metrics or {},
            )
            response = self._stub.ShareEvent(request, timeout=timeout)
            return response.accepted
        except grpc.RpcError as e:
            logger.debug("ShareEvent to %s failed: %s", self.hostname, e)
            return False

    def share_topology(
        self,
        hostname: str,
        neighbors: list[dict[str, str]],
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> bool:
        """Share our topology fragment. Returns True if accepted."""
        try:
            entries = [
                watchtower_pb2.NeighborEntry(
                    local_port=n["local_port"],
                    remote_host=n["remote_host"],
                    remote_port=n["remote_port"],
                    link_state=n.get("link_state", "up"),
                )
                for n in neighbors
            ]
            request = watchtower_pb2.TopologyFragmentRequest(
                hostname=hostname, neighbors=entries,
            )
            response = self._stub.ShareTopology(request, timeout=timeout)
            return response.accepted
        except grpc.RpcError as e:
            logger.debug("ShareTopology to %s failed: %s", self.hostname, e)
            return False

    def share_finding(
        self,
        hostname: str,
        origin_hostname: str,
        timestamp: str,
        severity: str,
        summary: str,
        affected_scope: str,
        ttl: int,
        finding_id: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> tuple[bool, bool]:
        """Share a finding. Returns (accepted, already_seen)."""
        try:
            request = watchtower_pb2.FindingShareRequest(
                hostname=hostname,
                origin_hostname=origin_hostname,
                timestamp=timestamp,
                severity=severity,
                summary=summary,
                affected_scope=affected_scope,
                ttl=ttl,
                finding_id=finding_id,
            )
            response = self._stub.ShareFinding(request, timeout=timeout)
            return response.accepted, response.already_seen
        except grpc.RpcError as e:
            logger.debug("ShareFinding to %s failed: %s", self.hostname, e)
            return False, False

    def close(self) -> None:
        """Close the gRPC channel if we own it."""
        if self._owns_channel:
            self._channel.close()
