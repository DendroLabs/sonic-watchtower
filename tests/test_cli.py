"""Tests for the Watchtower CLI."""

import pytest
from click.testing import CliRunner

from watchtower.cli.main import cli
from watchtower.store.journal import Journal
from watchtower.store.findings import FindingsStore
from watchtower.store.events import EventStore
from watchtower.store.topology import TopologyStore
from watchtower.store.baselines import BaselineStore
from watchtower.config import WatchtowerConfig


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def populated_db(tmp_path):
    """Create a journal with test data and return the path."""
    db_path = str(tmp_path / "test.db")
    journal = Journal(db_path)

    # Add findings
    findings = FindingsStore(journal)
    findings.create(severity="critical", summary="Optic degradation on Ethernet48.",
                    finding_id="f-test-001")
    findings.create(severity="warning", summary="BGP session flapping.",
                    finding_id="f-test-002")

    # Add events
    events = EventStore(journal)
    events.record(source="local", category="anomaly", severity="warning",
                  port="Ethernet48", raw_data={"rx_crc_errors": 1847})
    events.record(source="local", category="bgp_change", severity="info")

    # Add topology
    topo = TopologyStore(journal)
    topo.update_neighbor("Ethernet0", "spine-1", "Ethernet0")
    topo.update_neighbor("Ethernet48", "switch-b", "Ethernet12")

    # Add baselines
    baselines = BaselineStore(journal)
    baselines.update("Ethernet48", "rx_crc_errors", 2.0)
    baselines.update("Ethernet48", "rx_crc_errors", 3.0)

    journal.close()
    return db_path


@pytest.fixture
def config_file(tmp_path, populated_db):
    """Create a config file pointing to the test DB."""
    cfg = tmp_path / "watchtower.yml"
    cfg.write_text(f"journal:\n  path: {populated_db}\n")
    return str(cfg)


class TestCLIBasic:
    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Watchtower" in result.output

    def test_show_help(self, runner):
        result = runner.invoke(cli, ["show", "--help"])
        assert result.exit_code == 0
        assert "findings" in result.output
        assert "topology" in result.output
        assert "events" in result.output
        assert "resources" in result.output


class TestShowFindings:
    def test_show_active_findings(self, runner, config_file):
        result = runner.invoke(cli, ["-c", config_file, "show", "findings"])
        assert result.exit_code == 0
        assert "Active findings: 2" in result.output
        assert "Optic degradation" in result.output
        assert "BGP session" in result.output

    def test_show_findings_empty(self, runner, tmp_path):
        db_path = str(tmp_path / "empty.db")
        Journal(db_path).close()
        cfg = tmp_path / "empty.yml"
        cfg.write_text(f"journal:\n  path: {db_path}\n")
        result = runner.invoke(cli, ["-c", str(cfg), "show", "findings"])
        assert result.exit_code == 0
        assert "No active findings" in result.output

    def test_show_findings_by_severity(self, runner, config_file):
        result = runner.invoke(cli, ["-c", config_file, "show", "findings",
                                     "--severity", "critical"])
        assert result.exit_code == 0
        assert "Optic degradation" in result.output


class TestShowTopology:
    def test_show_topology(self, runner, config_file):
        result = runner.invoke(cli, ["-c", config_file, "show", "topology"])
        assert result.exit_code == 0
        assert "spine-1" in result.output
        assert "switch-b" in result.output
        assert "Ethernet48" in result.output

    def test_show_topology_empty(self, runner, tmp_path):
        db_path = str(tmp_path / "empty.db")
        Journal(db_path).close()
        cfg = tmp_path / "empty.yml"
        cfg.write_text(f"journal:\n  path: {db_path}\n")
        result = runner.invoke(cli, ["-c", str(cfg), "show", "topology"])
        assert result.exit_code == 0
        assert "No topology data" in result.output


class TestShowEvents:
    def test_show_events(self, runner, config_file):
        result = runner.invoke(cli, ["-c", config_file, "show", "events"])
        assert result.exit_code == 0
        assert "anomaly" in result.output
        assert "bgp_change" in result.output

    def test_show_events_by_severity(self, runner, config_file):
        result = runner.invoke(cli, ["-c", config_file, "show", "events",
                                     "--severity", "warning"])
        assert result.exit_code == 0
        assert "anomaly" in result.output


class TestShowResources:
    def test_show_resources(self, runner):
        result = runner.invoke(cli, ["show", "resources"])
        assert result.exit_code == 0
        assert "State:" in result.output
        assert "Watchtower CPU:" in result.output


class TestShowBaselines:
    def test_show_baselines(self, runner, config_file):
        result = runner.invoke(cli, ["-c", config_file, "show", "baselines", "Ethernet48"])
        assert result.exit_code == 0
        assert "rx_crc_errors" in result.output
        assert "samples=" in result.output

    def test_show_baselines_empty(self, runner, config_file):
        result = runner.invoke(cli, ["-c", config_file, "show", "baselines", "Ethernet99"])
        assert result.exit_code == 0
        assert "No baseline data" in result.output
