"""Robustness tests: missing keys, empty hashes, malformed data, connection failures."""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest
import redis

from watchtower.collectors.base import RedisReader
from watchtower.collectors.bgp_state import BGPStateCollector
from watchtower.collectors.interface_state import InterfaceStateCollector
from watchtower.collectors.lldp_topology import LLDPTopologyCollector
from watchtower.collectors.optic_health import OpticHealthCollector
from watchtower.collectors.port_stats import PortStatsCollector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reader(data: dict | None = None) -> RedisReader:
    """Create a RedisReader backed by fakeredis, optionally pre-populated."""
    client = fakeredis.FakeRedis(decode_responses=True)
    if data:
        for key, value in data.items():
            if isinstance(value, dict):
                if value:  # Redis can't store empty hashes
                    client.hset(key, mapping=value)
            else:
                client.set(key, value)
    return RedisReader.from_client(client)


def _make_broken_reader() -> RedisReader:
    """Create a RedisReader whose client raises ConnectionError on every call."""
    client = MagicMock(spec=redis.Redis)
    client.hgetall.side_effect = redis.ConnectionError("Connection refused")
    client.keys.side_effect = redis.ConnectionError("Connection refused")
    client.get.side_effect = redis.ConnectionError("Connection refused")
    return RedisReader.from_client(client)


# ===========================================================================
# PortStatsCollector
# ===========================================================================


class TestPortStatsRobustness:
    def test_empty_counters_db(self) -> None:
        """No keys at all in COUNTERS_DB."""
        reader = _make_reader()
        collector = PortStatsCollector(readers={RedisReader.COUNTERS_DB: reader})
        result = collector.collect()
        assert result == {}

    def test_port_map_empty(self) -> None:
        """COUNTERS_PORT_NAME_MAP returns empty (key doesn't exist or no fields)."""
        reader = _make_reader()  # no keys at all
        collector = PortStatsCollector(readers={RedisReader.COUNTERS_DB: reader})
        result = collector.collect()
        assert result == {}

    def test_port_map_exists_but_oid_key_missing(self) -> None:
        """Port name map references an OID, but the counter hash doesn't exist."""
        reader = _make_reader({"COUNTERS_PORT_NAME_MAP": {"Ethernet0": "oid:0x1000000000099"}})
        collector = PortStatsCollector(readers={RedisReader.COUNTERS_DB: reader})
        result = collector.collect()
        # OID key missing -> port is skipped
        assert result == {}

    def test_counter_hash_missing_for_oid(self) -> None:
        """Port map references OID but the counter hash returns empty."""
        reader = _make_reader(
            {
                "COUNTERS_PORT_NAME_MAP": {"Ethernet0": "oid:0x1"},
                # No COUNTERS:oid:0x1 key -- hgetall returns {}
            }
        )
        collector = PortStatsCollector(readers={RedisReader.COUNTERS_DB: reader})
        result = collector.collect()
        assert result == {}

    def test_malformed_counter_values(self) -> None:
        """Counter fields contain non-numeric strings."""
        reader = _make_reader(
            {
                "COUNTERS_PORT_NAME_MAP": {"Ethernet0": "oid:0x1"},
                "COUNTERS:oid:0x1": {
                    "SAI_PORT_STAT_IF_IN_OCTETS": "not_a_number",
                    "SAI_PORT_STAT_IF_OUT_OCTETS": "12345",
                    "SAI_PORT_STAT_IF_IN_ERRORS": "",
                },
            }
        )
        collector = PortStatsCollector(readers={RedisReader.COUNTERS_DB: reader})
        with pytest.raises((ValueError, TypeError)):
            collector.collect()

    def test_connection_failure(self) -> None:
        """Redis connection failure raises."""
        reader = _make_broken_reader()
        collector = PortStatsCollector(readers={RedisReader.COUNTERS_DB: reader})
        with pytest.raises(redis.ConnectionError):
            collector.collect()


# ===========================================================================
# InterfaceStateCollector
# ===========================================================================


