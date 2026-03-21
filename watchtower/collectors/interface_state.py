"""Interface state collector -- reads port admin/oper state from APPL_DB and STATE_DB."""

from __future__ import annotations

from watchtower.collectors.base import BaseCollector, RedisReader


class InterfaceStateCollector(BaseCollector):
    """Collects interface admin/oper state, speed, and MTU."""

    def collect(self, port: str | None = None) -> dict:
        """Collect interface state.

        Args:
            port: Specific port name. If None, returns all ports.

        Returns:
            Dict mapping port names to their state info.
        """
        appl = self._reader(RedisReader.APPL_DB)
        state = self._reader(RedisReader.STATE_DB)

        if port:
            keys = [f"PORT_TABLE:{port}"]
        else:
            keys = appl.keys("PORT_TABLE:*")

        result = {}
        for key in keys:
            port_name = key.split(":", 1)[1] if ":" in key else key
            appl_data = appl.hgetall(key)
            if not appl_data:
                continue

            # STATE_DB uses | separator
            state_data = state.hgetall(f"PORT_TABLE|{port_name}")

            result[port_name] = {
                "admin_status": appl_data.get("admin_status", "unknown"),
                "oper_status": state_data.get("oper_status", "unknown"),
                "speed": appl_data.get("speed", "0"),
                "mtu": appl_data.get("mtu", "0"),
                "alias": appl_data.get("alias", ""),
                "description": appl_data.get("description", ""),
            }

        return result
