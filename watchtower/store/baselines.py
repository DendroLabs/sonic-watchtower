"""Baseline computation and storage using exponential moving averages."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from watchtower.store.journal import Journal


def _hour_of_week(dt: datetime | None = None) -> int:
    """Return 0-167 bucket for the current hour-of-week."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.weekday() * 24 + dt.hour


class BaselineStore:
    """Read/write baseline statistics bucketed by hour-of-week."""

    # EMA smoothing factor: higher = more weight on recent samples
    ALPHA = 0.05

    def __init__(self, journal: Journal):
        self._journal = journal

    def get(self, port: str, metric: str, hour: int | None = None) -> dict | None:
        """Get baseline for a port/metric at the given hour-of-week (default: now)."""
        if hour is None:
            hour = _hour_of_week()

        row = self._journal.conn.execute(
            "SELECT p50, p95, p99, sample_count, updated_at "
            "FROM baselines WHERE port = ? AND metric = ? AND hour_of_week = ?",
            (port, metric, hour),
        ).fetchone()

        if row is None:
            return None

        return {
            "port": port,
            "metric": metric,
            "hour_of_week": hour,
            "p50": row["p50"],
            "p95": row["p95"],
            "p99": row["p99"],
            "sample_count": row["sample_count"],
            "updated_at": row["updated_at"],
        }

    def update(self, port: str, metric: str, value: float, dt: datetime | None = None):
        """Update the baseline with a new sample using EMA."""
        hour = _hour_of_week(dt)
        existing = self.get(port, metric, hour)

        if existing is None:
            # First sample -- seed all percentile estimates with this value
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._journal.conn.execute(
                "INSERT INTO baselines (port, metric, hour_of_week, p50, p95, p99, sample_count, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (port, metric, hour, value, value, value, now),
            )
        else:
            alpha = self.ALPHA
            # EMA update for each percentile estimate
            # p50: target quantile 0.5, p95: 0.95, p99: 0.99
            p50 = self._ema_quantile(existing["p50"], value, alpha, 0.50)
            p95 = self._ema_quantile(existing["p95"], value, alpha, 0.95)
            p99 = self._ema_quantile(existing["p99"], value, alpha, 0.99)
            count = existing["sample_count"] + 1
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            self._journal.conn.execute(
                "UPDATE baselines SET p50 = ?, p95 = ?, p99 = ?, sample_count = ?, updated_at = ? "
                "WHERE port = ? AND metric = ? AND hour_of_week = ?",
                (p50, p95, p99, count, now, port, metric, hour),
            )

        self._journal.conn.commit()

    @staticmethod
    def _ema_quantile(current: float, sample: float, alpha: float, quantile: float) -> float:
        """Update a quantile estimate using an incremental approach.

        If the sample is above the current estimate, nudge up proportional
        to (1 - quantile). If below, nudge up proportional to quantile.
        This converges to the true quantile over time.
        """
        if sample > current:
            return current + alpha * (1 - quantile)
        elif sample < current:
            return current - alpha * quantile
        return current

    def get_all_for_port(self, port: str) -> list[dict]:
        """Get all baseline entries for a given port."""
        rows = self._journal.conn.execute(
            "SELECT port, metric, hour_of_week, p50, p95, p99, sample_count "
            "FROM baselines WHERE port = ? ORDER BY metric, hour_of_week",
            (port,),
        ).fetchall()
        return [dict(r) for r in rows]

    def is_warmed_up(self, port: str, metric: str, min_samples: int = 168) -> bool:
        """Check if we have enough samples across all hours for reliable baselines."""
        row = self._journal.conn.execute(
            "SELECT COALESCE(SUM(sample_count), 0) as total "
            "FROM baselines WHERE port = ? AND metric = ?",
            (port, metric),
        ).fetchone()
        return row["total"] >= min_samples