class TestInterfaceStateRobustness:
    def test_empty_appl_db(self) -> None:
        """No PORT_TABLE keys in APPL_DB."""
        appl = _make_reader()
        state = _make_reader()
        collector = InterfaceStateCollector(
            readers={RedisReader.APPL_DB: appl, RedisReader.STATE_DB: state}
        )
        result = collector.collect()
        assert result == {}

    def test_appl_key_returns_empty_hash(self) -> None:
        """PORT_TABLE key exists but hgetall returns empty (no fields)."""
        appl = _make_reader()  # no PORT_TABLE keys -> keys() returns []
        state = _make_reader()
        collector = InterfaceStateCollector(
            readers={RedisReader.APPL_DB: appl, RedisReader.STATE_DB: state}
        )
        result = collector.collect()
        assert result == {}

    def test_appl_exists_but_state_missing(self) -> None:
        """APPL_DB has port data but STATE_DB has no matching entry."""
        appl = _make_reader({"PORT_TABLE:Ethernet0": {"admin_status": "up", "speed": "100000"}})
        state = _make_reader()
        collector = InterfaceStateCollector(
            readers={RedisReader.APPL_DB: appl, RedisReader.STATE_DB: state}
        )
        result = collector.collect()
        assert "Ethernet0" in result
        assert result["Ethernet0"]["oper_status"] == "unknown"

    def test_missing_fields_use_defaults(self) -> None:
        """PORT_TABLE exists but is missing some expected fields."""
        appl = _make_reader({"PORT_TABLE:Ethernet0": {"admin_status": "up"}})
        state = _make_reader({"PORT_TABLE|Ethernet0": {"netdev_oper_status": "up"}})
        collector = InterfaceStateCollector(
            readers={RedisReader.APPL_DB: appl, RedisReader.STATE_DB: state}
        )
        result = collector.collect()
        assert result["Ethernet0"]["speed"] == "0"
        assert result["Ethernet0"]["mtu"] == "0"
        assert result["Ethernet0"]["alias"] == ""

    def test_connection_failure(self) -> None:
        reader = _make_broken_reader()
        collector = InterfaceStateCollector(
            readers={RedisReader.APPL_DB: reader, RedisReader.STATE_DB: reader}
        )
        with pytest.raises(redis.ConnectionError):
            collector.collect()


# ===========================================================================
# LLDPTopologyCollector
# ===========================================================================


class TestLLDPTopologyRobustness:
    def test_no_lldp_entries(self) -> None:
        reader = _make_reader()
        collector = LLDPTopologyCollector(readers={RedisReader.APPL_DB: reader})
        result = collector.collect()
        assert result == {}

    def test_lldp_specific_port_not_found(self) -> None:
        """Query a specific port that has no LLDP entry."""
        reader = _make_reader()
        collector = LLDPTopologyCollector(readers={RedisReader.APPL_DB: reader})
        result = collector.collect(port="Ethernet99")
        assert result == {}

    def test_lldp_entry_missing_fields(self) -> None:
        """LLDP entry has only partial fields."""
        reader = _make_reader({"LLDP_ENTRY_TABLE:Ethernet0": {"lldp_rem_sys_name": "spine01"}})
        collector = LLDPTopologyCollector(readers={RedisReader.APPL_DB: reader})
        result = collector.collect()
        assert result["Ethernet0"]["neighbor_hostname"] == "spine01"
        assert result["Ethernet0"]["neighbor_port"] == ""
        assert result["Ethernet0"]["chassis_id"] == ""

    def test_connection_failure(self) -> None:
        reader = _make_broken_reader()
        collector = LLDPTopologyCollector(readers={RedisReader.APPL_DB: reader})
        with pytest.raises(redis.ConnectionError):
            collector.collect()


# ===========================================================================
# BGPStateCollector
# ===========================================================================


