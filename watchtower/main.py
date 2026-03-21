"""Watchtower main event loop -- poll cycle, anomaly detection, finding generation."""

from __future__ import annotations

import logging
import signal
import time
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

from watchtower.analyzers.baseline_compare import AnomalyResult, BaselineCompareAnalyzer
from watchtower.collectors.base import RedisReader
from watchtower.collectors.bgp_state import BGPStateCollector
from watchtower.collectors.interface_state import InterfaceStateCollector
from watchtower.collectors.lldp_topology import LLDPTopologyCollector
from watchtower.collectors.optic_health import OpticHealthCollector
from watchtower.collectors.port_stats import PortStatsCollector
from watchtower.config import WatchtowerConfig, load_config
from watchtower.governor import ResourceGovernor
from watchtower.llm.fallback import (
    finding_from_anomaly,
    finding_from_bgp_change,
    finding_from_link_change,
    finding_from_optic_degradation,
)
from watchtower.output.banner import BannerWriter
from watchtower.output.syslog_emitter import SyslogEmitter
from watchtower.store.baselines import BaselineStore
from watchtower.store.events import EventStore
from watchtower.store.findings import FindingsStore
from watchtower.store.journal import Journal
from watchtower.store.topology import TopologyStore

logger = logging.getLogger("watchtower")


