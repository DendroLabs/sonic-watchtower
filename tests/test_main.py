"""Integration tests for the Watchtower main event loop with mock Redis."""

from __future__ import annotations

import json
from pathlib import Path

import fakeredis
import pytest

from watchtower.config import WatchtowerConfig, AnomalyConfig, BannerConfig, SyslogConfig
from watchtower.collectors.base import RedisReader
from watchtower.main import WatchtowerDaemon

MOCK_DIR = Path(__file__).parent / "mock_redis"


def _load_fixture(filename: str) -> dict:
    with open(MOCK_DIR / filename) as f:
        return json.load(f)


def _populate_redis(client: fakeredis.FakeRedis, data: dict):
    for key, value in data.items():
        if isinstance(value, dict):
            client.hset(key, mapping=value)
        else:
            client.set(key, value)


@pytest.fixture
def mock_readers():
    """Create fakeredis readers loaded with all fixture data."""
    counters_client = fakeredis.FakeRedis(decode_responses=True)
    appl_client = fakeredis.FakeRedis(decode_responses=True)
    state_client = fakeredis.FakeRedis(decode_responses=True)

    _populate_redis(counters_client, _load_fixture("counters_db.json"))
    _populate_redis(appl_client, _load_fixture("appl_db.json"))
    _populate_redis(state_client, _load_fixture("state_db.json"))

    return {
        RedisReader.COUNTERS_DB: RedisReader.from_client(counters_client),
        RedisReader.APPL_DB: RedisReader.from_client(appl_client),
        RedisReader.STATE_DB: RedisReader.from_client(state_client),
    }


@pytest.fixture
def daemon(tmp_path, mock_readers):
    """Create a WatchtowerDaemon with mock Redis and temp storage."""
    config = WatchtowerConfig(
        hostname="test-switch",
        poll_interval=30,
        anomaly=AnomalyConfig(
            deviation_threshold=5.0,
            immediate_threshold=10.0,
            baseline_warmup_hours=5,  # low for testing
        ),
        banner=BannerConfig(
            findings_file=str(tmp_path / "banner.txt"),
        ),
        syslog=SyslogConfig(min_severity="info"),
    )
    config.journal.path = str(tmp_path / "journal.db")

    d = WatchtowerDaemon(config, readers=mock_readers, syslog_dry_run=True)
    yield d
    d.journal.close()


class TestPollCycle:
    def test_single_cycle_collects_data(self, daemon):
        """A single poll cycle should collect and store topology."""
        daemon.run_once()

        topo = daemon.topology_store.get_all()
        assert len(topo) >= 2
        hostnames = {t["neighbor_hostname"] for t in topo}
        assert "spine-1" in hostnames
        assert "switch-b" in hostnames

    def test_single_cycle_records_events(self, daemon):
        """First cycle establishes baseline, shouldn't trigger anomalies."""
        daemon.run_once()
        # First cycle seeds baselines -- no anomalies expected yet
        # (baselines not warmed up)
        events = daemon.events.get_recent(seconds=60)
        # Optic degradation should still be detected (threshold-based, not baseline-based)
        optic_events = [e for e in events if e["category"] == "optic_degradation"]
        assert len(optic_events) >= 1  # Ethernet48 has degraded optics

    def test_detects_optic_degradation(self, daemon):
        """Should detect the degraded optic on Ethernet48 from fixture data."""
        daemon.run_once()

        active = daemon.findings.get_active()
        optic_findings = [f for f in active if "Optic" in f.get("summary", "")]
        assert len(optic_findings) >= 1
        assert any("Ethernet48" in f["summary"] for f in optic_findings)

    def test_detects_bgp_change(self, daemon, mock_readers):
        """Should detect BGP state change between cycles."""
        # First cycle establishes baseline states
        daemon.run_once()

        # Simulate BGP session going down
        appl_reader = mock_readers[RedisReader.APPL_DB]
        appl_reader._client.hset("BGP_NEIGHBOR_TABLE:10.0.0.1", "state", "Idle")

        # Second cycle detects the change
        daemon.run_once()

        active = daemon.findings.get_active()
        bgp_findings = [f for f in active if "BGP" in f.get("summary", "")]
        assert len(bgp_findings) >= 1
        assert any("went down" in f["summary"] for f in bgp_findings)

    def test_detects_link_change(self, daemon, mock_readers):
        """Should detect link state change between cycles."""
        daemon.run_once()

        # Simulate link going down
        state_reader = mock_readers[RedisReader.STATE_DB]
        state_reader._client.hset("PORT_TABLE|Ethernet0", "oper_status", "down")

        daemon.run_once()

        active = daemon.findings.get_active()
        link_findings = [f for f in active if "Link" in f.get("summary", "") or "link" in f.get("summary", "")]
        assert len(link_findings) >= 1

    def test_banner_updated(self, daemon, tmp_path):
        """Banner file should be written after a poll cycle."""
        daemon.run_once()

        banner_path = tmp_path / "banner.txt"
        assert banner_path.exists()
        content = banner_path.read_text()
        assert "WATCHTOWER" in content

    def test_syslog_emitted(self, daemon):
        """Findings should be emitted to syslog (dry run)."""
        daemon.run_once()

        # At minimum, optic degradation should emit to syslog
        assert len(daemon.syslog.emitted) >= 1

    def test_governor_state_check(self, daemon):
        """Governor should be in FULL state under normal conditions."""
        daemon.run_once()
        assert daemon.governor.state.value == "full"

    def test_multiple_cycles_stable(self, daemon):
        """Multiple poll cycles should not crash or create duplicate findings."""
        daemon.run_once()
        daemon.run_once()
        daemon.run_once()

        # Should have findings but not exploding counts
        active = daemon.findings.get_active()
        assert len(active) > 0
        assert len(active) < 50  # sanity check


class TestDaemonInit:
    def test_creates_journal_directory(self, tmp_path, mock_readers):
        db_path = str(tmp_path / "deep" / "nested" / "journal.db")
        config = WatchtowerConfig(hostname="test")
        config.journal.path = db_path
        config.banner.findings_file = str(tmp_path / "banner.txt")

        daemon = WatchtowerDaemon(config, readers=mock_readers, syslog_dry_run=True)
        assert Path(db_path).parent.exists()
        daemon.journal.close()
