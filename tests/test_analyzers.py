"""Tests for baseline comparison analyzer and template-based findings."""

import pytest

from watchtower.analyzers.baseline_compare import AnomalyResult, BaselineCompareAnalyzer
from watchtower.config import AnomalyConfig
from watchtower.llm.fallback import (
    finding_from_anomaly,
    finding_from_bgp_change,
    finding_from_link_change,
    finding_from_optic_degradation,
)
from watchtower.store.baselines import BaselineStore
from watchtower.store.journal import Journal


@pytest.fixture
def journal():
    j = Journal(":memory:")
    yield j
    j.close()


@pytest.fixture
def baselines(journal):
    return BaselineStore(journal)


@pytest.fixture
def analyzer(journal):
    config = AnomalyConfig(
        deviation_threshold=5.0,
        immediate_threshold=10.0,
        baseline_warmup_hours=10,  # low threshold for testing
    )
    return BaselineCompareAnalyzer(journal, anomaly_config=config)


class TestBaselineCompareAnalyzer:
    def test_no_baseline_returns_no_anomaly(self, analyzer):
        result = analyzer.analyze("Ethernet0", "rx_errors", 100.0)
        assert not result.is_anomaly
        assert not result.warmed_up
        assert result.current_value == 100.0

    def test_warmup_prevents_anomaly(self, analyzer, baselines):
        # Add a few samples but not enough to warm up
        for _ in range(5):
            baselines.update("Ethernet0", "rx_errors", 1.0)

        result = analyzer.analyze("Ethernet0", "rx_errors", 100.0)
        assert not result.is_anomaly  # not warmed up yet
        assert not result.warmed_up

    def test_detects_anomaly_after_warmup(self, analyzer, baselines):
        # Feed enough samples to warm up
        for _ in range(15):
            baselines.update("Ethernet0", "rx_errors", 2.0)

        # Now check a high value
        result = analyzer.analyze("Ethernet0", "rx_errors", 100.0)
        assert result.warmed_up
        assert result.is_anomaly
        assert result.deviation_factor > 5.0

    def test_immediate_threshold(self, analyzer, baselines):
        for _ in range(15):
            baselines.update("Ethernet0", "rx_errors", 2.0)

        result = analyzer.analyze("Ethernet0", "rx_errors", 500.0)
        assert result.is_anomaly
        assert result.is_immediate

    def test_normal_value_no_anomaly(self, analyzer, baselines):
        for _ in range(15):
            baselines.update("Ethernet0", "rx_errors", 10.0)

        result = analyzer.analyze("Ethernet0", "rx_errors", 12.0)
        assert result.warmed_up
        assert not result.is_anomaly

    def test_check_port_stats(self, analyzer, baselines):
        # Warm up baselines
        for _ in range(15):
            baselines.update("Ethernet48", "rx_crc_errors", 2.0)
            baselines.update("Ethernet48", "rx_errors", 2.0)
            baselines.update("Ethernet48", "rx_drops", 5.0)
            baselines.update("Ethernet48", "tx_errors", 0.0)
            baselines.update("Ethernet48", "tx_drops", 0.0)

        stats = {
            "rx_crc_errors": 1847,
            "rx_errors": 1847,
            "rx_drops": 8,
            "tx_errors": 0,
            "tx_drops": 0,
        }

        anomalies = analyzer.check_port_stats("Ethernet48", stats)
        # rx_crc_errors and rx_errors should be anomalous
        anomaly_metrics = {a.metric for a in anomalies}
        assert "rx_crc_errors" in anomaly_metrics
        assert "rx_errors" in anomaly_metrics
        # tx_errors=0 should not be anomalous
        assert "tx_errors" not in anomaly_metrics

    def test_to_dict(self, analyzer):
        result = analyzer.analyze("Ethernet0", "rx_errors", 50.0)
        d = result.to_dict()
        assert d["port"] == "Ethernet0"
        assert d["metric"] == "rx_errors"
        assert d["current_value"] == 50.0


# --- Template Findings ---


class TestTemplateFindingFromAnomaly:
    def test_warning_finding(self):
        result = AnomalyResult(
            port="Ethernet48",
            metric="rx_crc_errors",
            current_value=1847,
            baseline_p50=2.0,
            baseline_p95=12.0,
            baseline_p99=20.0,
            deviation_factor=153.9,
            is_anomaly=True,
            is_immediate=False,
            warmed_up=True,
        )
        finding = finding_from_anomaly(result)
        assert finding["severity"] == "warning"
        assert "Ethernet48" in finding["summary"]
        assert "1847" in finding["summary"]
        assert "153.9x" in finding["summary"]

    def test_critical_finding(self):
        result = AnomalyResult(
            port="Ethernet48",
            metric="rx_crc_errors",
            current_value=5000,
            baseline_p50=2.0,
            baseline_p95=12.0,
            baseline_p99=20.0,
            deviation_factor=416.7,
            is_anomaly=True,
            is_immediate=True,
            warmed_up=True,
        )
        finding = finding_from_anomaly(result)
        assert finding["severity"] == "critical"

    def test_finding_with_neighbor(self):
        result = AnomalyResult(
            port="Ethernet48",
            metric="rx_errors",
            current_value=100,
            baseline_p50=1.0,
            baseline_p95=5.0,
            baseline_p99=10.0,
            deviation_factor=20.0,
            is_anomaly=True,
            is_immediate=True,
            warmed_up=True,
        )
        neighbor = {"neighbor_hostname": "switch-b", "neighbor_port": "Ethernet12"}
        finding = finding_from_anomaly(result, neighbor_info=neighbor)
        assert "switch-b" in finding["summary"]


class TestTemplateFindingBGP:
    def test_session_down(self):
        finding = finding_from_bgp_change("10.0.0.1", "Established", "Idle", "spine-1")
        assert finding["severity"] == "warning"
        assert "went down" in finding["summary"]
        assert "spine-1" in finding["summary"]

    def test_session_up(self):
        finding = finding_from_bgp_change("10.0.0.1", "Idle", "Established")
        assert finding["severity"] == "info"
        assert "came up" in finding["summary"]

    def test_other_transition(self):
        finding = finding_from_bgp_change("10.0.0.1", "Idle", "Active")
        assert finding["severity"] == "info"
        assert "Idle -> Active" in finding["summary"]


class TestTemplateFindingLink:
    def test_link_down(self):
        finding = finding_from_link_change("Ethernet48", "down", {"neighbor_hostname": "switch-b"})
        assert finding["severity"] == "warning"
        assert "down" in finding["summary"]
        assert "switch-b" in finding["summary"]

    def test_link_up(self):
        finding = finding_from_link_change("Ethernet48", "up")
        assert finding["severity"] == "info"
        assert "up" in finding["summary"]


class TestTemplateFindingOptic:
    def test_degraded(self):
        finding = finding_from_optic_degradation(
            "Ethernet48",
            -8.2,
            baseline_rx_power=-2.1,
            neighbor_info={"neighbor_hostname": "switch-b"},
        )
        assert finding["severity"] == "warning"
        assert "-8.2" in finding["summary"]
        assert "switch-b" in finding["summary"]

    def test_critical_degradation(self):
        finding = finding_from_optic_degradation("Ethernet48", -12.5)
        assert finding["severity"] == "critical"
