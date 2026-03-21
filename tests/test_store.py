"""Tests for the store layer: journal, baselines, topology, findings, events."""

import sqlite3
import time
from datetime import UTC

import pytest

from watchtower.store.baselines import BaselineStore
from watchtower.store.events import EventStore
from watchtower.store.findings import FindingsStore
from watchtower.store.journal import Journal
from watchtower.store.topology import TopologyStore


@pytest.fixture
def journal():
    j = Journal(":memory:")
    yield j
    j.close()


@pytest.fixture
def baselines(journal):
    return BaselineStore(journal)


@pytest.fixture
def topology(journal):
    return TopologyStore(journal)


@pytest.fixture
def findings(journal):
    return FindingsStore(journal)


@pytest.fixture
def events(journal):
    return EventStore(journal)


# --- Journal ---


class TestJournal:
    def test_schema_creates_tables(self, journal):
        tables = journal.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [t["name"] for t in tables]
        assert "baselines" in names
        assert "events" in names
        assert "topology" in names
        assert "findings" in names
        assert "peer_state" in names

    def test_double_init_is_idempotent(self, journal):
        # Re-initializing should not raise
        journal._init_schema()
        tables = journal.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        assert len(tables) >= 5

    def test_wal_mode(self, tmp_path):
        """WAL mode only works with file-based databases, not :memory:."""
        j = Journal(str(tmp_path / "test.db"))
        mode = j.conn.execute("PRAGMA journal_mode").fetchone()
        assert mode[0] == "wal"
        j.close()


# --- Baselines ---


class TestBaselines:
    def test_get_nonexistent_returns_none(self, baselines):
        assert baselines.get("Ethernet0", "rx_bytes", 0) is None

    def test_first_sample_seeds_values(self, baselines):
        baselines.update("Ethernet0", "rx_crc_errors", 100.0)
        result = baselines.get("Ethernet0", "rx_crc_errors")
        assert result is not None
        assert result["p50"] == 100.0
        assert result["p95"] == 100.0
        assert result["p99"] == 100.0
        assert result["sample_count"] == 1

    def test_multiple_updates_increment_count(self, baselines):
        for i in range(10):
            baselines.update("Ethernet0", "rx_bytes", float(i * 1000))
        result = baselines.get("Ethernet0", "rx_bytes")
        assert result["sample_count"] == 10

    def test_ema_moves_estimates(self, baselines):
        # Seed with 100
        baselines.update("Ethernet0", "rx_crc_errors", 100.0)
        # Feed values well above -- p50 should move up
        for _ in range(50):
            baselines.update("Ethernet0", "rx_crc_errors", 200.0)
        result = baselines.get("Ethernet0", "rx_crc_errors")
        assert result["p50"] > 100.0  # should have moved toward 200

    def test_hour_of_week_bucketing(self, baselines):
        from datetime import datetime

        # Monday 3 AM = hour 3
        dt_mon = datetime(2026, 3, 16, 3, 0, tzinfo=UTC)  # Monday
        baselines.update("Ethernet0", "rx_bytes", 1000.0, dt=dt_mon)

        # Wednesday 15:00 = hour 2*24+15 = 63
        dt_wed = datetime(2026, 3, 18, 15, 0, tzinfo=UTC)  # Wednesday
        baselines.update("Ethernet0", "rx_bytes", 5000.0, dt=dt_wed)

        mon_result = baselines.get("Ethernet0", "rx_bytes", hour=3)
        wed_result = baselines.get("Ethernet0", "rx_bytes", hour=63)
        assert mon_result is not None
        assert wed_result is not None
        assert mon_result["p50"] == 1000.0
        assert wed_result["p50"] == 5000.0

    def test_get_all_for_port(self, baselines):
        baselines.update("Ethernet0", "rx_bytes", 100.0)
        baselines.update("Ethernet0", "tx_bytes", 200.0)
        baselines.update("Ethernet4", "rx_bytes", 300.0)

        results = baselines.get_all_for_port("Ethernet0")
        assert len(results) == 2
        metrics = {r["metric"] for r in results}
        assert metrics == {"rx_bytes", "tx_bytes"}

    def test_is_warmed_up(self, baselines):
        # Not warmed up with just 1 sample
        baselines.update("Ethernet0", "rx_bytes", 100.0)
        assert not baselines.is_warmed_up("Ethernet0", "rx_bytes", min_samples=10)

        # Feed more samples
        for i in range(20):
            baselines.update("Ethernet0", "rx_bytes", float(i))
        # Now have 21 total samples
        assert baselines.is_warmed_up("Ethernet0", "rx_bytes", min_samples=20)


# --- Topology ---


class TestTopology:
    def test_add_and_get_neighbor(self, topology):
        topology.update_neighbor("Ethernet48", "switch-b", "Ethernet12")
        result = topology.get_neighbor("Ethernet48")
        assert result is not None
        assert result["neighbor_hostname"] == "switch-b"
        assert result["neighbor_port"] == "Ethernet12"

    def test_get_nonexistent_returns_none(self, topology):
        assert topology.get_neighbor("Ethernet99") is None

    def test_update_refreshes_last_seen(self, topology):
        topology.update_neighbor("Ethernet48", "switch-b", "Ethernet12")
        first = topology.get_neighbor("Ethernet48")

        time.sleep(0.01)
        topology.update_neighbor("Ethernet48", "switch-b", "Ethernet12")
        second = topology.get_neighbor("Ethernet48")

        assert second["last_seen"] >= first["last_seen"]
        assert second["first_seen"] == first["first_seen"]

    def test_get_all(self, topology):
        topology.update_neighbor("Ethernet0", "spine-1", "Ethernet0")
        topology.update_neighbor("Ethernet4", "spine-2", "Ethernet0")
        topology.update_neighbor("Ethernet48", "switch-b", "Ethernet12")

        all_entries = topology.get_all()
        assert len(all_entries) == 3

    def test_get_ports_to_host(self, topology):
        topology.update_neighbor("Ethernet0", "spine-1", "Ethernet0")
        topology.update_neighbor("Ethernet4", "spine-1", "Ethernet4")
        topology.update_neighbor("Ethernet8", "spine-2", "Ethernet0")

        spine1_ports = topology.get_ports_to_host("spine-1")
        assert len(spine1_ports) == 2

    def test_remove_stale(self, topology):
        topology.update_neighbor("Ethernet0", "spine-1", "Ethernet0")
        # All entries are fresh, nothing should be removed
        topology.remove_stale(max_age_seconds=300)
        assert len(topology.get_all()) == 1


