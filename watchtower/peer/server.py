"""gRPC server for the Watchtower peer protocol."""

from __future__ import annotations

import logging
from typing import Any, Protocol

import grpc

from watchtower.peer.proto import watchtower_pb2, watchtower_pb2_grpc

logger = logging.getLogger("watchtower.peer.server")


class PeerMessageHandler(Protocol):
    """Interface for handling incoming peer messages."""

    def handle_heartbeat(
        self,
        hostname: str,
        role: str,
        uptime_seconds: int,
        current_severity: str,
        active_finding_count: int,
        governor_state: str,
    ) -> dict[str, Any]:
        """Handle a heartbeat and return our own status as a dict."""
        ...

    def handle_event(
        self,
        hostname: str,
        timestamp: str,
        event_type: str,
        affected_port: str,
        summary: str,
        metrics: dict[str, str],
    ) -> bool:
        """Handle an incoming event. Returns True if accepted."""
        ...

    def handle_topology(
        self,
        hostname: str,
        neighbors: list[dict[str, str]],
    ) -> bool:
        """Handle a topology fragment. Returns True if accepted."""
        ...

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
        """Handle a finding share. Returns (accepted, already_seen)."""
        ...


class WatchtowerServicer(watchtower_pb2_grpc.WatchtowerPeerServicer):
    """gRPC service implementation for the Watchtower peer protocol."""

    def __init__(self, handler: PeerMessageHandler):
        self._handler = handler

    def Heartbeat(  # type: ignore[override]
        self,
        request: watchtower_pb2.HeartbeatRequest,
        context: grpc.ServicerContext,
    ) -> watchtower_pb2.HeartbeatResponse:
        logger.debug("Heartbeat from %s (severity=%s)", request.hostname, request.current_severity)
        try:
            our_status = self._handler.handle_heartbeat(
                hostname=request.hostname,
                role=request.role,
                uptime_seconds=request.uptime_seconds,
                current_severity=request.current_severity,
                active_finding_count=request.active_finding_count,
                governor_state=request.governor_state,
            )
            return watchtower_pb2.HeartbeatResponse(
                hostname=our_status.get("hostname", ""),
                role=our_status.get("role", "leaf"),
                uptime_seconds=our_status.get("uptime_seconds", 0),
                current_severity=our_status.get("current_severity", "ok"),
                active_finding_count=our_status.get("active_finding_count", 0),
                governor_state=our_status.get("governor_state", "unknown"),
            )
        except Exception:
            logger.exception("Error handling heartbeat from %s", request.hostname)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal error processing heartbeat")
            return watchtower_pb2.HeartbeatResponse()

    def ShareEvent(  # type: ignore[override]
        self,
        request: watchtower_pb2.EventShareRequest,
        context: grpc.ServicerContext,
    ) -> watchtower_pb2.EventShareResponse:
        logger.debug(
            "Event from %s: %s on %s",
            request.hostname,
            request.event_type,
            request.affected_port,
        )
        try:
            accepted = self._handler.handle_event(
                hostname=request.hostname,
                timestamp=request.timestamp,
                event_type=request.event_type,
                affected_port=request.affected_port,
                summary=request.summary,
                metrics=dict(request.metrics),
            )
            return watchtower_pb2.EventShareResponse(accepted=accepted)
        except Exception:
            logger.exception("Error handling event from %s", request.hostname)
            return watchtower_pb2.EventShareResponse(accepted=False)

    def ShareTopology(  # type: ignore[override]
        self,
        request: watchtower_pb2.TopologyFragmentRequest,
        context: grpc.ServicerContext,
    ) -> watchtower_pb2.TopologyFragmentResponse:
        logger.debug(
            "Topology from %s (%d neighbors)",
            request.hostname,
            len(request.neighbors),
        )
        try:
            neighbors = [
                {
                    "local_port": n.local_port,
                    "remote_host": n.remote_host,
                    "remote_port": n.remote_port,
                    "link_state": n.link_state,
                }
                for n in request.neighbors
            ]
            accepted = self._handler.handle_topology(
                hostname=request.hostname,
                neighbors=neighbors,
            )
            return watchtower_pb2.TopologyFragmentResponse(accepted=accepted)
        except Exception:
            logger.exception("Error handling topology from %s", request.hostname)
            return watchtower_pb2.TopologyFragmentResponse(accepted=False)

    def ShareFinding(  # type: ignore[override]
        self,
        request: watchtower_pb2.FindingShareRequest,
        context: grpc.ServicerContext,
    ) -> watchtower_pb2.FindingShareResponse:
        logger.debug(
            "Finding from %s (origin=%s, ttl=%d): %s",
            request.hostname,
            request.origin_hostname,
            request.ttl,
            request.summary,
        )
        try:
            accepted, already_seen = self._handler.handle_finding(
                hostname=request.hostname,
                origin_hostname=request.origin_hostname,
                timestamp=request.timestamp,
                severity=request.severity,
                summary=request.summary,
                affected_scope=request.affected_scope,
                ttl=request.ttl,
                finding_id=request.finding_id,
            )
            return watchtower_pb2.FindingShareResponse(
                accepted=accepted,
                already_seen=already_seen,
            )
        except Exception:
            logger.exception("Error handling finding from %s", request.hostname)
            return watchtower_pb2.FindingShareResponse(accepted=False, already_seen=False)
