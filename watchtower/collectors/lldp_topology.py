"""LLDP topology collector -- reads LLDP neighbor data from APPL_DB."""

from __future__ import annotations

from watchtower.collectors.base import BaseCollector, RedisReader


class LLDPTopologyCollector(BaseCollector):
    """Collects LLDP neighbor information for topology mapping."""

    def collect(self, port: str | None = None) -> dict:
        """Collect LLDP neighbor data.

        Args:
            port: Specific port name. If None, returns all LLDP neighbors.

        Returns:
            Dict mapping local port names to neighbor info.
        """
        reader = self._reader(RedisReader.APPL_DB)

        if port:
            keys = [f"LLDP_ENTRY_TABLE:{port}"]
        else:
            keys = reader.keys("LLDP_ENTRY_TABLE:*")

        result = {}
        for key in keys:
            port_name = key.split(":", 1)[1] if ":" in key else key
            data = reader.hgetall(key)
            if not data:
                continue

            result[port_name] = {
                "neighbor_hostname": data.get("lldp_rem_sys_name", ""),
                "neighbor_port": data.get("lldp_rem_port_id", ""),
                "neighbor_description": data.get("lldp_rem_port_desc", ""),
                "chassis_id": data.get("lldp_rem_chassis_id", ""),
                "system_description": data.get("lldp_rem_sys_desc", ""),
            }

        return result
