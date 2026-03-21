"""Baseline comparison analyzer -- detects anomalies by comparing current values against baselines."""

from __future__ import annotations

from watchtower.analyzers.base import BaseAnalyzer
from watchtower.store.baselines import BaselineStore
from watchtower.store.journal import Journal
from watchtower.config import AnomalyConfig


class AnomalyResult:
    """Result of comparing a metric against its baseline."""

    def __init__(
        self,
        port: str,
        metric: str,
        current_value: float,
        baseline_p50: float,
        baseline_p95: float,
        baseline_p99: float,
        deviation_factor: float,
        is_anomaly: bool,
        is_immediate: bool,
        warmed_up: bool,
    ):
        self.port = port
        self.metric = metric
        self.current_value = current_value
        self.baseline_p50 = baseline_p50
        self.baseline_p95 = baseline_p95
        self.baseline_p99 = baseline_p99
        self.deviation_factor = deviation_factor
        self.is_anomaly = is_anomaly
        self.is_immediate = is_immediate
        self.warmed_up = warmed_up

    def to_dict(self) -> dict:
        return {
            "port": self.port,
            "metric": self.metric,
            "current_value": self.current_value,
            "baseline_p50": self.baseline_p50,
            "baseline_p95": self.baseline_p95,
            "baseline_p99": self.baseline_p99,
            "deviation_factor": self.deviation_factor,
            "is_anomaly": self.is_anomaly,
            "is_immediate": self.is_immediate,
            "warmed_up": self.warmed_up,
        }


class BaselineCompareAnalyzer(BaseAnalyzer):
    """Compares current metric values against stored baselines to detect anomalies."""

    def __init__(self, journal: Journal, anomaly_config: AnomalyConfig | None = None):
        super().__init__(journal)
        self._baselines = BaselineStore(journal)
        self._config = anomaly_config or AnomalyConfig()

    def analyze(self, port: str, metric: str, current_value: float) -> AnomalyResult:
        """Compare a current value against the baseline for this port/metric.

        Returns an AnomalyResult with deviation info and anomaly flags.
        """
        baseline = self._baselines.get(port, metric)
        warmed_up = self._baselines.is_warmed_up(
            port, metric,
            min_samples=self._config.baseline_warmup_hours,
        )

        if baseline is None:
            return AnomalyResult(
                port=port,
                metric=metric,
                current_value=current_value,
                baseline_p50=0.0,
                baseline_p95=0.0,
                baseline_p99=0.0,
                deviation_factor=0.0,
                is_anomaly=False,
                is_immediate=False,
                warmed_up=False,
            )

        p95 = baseline["p95"]
        p99 = baseline["p99"]

        # Calculate deviation factor relative to p95
        if p95 > 0:
            deviation_factor = current_value / p95
        elif current_value > 0:
            deviation_factor = float("inf")
        else:
            deviation_factor = 0.0

        is_anomaly = (
            warmed_up
            and deviation_factor >= self._config.deviation_threshold
        )
        is_immediate = (
            warmed_up
            and deviation_factor >= self._config.immediate_threshold
        )

        return AnomalyResult(
            port=port,
            metric=metric,
            current_value=current_value,
            baseline_p50=baseline["p50"],
            baseline_p95=p95,
            baseline_p99=p99,
            deviation_factor=deviation_factor,
            is_anomaly=is_anomaly,
            is_immediate=is_immediate,
            warmed_up=warmed_up,
        )

    def check_port_stats(self, port: str, stats: dict) -> list[AnomalyResult]:
        """Check all error/drop metrics for a port against baselines.

        Args:
            port: Port name
            stats: Dict of metric_name -> value from PortStatsCollector

        Returns:
            List of AnomalyResults for metrics that are anomalous.
        """
        error_metrics = ["rx_errors", "tx_errors", "rx_drops", "tx_drops", "rx_crc_errors"]
        anomalies = []

        for metric in error_metrics:
            value = stats.get(metric, 0)
            if value > 0:
                result = self.analyze(port, metric, float(value))
                if result.is_anomaly:
                    anomalies.append(result)

            # Always update baseline with current value
            self._baselines.update(port, metric, float(value))

        return anomalies
