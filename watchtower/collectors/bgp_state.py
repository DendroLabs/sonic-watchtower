"""BGP state collector -- reads BGP session data from APPL_DB."""

from __future__ import annotations

from watchtower.collectors.base import BaseCollector, RedisReader


class BGPStateCollector(BaseCollector):
    """Collects BGP neighbor session states and metrics."""

    def collect(self, neighbor: str | None = None) -> dict:
        """Collect BGP session data.

        Args:
            neighbor: Specific neighbor IP. If None, returns all BGP neighbors.

        Returns:
            Dict mapping neighbor IPs to session info.
        """
        reader = self._reader(RedisReader.APPL_DB)

        if neighbor:
            keys = [f"BGP_NEIGHBOR_TABLE:{neighbor}"]
        else:
            keys = reader.keys("BGP_NEIGHBOR_TABLE:*")

        result = {}
        for key in keys:
            neighbor_ip = key.split(":", 1)[1] if ":" in key else key
            data = reader.hgetall(key)
            if not data:
                continue

            result[neighbor_ip] = {
                "state": data.get("state", "Unknown"),
                "peer_as": data.get("peerAs", ""),
                "local_as": data.get("localAs", ""),
                "description": data.get("peerDesc", ""),
                "hold_time": int(data.get("holdTime", "0")),
                "keepalive": int(data.get("keepAlive", "0")),
                "prefixes_received": int(data.get("pfxRcvd", "0")),
                "uptime_seconds": int(data.get("upTime", "0")),
            }

        return result