class WatchtowerDaemon:
    """Main Watchtower daemon -- orchestrates the poll-detect-report cycle."""

    def __init__(
        self,
        config: WatchtowerConfig,
        readers: dict[int, RedisReader] | None = None,
        syslog_dry_run: bool = False,
    ):
        self.config = config
        self._running = False

        # Store layer
        db_path = config.journal.path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.journal = Journal(db_path)
        self.baselines = BaselineStore(self.journal)
        self.events = EventStore(self.journal)
        self.findings = FindingsStore(self.journal)
        self.topology_store = TopologyStore(self.journal)

        # Collectors
        self._readers = readers or {}
        self.port_stats = PortStatsCollector(readers=self._readers)
        self.interface_state = InterfaceStateCollector(readers=self._readers)
        self.lldp_topology = LLDPTopologyCollector(readers=self._readers)
        self.bgp_state = BGPStateCollector(readers=self._readers)
        self.optic_health = OpticHealthCollector(readers=self._readers)

        # Analyzer
        self.baseline_analyzer = BaselineCompareAnalyzer(
            self.journal, anomaly_config=config.anomaly
        )

        # Governor
        self.governor = ResourceGovernor(config.resources)

        # Output
        self.syslog = SyslogEmitter(config.syslog, dry_run=syslog_dry_run)
        self.banner = BannerWriter(config.banner)

        # State tracking for change detection
        self._last_bgp_states: dict[str, str] = {}
        self._last_oper_states: dict[str, str] = {}

    def run(self) -> None:
        """Run the main event loop."""
        self._running = True
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        logger.info(
            "Watchtower starting on %s (poll_interval=%ds)",
            self.config.hostname,
            self.config.poll_interval,
        )

        while self._running:
            cycle_start = time.time()
            try:
                self._poll_cycle()
            except Exception:
                logger.exception("Error in poll cycle")

            # Calculate sleep with governor multiplier
            interval = self.config.poll_interval * self.governor.poll_interval_multiplier
            elapsed = time.time() - cycle_start
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0 and self._running:
                time.sleep(sleep_time)

        logger.info("Watchtower stopped.")
        self.journal.close()

    def run_once(self) -> None:
        """Run a single poll cycle (useful for testing)."""
        self._poll_cycle()

    def _poll_cycle(self) -> None:
        """Execute one complete poll-detect-report cycle."""
        # Update governor state
        self.governor.update()

        if not self.governor.should_collect():
            logger.debug("Governor state: dormant -- skipping collection")
            return

        # 1. Collect data
        port_stats = self._safe_collect(self.port_stats.collect)
        iface_state = self._safe_collect(self.interface_state.collect)
        lldp_data = self._safe_collect(self.lldp_topology.collect)
        bgp_data = self._safe_collect(self.bgp_state.collect)
        optic_data = self._safe_collect(self.optic_health.collect)

        # 2. Update topology from LLDP
        if lldp_data:
            for local_port, neighbor in lldp_data.items():
                self.topology_store.update_neighbor(
                    local_port,
                    neighbor["neighbor_hostname"],
                    neighbor["neighbor_port"],
                )

        # 3. Detect anomalies in port stats
        if port_stats:
            for port_name, stats in port_stats.items():
                anomalies = self.baseline_analyzer.check_port_stats(port_name, stats)
                for anomaly in anomalies:
                    neighbor = lldp_data.get(port_name) if lldp_data else None
                    self._handle_anomaly(anomaly, neighbor)

        # 4. Detect BGP state changes
        if bgp_data:
            self._check_bgp_changes(bgp_data)

        # 5. Detect link state changes
        if iface_state:
            self._check_link_changes(iface_state, lldp_data)

        # 6. Check optic health
        if optic_data:
            self._check_optic_health(optic_data, lldp_data)

        # 7. Update banner with current active findings
        active = self.findings.get_active()
        self.banner.write(active)

        # 8. Prune old data periodically (every ~100 cycles)
        # Using a simple modulo on cycle count isn't ideal, but works for Phase 1
        self.journal.prune(
            detail_days=self.config.journal.retention_detail_days,
            summary_days=self.config.journal.retention_summary_days,
        )

    def _handle_anomaly(
        self, anomaly: AnomalyResult, neighbor_info: dict[str, Any] | None
    ) -> None:
        """Record an anomaly event and generate a template finding."""
        event_id = self.events.record(
            source="local",
            category="anomaly",
            severity="warning" if not anomaly.is_immediate else "critical",
            port=anomaly.port,
            raw_data=anomaly.to_dict(),
        )

        if self.governor.can_investigate():
            finding_data = finding_from_anomaly(anomaly, neighbor_info)
            finding_id = self.findings.create(
                severity=finding_data["severity"],
                summary=finding_data["summary"],
                detail=finding_data["detail"],
                related_events=[event_id],
            )
            self.syslog.emit_finding(finding_id, finding_data["severity"], finding_data["summary"])
            logger.info("Finding %s: %s", finding_id, finding_data["summary"])
        else:
            self.governor.defer_investigation()

    def _check_bgp_changes(self, bgp_data: dict[str, Any]) -> None:
        """Detect BGP session state changes."""
        for neighbor_ip, session in bgp_data.items():
            new_state = session["state"]
            old_state = self._last_bgp_states.get(neighbor_ip)

            if old_state is not None and old_state != new_state:
                finding_data = finding_from_bgp_change(
                    neighbor_ip,
                    old_state,
                    new_state,
                    description=session.get("description", ""),
                )
                event_id = self.events.record(
                    source="local",
                    category="bgp_change",
                    severity=finding_data["severity"],
                    raw_data={
                        "neighbor": neighbor_ip,
                        "old_state": old_state,
                        "new_state": new_state,
                    },
                )
                finding_id = self.findings.create(
                    severity=finding_data["severity"],
                    summary=finding_data["summary"],
                    detail=finding_data["detail"],
                    related_events=[event_id],
                )
                self.syslog.emit_finding(
                    finding_id, finding_data["severity"], finding_data["summary"]
                )

            self._last_bgp_states[neighbor_ip] = new_state

    def _check_link_changes(
        self, iface_state: dict[str, Any], lldp_data: dict[str, Any] | None
    ) -> None:
        """Detect link operational state changes."""
        for port_name, state in iface_state.items():
            new_oper = state["oper_status"]
            old_oper = self._last_oper_states.get(port_name)

            if old_oper is not None and old_oper != new_oper:
                neighbor = lldp_data.get(port_name) if lldp_data else None
                finding_data = finding_from_link_change(port_name, new_oper, neighbor)
                event_id = self.events.record(
                    source="local",
                    category="link_change",
                    severity=finding_data["severity"],
                    port=port_name,
                    raw_data={"old_state": old_oper, "new_state": new_oper},
                )
                finding_id = self.findings.create(
                    severity=finding_data["severity"],
                    summary=finding_data["summary"],
                    detail=finding_data["detail"],
                    related_events=[event_id],
                )
                self.syslog.emit_finding(
                    finding_id, finding_data["severity"], finding_data["summary"]
                )

            self._last_oper_states[port_name] = new_oper

    def _check_optic_health(
        self, optic_data: dict[str, Any], lldp_data: dict[str, Any] | None
    ) -> None:
        """Check for optic power degradation."""
        for port_name, optic in optic_data.items():
            rx_avg = optic.get("rx_power_avg_dbm", 0.0)
            # Flag if RX power drops below -7 dBm (typical threshold)
            if rx_avg < -7.0:
                neighbor = lldp_data.get(port_name) if lldp_data else None
                baseline = self.baselines.get(port_name, "rx_power_avg_dbm")
                baseline_rx = baseline["p50"] if baseline else None

                finding_data = finding_from_optic_degradation(
                    port_name,
                    rx_avg,
                    baseline_rx_power=baseline_rx,
                    neighbor_info=neighbor,
                )
                self.events.record(
                    source="local",
                    category="optic_degradation",
                    severity=finding_data["severity"],
                    port=port_name,
                    raw_data={"rx_power_avg_dbm": rx_avg},
                )
                finding_id = self.findings.create(
                    severity=finding_data["severity"],
                    summary=finding_data["summary"],
                    detail=finding_data["detail"],
                )
                self.syslog.emit_finding(
                    finding_id, finding_data["severity"], finding_data["summary"]
                )

    @staticmethod
    def _safe_collect(collect_fn: Callable[..., Any], **kwargs: Any) -> Any:
        """Call a collector, returning empty dict/list on error."""
        try:
            return collect_fn(**kwargs)
        except Exception:
            logger.exception("Collector error")
            return {}

    def _handle_signal(self, signum: int, frame: types.FrameType | None) -> None:
        logger.info("Received signal %d, shutting down...", signum)
        self._running = False


def main() -> None:
    """CLI entry point for running the daemon."""
    import sys

    config_path = None
    for i, arg in enumerate(sys.argv):
        if arg in ("--config", "-c") and i + 1 < len(sys.argv):
            config_path = sys.argv[i + 1]

    config = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    )

    # Create Redis readers for SONiC databases
    readers = {}
    try:
        readers = {
            RedisReader.APPL_DB: RedisReader(db=RedisReader.APPL_DB),
            RedisReader.COUNTERS_DB: RedisReader(db=RedisReader.COUNTERS_DB),
            RedisReader.STATE_DB: RedisReader(db=RedisReader.STATE_DB),
            RedisReader.CONFIG_DB: RedisReader(db=RedisReader.CONFIG_DB),
        }
    except Exception:
        logger.warning("Could not connect to Redis -- running in offline mode")

    daemon = WatchtowerDaemon(config, readers=readers)
    daemon.run()


if __name__ == "__main__":
    main()
