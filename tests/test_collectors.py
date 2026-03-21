"""Tests for all Phase 1 collectors using fakeredis."""

from __future__ import annotations

import json
from pathlib import Path

import fakeredis
import pytest

from watchtower.collectors.base import RedisReader
from watchtower.collectors.bgp_state import BGPStateCollector
from watchtower.collectors.interface_state import InterfaceStateCollector
from watchtower.collectors.lldp_topology import LLDPTopologyCollector
from watchtower.collectors.log_filter import LogFilterCollector
from watchtower.collectors.optic_health import OpticHealthCollector
from watchtower.collectors.port_stats import PortStatsCollector

MOCK_DIR = Path(__file__).parent / "mock_redis"


def _load_fixture(filename: str) -> dict:
    with open(MOCK_DIR / filename) as f:
        return json.load(f)


def _populate_redis(client: fakeredis.FakeRedis, data: dict):
    """Load fixture data into a fakeredis client."""
    for key, value in data.items():
        if isinstance(value, dict):
            client.hset(key, mapping=value)
        else:
            client.set(key, value)


@pytest.fixture
def counters_reader():
    client = fakeredis.FakeRedis(decode_responses=True)
    _populate_redis(client, _load_fixture("counters_db.json"))
    return RedisReader.from_client(client)


@pytest.fixture
def appl_reader():
    client = fakeredis.FakeRedis(decode_responses=True)
    _populate_redis(client, _load_fixture("appl_db.json"))
    return RedisReader.from_client(client)


@pytest.fixture
def state_reader():
    client = fakeredis.FakeRedis(decode_responses=True)
    _populate_redis(client, _load_fixture("state_db.json"))
    return RedisReader.from_client(client)


# --- PortStatsCollector ---


class TestPortStatsCollector:
    def test_collect_all_ports(self, counters_reader):
        collector = PortStatsCollector(readers={RedisReader.COUNTERS_DB: counters_reader})
        result = collector.collect()

        assert "Ethernet0" in result
        assert "Ethernet48" in result
        assert result["Ethernet0"]["rx_bytes"] == 1000000
        assert result["Ethernet0"]["tx_bytes"] == 2000000
        assert result["Ethernet0"]["rx_errors"] == 0

    def test_collect_single_port(self, counters_reader):
        collector = PortStatsCollector(readers={RedisReader.COUNTERS_DB: counters_reader})
        result = collector.collect(port="Ethernet48")

        assert "Ethernet48" in result
        assert "Ethernet0" not in result
        assert result["Ethernet48"]["rx_crc_errors"] == 1847
        assert result["Ethernet48"]["rx_errors"] == 1847

    def test_collect_nonexistent_port(self, counters_reader):
        collector = PortStatsCollector(readers={RedisReader.COUNTERS_DB: counters_reader})
        result = collector.collect(port="Ethernet99")
        assert result == {}

    def test_collect_empty_db(self):
        client = fakeredis.FakeRedis(decode_responses=True)
        reader = RedisReader.from_client(client)
        collector = PortStatsCollector(readers={RedisReader.COUNTERS_DB: reader})
        result = collector.collect()
        assert result == {}


# --- InterfaceStateCollector ---


class TestInterfaceStateCollector:
    def test_collect_all(self, appl_reader, state_reader):
        collector = InterfaceStateCollector(
            readers={
                RedisReader.APPL_DB: appl_reader,
                RedisReader.STATE_DB: state_reader,
            }
        )
        result = collector.collect()

        assert "Ethernet0" in result
        assert result["Ethernet0"]["admin_status"] == "up"
        assert result["Ethernet0"]["oper_status"] == "up"
        assert result["Ethernet0"]["speed"] == "100000"

    def test_collect_single_port(self, appl_reader, state_reader):
        collector = InterfaceStateCollector(
            readers={
                RedisReader.APPL_DB: appl_reader,
                RedisReader.STATE_DB: state_reader,
            }
        )
        result = collector.collect(port="Ethernet48")

        assert "Ethernet48" in result
        assert result["Ethernet48"]["description"] == "link to switch-b"


# --- LLDPTopologyCollector ---


class TestLLDPTopologyCollector:
    def test_collect_all(self, appl_reader):
        collector = LLDPTopologyCollector(readers={RedisReader.APPL_DB: appl_reader})
        result = collector.collect()

        assert "Ethernet0" in result
        assert "Ethernet48" in result
        assert result["Ethernet0"]["neighbor_hostname"] == "spine-1"
        assert result["Ethernet48"]["neighbor_hostname"] == "switch-b"
        assert result["Ethernet48"]["neighbor_port"] == "Ethernet12"

    def test_collect_single_port(self, appl_reader):
        collector = LLDPTopologyCollector(readers={RedisReader.APPL_DB: appl_reader})
        result = collector.collect(port="Ethernet48")

        assert "Ethernet48" in result
        assert len(result) == 1
        assert result["Ethernet48"]["chassis_id"] == "aa:bb:cc:dd:ee:02"

    def test_collect_no_lldp(self):
        client = fakeredis.FakeRedis(decode_responses=True)
        reader = RedisReader.from_client(client)
        collector = LLDPTopologyCollector(readers={RedisReader.APPL_DB: reader})
        result = collector.collect()
        assert result == {}


