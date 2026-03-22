"""LLDP-based peer discovery for Watchtower."""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass

from watchtower.store.topology import TopologyStore

logger = logging.getLogger("watchtower.peer.discovery")


@dataclass
class PeerEndpoint:
    """A discovered peer Watchtower endpoint."""

    hostname: str
    address: str
    port: int


class PeerDiscovery:
    """Discovers peer Watchtower instances from LLDP topology data."""

    def __init__(self, topology_store: TopologyStore, peer_port: int = 5950):
        self._topology = topology_store
        self._port = peer_port

    def discover_peers(self) -> list[PeerEndpoint]:
        """Read LLDP topology and return endpoints for peer Watchtowers.

        Each unique neighbor_hostname maps to one endpoint.
        Address is resolved via DNS (or /etc/hosts).
        """
        entries = self._topology.get_all()
        seen_hostnames: set[str] = set()
        endpoints: list[PeerEndpoint] = []

        for entry in entries:
            hostname = entry["neighbor_hostname"]
            if not hostname or hostname in seen_hostnames:
                continue
            seen_hostnames.add(hostname)

            address = self._resolve_address(hostname)
            if address is not None:
                endpoints.append(PeerEndpoint(
                    hostname=hostname,
                    address=address,
                    port=self._port,
                ))

        return endpoints

    def _resolve_address(self, hostname: str) -> str | None:
        """Resolve a hostname to an IP address. Returns None on failure."""
        try:
            results = socket.getaddrinfo(
                hostname, self._port, socket.AF_UNSPEC, socket.SOCK_STREAM,
            )
            if results:
                # Return the first resolved address
                return str(results[0][4][0])
        except socket.gaierror:
            logger.debug("Cannot resolve peer hostname: %s", hostname)
        return None
