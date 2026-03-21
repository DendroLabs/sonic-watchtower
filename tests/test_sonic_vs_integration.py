"""Integration tests against a real SONiC VS (virtual switch) instance.

These tests are SKIPPED by default. They require a running SONiC VS container
with Redis accessible on localhost.

Run with:
    pytest tests/test_sonic_vs_integration.py -v -m sonic_vs

Or via the validation script:
    ./scripts/validate_sonic_vs.sh
"""

from __future__ import annotations

import pytest
import redis

from watchtower.collectors.base import RedisReader
from watchtower.collectors.bgp_state import BGPStateCollector
from watchtower.collectors.interface_state import InterfaceStateCollector
from watchtower.collectors.lldp_topology import LLDPTopologyCollector
from watchtower.collectors.optic_health import OpticHealthCollector
from watchtower.collectors.port_stats import PortStatsCollector

pytestmark = pytest.mark.sonic_vs


def _can_connect() -> bool:
    """Check if we can connect to a local Redis (SONiC VS)."""
    try:
        client = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
        client.ping()
        return True
    except (redis.ConnectionError, redis.TimeoutError):
        return False


skip_no_sonic = pytest.mark.skipif(
    not _can_connect(),
    reason="No SONiC VS Redis available on localhost:6379",
)


def _make_readers() -> dict[int, RedisReader]:
    """Create RedisReaders for all SONiC databases."""
    return {
        RedisReader.APPL_DB: RedisReader(db=RedisReader.APPL_DB),
        RedisReader.COUNTERS_DB: RedisReader(db=RedisReader.COUNTERS_DB),
        RedisReader.STATE_DB: RedisReader(db=RedisReader.STATE_DB),
    }


@skip_no_sonic
class TestPortStatsVS:
    def test_collect_returns_dict(self) -> None:
        readers = _make_readers()
        collector = PortStatsCollector(readers=readers)
        result = collector.collect()
        assert isinstance(result, dict)

    def test_port_map_exists(self) -> None:
        reader = RedisReader(db=RedisReader.COUNTERS_DB)
        port_map = reader.hgetall("COUNTERS_PORT_NAME_MAP")
        # VS should have at least some ports
        assert isinstance(port_map, dict)


@skip_no_sonic
class TestInterfaceStateVS:
    def test_collect_returns_dict(self) -> None:
        readers = _make_readers()
        collector = InterfaceStateCollector(readers=readers)
        result = collector.collect()
        assert isinstance(result, dict)


@skip_no_sonic
class TestLLDPTopologyVS:
    def test_collect_returns_dict(self) -> None:
        readers = _make_readers()
        collector = LLDPTopologyCollector(readers=readers)
        result = collector.collect()
        assert isinstance(result, dict)


@skip_no_sonic
class TestBGPStateVS:
    def test_collect_returns_dict(self) -> None:
        readers = _make_readers()
        collector = BGPStateCollector(readers=readers)
        result = collector.collect()
        assert isinstance(result, dict)


@skip_no_sonic
class TestOpticHealthVS:
    def test_collect_returns_dict(self) -> None:
        readers = _make_readers()
        collector = OpticHealthCollector(readers=readers)
        result = collector.collect()
        assert isinstance(result, dict)
