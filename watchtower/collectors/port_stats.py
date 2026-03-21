"""Port statistics collector -- reads per-port counters from COUNTERS_DB."""

from __future__ import annotations

from watchtower.collectors.base import BaseCollector, RedisReader

# Counter field mappings: SAI name -> friendly name
_COUNTER_MAP = {
    "SAI_PORT_STAT_IF_IN_OCTETS": "rx_bytes",
    "SAI_PORT_STAT_IF_OUT_OCTETS": "tx_bytes",
    "SAI_PORT_STAT_IF_IN_ERRORS": "rx_errors",
    "SAI_PORT_STAT_IF_OUT_ERRORS": "tx_errors",
    "SAI_PORT_STAT_IF_IN_DISCARDS": "rx_drops",
    "SAI_PORT_STAT_IF_OUT_DISCARDS": "tx_drops",
    "SAI_PORT_STAT_IF_IN_UCAST_PKTS": "rx_packets",
    "SAI_PORT_STAT_IF_OUT_UCAST_PKTS": "tx_packets",
    "SAI_PORT_STAT_ETHER_STATS_CRC_ALIGN_ERRORS": "rx_crc_errors",
}


class PortStatsCollector(BaseCollector):
    """Collects per-port counter statistics from COUNTERS_DB."""

    def collect(self, port: str | None = None) -> dict:
        """Collect port stats.

        Args:
            port: Specific port name (e.g., "Ethernet48"). If None, returns all ports.

        Returns:
            Dict mapping port names to their counter values.
        """
        reader = self._reader(RedisReader.COUNTERS_DB)

        # Get port name -> OID mapping
        port_map = reader.hgetall("COUNTERS_PORT_NAME_MAP")
        if not port_map:
            return {}

        if port:
            if port not in port_map:
                return {}
            port_map = {port: port_map[port]}

        result = {}
        for port_name, oid in port_map.items():
            counters = reader.hgetall(f"COUNTERS:{oid}")
            if not counters:
                continue

            stats = {}
            for sai_name, friendly_name in _COUNTER_MAP.items():
                value = counters.get(sai_name, "0")
                stats[friendly_name] = int(value)

            result[port_name] = stats

        return result