# --- Findings ---


class TestFindings:
    def test_create_and_get(self, findings):
        fid = findings.create(
            severity="warning",
            summary="Test finding",
            detail="Detailed description",
            finding_id="f-test-001",
        )
        assert fid == "f-test-001"

        result = findings.get("f-test-001")
        assert result is not None
        assert result["severity"] == "warning"
        assert result["summary"] == "Test finding"
        assert result["active"] == 1

    def test_auto_generated_id(self, findings):
        fid = findings.create(severity="info", summary="Auto ID test")
        assert fid.startswith("f-")
        result = findings.get(fid)
        assert result is not None

    def test_get_nonexistent_returns_none(self, findings):
        assert findings.get("f-nonexistent") is None

    def test_resolve(self, findings):
        fid = findings.create(
            severity="critical", summary="Critical issue", finding_id="f-test-002"
        )
        findings.resolve(fid)

        result = findings.get(fid)
        assert result["active"] == 0
        assert result["resolved_at"] is not None

    def test_get_active(self, findings):
        findings.create(severity="critical", summary="Critical", finding_id="f-crit-001")
        findings.create(severity="warning", summary="Warning", finding_id="f-warn-001")
        findings.create(severity="info", summary="Info", finding_id="f-info-001")

        active = findings.get_active()
        assert len(active) == 3
        # Should be ordered: critical, warning, info
        assert active[0]["severity"] == "critical"
        assert active[1]["severity"] == "warning"
        assert active[2]["severity"] == "info"

    def test_get_active_filtered(self, findings):
        findings.create(severity="critical", summary="Critical", finding_id="f-crit-002")
        findings.create(severity="warning", summary="Warning", finding_id="f-warn-002")

        critical = findings.get_active(severity="critical")
        assert len(critical) == 1
        assert critical[0]["severity"] == "critical"

    def test_resolved_not_in_active(self, findings):
        fid = findings.create(
            severity="warning", summary="Will resolve", finding_id="f-resolve-001"
        )
        findings.resolve(fid)

        active = findings.get_active()
        assert len(active) == 0

    def test_count_active(self, findings):
        findings.create(severity="critical", summary="C1", finding_id="f-c1")
        findings.create(severity="critical", summary="C2", finding_id="f-c2")
        findings.create(severity="warning", summary="W1", finding_id="f-w1")

        counts = findings.count_active()
        assert counts["critical"] == 2
        assert counts["warning"] == 1
        assert counts["info"] == 0
        assert counts["total"] == 3

    def test_related_events(self, findings):
        fid = findings.create(
            severity="warning",
            summary="Test",
            related_events=[1, 2, 3],
            finding_id="f-rel-001",
        )
        result = findings.get(fid)
        assert result["related_events"] == [1, 2, 3]


# --- Events ---


class TestEvents:
    def test_record_and_get_recent(self, events):
        eid = events.record(
            source="local",
            category="anomaly",
            severity="warning",
            port="Ethernet48",
            raw_data={"rx_crc_errors": 1847, "baseline_p99": 12},
        )
        assert eid is not None

        recent = events.get_recent(seconds=60)
        assert len(recent) == 1
        assert recent[0]["source"] == "local"
        assert recent[0]["raw_data"]["rx_crc_errors"] == 1847

    def test_record_without_port(self, events):
        events.record(source="syslog", category="bgp_change", severity="info")
        recent = events.get_recent(seconds=60)
        assert len(recent) == 1
        assert recent[0]["port"] is None

    def test_get_recent_filtered_by_severity(self, events):
        events.record(source="local", category="anomaly", severity="warning")
        events.record(source="local", category="anomaly", severity="info")
        events.record(source="local", category="link_down", severity="critical")

        warnings = events.get_recent(seconds=60, severity="warning")
        assert len(warnings) == 1
        assert warnings[0]["severity"] == "warning"

    def test_get_by_port(self, events):
        events.record(source="local", category="anomaly", severity="warning", port="Ethernet48")
        events.record(source="local", category="anomaly", severity="info", port="Ethernet0")
        events.record(source="local", category="crc_error", severity="warning", port="Ethernet48")

        eth48 = events.get_by_port("Ethernet48", seconds=60)
        assert len(eth48) == 2

    def test_count_recent(self, events):
        events.record(source="local", category="a", severity="warning")
        events.record(source="local", category="b", severity="warning")
        events.record(source="local", category="c", severity="critical")

        counts = events.count_recent(seconds=60)
        assert counts["warning"] == 2
        assert counts["critical"] == 1
        assert counts["total"] == 3

    def test_source_validation(self, events):
        """Only local, peer, syslog are valid sources."""
        with pytest.raises(sqlite3.IntegrityError):
            events.record(source="invalid", category="test", severity="info")
