"""Optic health collector -- reads DOM (Digital Optical Monitoring) data from STATE_DB."""

from __future__ import annotations

from watchtower.collectors.base import BaseCollector, RedisReader


class OpticHealthCollector(BaseCollector):
    """Collects transceiver DOM sensor data and module info."""

    def collect(self, port: str | None = None) -> dict:
        """Collect optic DOM data.

        Args:
            port: Specific port name. If None, returns all transceivers.

        Returns:
            Dict mapping port names to optic health data.
        """
        reader = self._reader(RedisReader.STATE_DB)

        if port:
            dom_keys = [f"TRANSCEIVER_DOM_SENSOR|{port}"]
        else:
            dom_keys = reader.keys("TRANSCEIVER_DOM_SENSOR|*")

        result = {}
        for key in dom_keys:
            port_name = key.split("|", 1)[1] if "|" in key else key
            dom_data = reader.hgetall(key)
            if not dom_data:
                continue

            info_data = reader.hgetall(f"TRANSCEIVER_INFO|{port_name}")

            # Parse RX/TX power lanes
            rx_power = []
            tx_power = []
            tx_bias = []
            for i in range(1, 9):  # up to 8 lanes
                rx_key = f"rx{i}power"
                tx_key = f"tx{i}power"
                bias_key = f"tx{i}bias"
                if rx_key in dom_data:
                    rx_power.append(float(dom_data[rx_key]))
                if tx_key in dom_data:
                    tx_power.append(float(dom_data[tx_key]))
                if bias_key in dom_data:
                    tx_bias.append(float(dom_data[bias_key]))

            result[port_name] = {
                "temperature": float(dom_data.get("temperature", "0")),
                "voltage": float(dom_data.get("voltage", "0")),
                "rx_power_dbm": rx_power,
                "tx_power_dbm": tx_power,
                "tx_bias_ma": tx_bias,
                "rx_power_avg_dbm": sum(rx_power) / len(rx_power) if rx_power else 0.0,
                "tx_power_avg_dbm": sum(tx_power) / len(tx_power) if tx_power else 0.0,
                "type": info_data.get("type", ""),
                "vendor": info_data.get("vendor_name", ""),
                "serial": info_data.get("serial", ""),
                "model": info_data.get("model", ""),
            }

        return result