# --- BGPStateCollector ---


class TestBGPStateCollector:
    def test_collect_all(self, appl_reader):
        collector = BGPStateCollector(readers={RedisReader.APPL_DB: appl_reader})
        result = collector.collect()

        assert "10.0.0.1" in result
        assert "10.0.0.3" in result
        assert result["10.0.0.1"]["state"] == "Established"
        assert result["10.0.0.1"]["prefixes_received"] == 1500
        assert result["10.0.0.3"]["state"] == "Idle"

    def test_collect_single_neighbor(self, appl_reader):
        collector = BGPStateCollector(readers={RedisReader.APPL_DB: appl_reader})
        result = collector.collect(neighbor="10.0.0.1")

        assert "10.0.0.1" in result
        assert len(result) == 1
        assert result["10.0.0.1"]["peer_as"] == "65001"
        assert result["10.0.0.1"]["hold_time"] == 180

    def test_collect_nonexistent_neighbor(self, appl_reader):
        collector = BGPStateCollector(readers={RedisReader.APPL_DB: appl_reader})
        result = collector.collect(neighbor="10.99.99.99")
        assert result == {}


# --- OpticHealthCollector ---


class TestOpticHealthCollector:
    def test_collect_all(self, state_reader):
        collector = OpticHealthCollector(readers={RedisReader.STATE_DB: state_reader})
        result = collector.collect()

        assert "Ethernet0" in result
        assert "Ethernet48" in result
        assert result["Ethernet0"]["temperature"] == 32.5
        assert len(result["Ethernet0"]["rx_power_dbm"]) == 4

    def test_degraded_optic(self, state_reader):
        collector = OpticHealthCollector(readers={RedisReader.STATE_DB: state_reader})
        result = collector.collect(port="Ethernet48")

        eth48 = result["Ethernet48"]
        assert eth48["rx_power_avg_dbm"] < -7.0  # degraded
        assert eth48["tx_power_avg_dbm"] > -2.0  # TX is fine
        assert eth48["temperature"] == 45.2
        assert eth48["vendor"] == "Finisar"

    def test_collect_single_port(self, state_reader):
        collector = OpticHealthCollector(readers={RedisReader.STATE_DB: state_reader})
        result = collector.collect(port="Ethernet0")

        assert "Ethernet0" in result
        assert len(result) == 1
        assert result["Ethernet0"]["type"] == "QSFP28"


# --- LogFilterCollector ---


class TestLogFilterCollector:
    def test_collect_from_file(self, tmp_path):
        log_file = tmp_path / "syslog"
        log_file.write_text(
            "Mar 19 14:00:01 switch-a CRON[1234]: (root) CMD (test)\n"
            "Mar 19 14:00:02 switch-a orchagent: Port Ethernet48 link down\n"
            "Mar 19 14:00:03 switch-a bgp#zebra: Neighbor 10.0.0.3 went down\n"
            "Mar 19 14:00:04 switch-a systemd[1]: Started Session 42\n"
            "Mar 19 14:00:05 switch-a syncd: SAI_STATUS_SUCCESS\n"
        )
        collector = LogFilterCollector(log_path=str(log_file))
        result = collector.collect()

        # CRON and systemd lines should be filtered as noise
        lines = [r["line"] for r in result]
        assert not any("CRON" in line for line in lines)
        assert not any("Started Session" in line for line in lines)

        # orchagent and bgp lines should remain and be interesting
        assert any("orchagent" in line for line in lines)
        interesting = [r for r in result if r["interesting"]]
        assert len(interesting) >= 2

    def test_collect_nonexistent_file(self):
        collector = LogFilterCollector(log_path="/nonexistent/syslog")
        result = collector.collect()
        assert result == []

    def test_noise_filtering(self, tmp_path):
        log_file = tmp_path / "syslog"
        log_file.write_text(
            "pam_unix(sshd:session): session opened for user admin\n"
            "rsyslogd: action resumed\n"
            "supervisord: INFO something\n"
        )
        collector = LogFilterCollector(log_path=str(log_file))
        result = collector.collect()
        assert len(result) == 0

    def test_empty_log(self, tmp_path):
        log_file = tmp_path / "syslog"
        log_file.write_text("")
        collector = LogFilterCollector(log_path=str(log_file))
        result = collector.collect()
        assert result == []