class TestBGPStateRobustness:
    def test_no_bgp_neighbors(self) -> None:
        reader = _make_reader()
        collector = BGPStateCollector(readers={RedisReader.APPL_DB: reader})
        result = collector.collect()
        assert result == {}

    def test_bgp_specific_neighbor_not_found(self) -> None:
        """Query a specific neighbor IP that doesn't exist."""
        reader = _make_reader()
        collector = BGPStateCollector(readers={RedisReader.APPL_DB: reader})
        result = collector.collect(neighbor="192.168.99.99")
        assert result == {}

    def test_bgp_missing_fields_use_defaults(self) -> None:
        """BGP entry with only a state field."""
        reader = _make_reader({"BGP_NEIGHBOR_TABLE:10.0.0.1": {"state": "Established"}})
        collector = BGPStateCollector(readers={RedisReader.APPL_DB: reader})
        result = collector.collect()
        assert result["10.0.0.1"]["state"] == "Established"
        assert result["10.0.0.1"]["peer_as"] == ""
        assert result["10.0.0.1"]["hold_time"] == 0

    def test_bgp_malformed_numeric_fields(self) -> None:
        """Non-numeric values in fields that get int()-converted."""
        reader = _make_reader(
            {
                "BGP_NEIGHBOR_TABLE:10.0.0.1": {
                    "state": "Established",
                    "holdTime": "bad",
                    "keepAlive": "",
                    "pfxRcvd": "not_int",
                    "upTime": "abc",
                }
            }
        )
        collector = BGPStateCollector(readers={RedisReader.APPL_DB: reader})
        with pytest.raises((ValueError, TypeError)):
            collector.collect()

    def test_connection_failure(self) -> None:
        reader = _make_broken_reader()
        collector = BGPStateCollector(readers={RedisReader.APPL_DB: reader})
        with pytest.raises(redis.ConnectionError):
            collector.collect()


# ===========================================================================
# OpticHealthCollector
# ===========================================================================


class TestOpticHealthRobustness:
    def test_no_transceivers(self) -> None:
        reader = _make_reader()
        collector = OpticHealthCollector(readers={RedisReader.STATE_DB: reader})
        result = collector.collect()
        assert result == {}

    def test_dom_specific_port_not_found(self) -> None:
        """Query a specific port with no DOM data."""
        reader = _make_reader()
        collector = OpticHealthCollector(readers={RedisReader.STATE_DB: reader})
        result = collector.collect(port="Ethernet99")
        assert result == {}

    def test_dom_missing_power_lanes(self) -> None:
        """DOM entry exists with only temperature -- no rx/tx power lanes."""
        reader = _make_reader(
            {
                "TRANSCEIVER_DOM_SENSOR|Ethernet0": {
                    "temperature": "35.0",
                    "voltage": "3.3",
                }
            }
        )
        collector = OpticHealthCollector(readers={RedisReader.STATE_DB: reader})
        result = collector.collect()
        assert result["Ethernet0"]["temperature"] == 35.0
        assert result["Ethernet0"]["rx_power_dbm"] == []
        assert result["Ethernet0"]["rx_power_avg_dbm"] == 0.0

    def test_dom_no_transceiver_info(self) -> None:
        """DOM data exists but TRANSCEIVER_INFO is missing."""
        reader = _make_reader(
            {
                "TRANSCEIVER_DOM_SENSOR|Ethernet0": {
                    "temperature": "30.0",
                    "voltage": "3.3",
                    "rx1power": "-2.0",
                }
            }
        )
        collector = OpticHealthCollector(readers={RedisReader.STATE_DB: reader})
        result = collector.collect()
        assert result["Ethernet0"]["type"] == ""
        assert result["Ethernet0"]["vendor"] == ""

    def test_dom_malformed_float_values(self) -> None:
        """Non-numeric values in DOM fields that get float()-converted."""
        reader = _make_reader(
            {
                "TRANSCEIVER_DOM_SENSOR|Ethernet0": {
                    "temperature": "hot",
                    "voltage": "bad",
                    "rx1power": "not_a_float",
                }
            }
        )
        collector = OpticHealthCollector(readers={RedisReader.STATE_DB: reader})
        with pytest.raises((ValueError, TypeError)):
            collector.collect()

    def test_connection_failure(self) -> None:
        reader = _make_broken_reader()
        collector = OpticHealthCollector(readers={RedisReader.STATE_DB: reader})
        with pytest.raises(redis.ConnectionError):
            collector.collect()
