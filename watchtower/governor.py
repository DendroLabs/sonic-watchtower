"""Resource governor -- self-policing CPU/RAM monitor with graduated throttle states."""

from __future__ import annotations

import enum
import os
import time
from dataclasses import dataclass, field

from watchtower.config import ResourceConfig


class GovernorState(enum.Enum):
    FULL = "full"  # normal operation
    THROTTLED = "throttled"  # reduced frequency, limited investigations
    PAUSED = "paused"  # LLM paused, minimum collectors only
    DORMANT = "dormant"  # fully dormant, heartbeat only


@dataclass
class ResourceSnapshot:
    """Current resource usage snapshot."""

    watchtower_cpu_percent: float = 0.0
    watchtower_memory_mb: float = 0.0
    system_cpu_percent: float = 0.0
    system_memory_percent: float = 0.0
    timestamp: float = field(default_factory=time.time)


class ResourceGovernor:
    """Monitors resource usage and determines operational state.

    The governor samples system resources and determines whether Watchtower
    should be running at full capacity, throttled, paused, or dormant.
    """

    def __init__(self, config: ResourceConfig | None = None):
        self._config = config or ResourceConfig()
        self._state = GovernorState.FULL
        self._last_snapshot = ResourceSnapshot()
        self._poll_interval_multiplier = 1
        self._investigations_deferred = 0
        self._total_state_changes = 0

    @property
    def state(self) -> GovernorState:
        return self._state

    @property
    def poll_interval_multiplier(self) -> int:
        """Multiplier for the base poll interval based on current state."""
        return self._poll_interval_multiplier

    @property
    def investigations_deferred(self) -> int:
        return self._investigations_deferred

    def can_investigate(self) -> bool:
        """Whether the governor allows running an investigation."""
        return self._state in (GovernorState.FULL, GovernorState.THROTTLED)

    def can_run_llm(self) -> bool:
        """Whether the governor allows LLM inference."""
        return self._state == GovernorState.FULL

    def should_collect(self) -> bool:
        """Whether the governor allows running collectors."""
        return self._state != GovernorState.DORMANT

    def defer_investigation(self) -> None:
        """Record that an investigation was deferred due to resource pressure."""
        self._investigations_deferred += 1

    def update(self, snapshot: ResourceSnapshot | None = None) -> None:
        """Update governor state based on current resource usage.

        If no snapshot is provided, reads from /proc (Linux) or
        uses psutil-free fallback.
        """
        if snapshot is None:
            snapshot = self._read_resources()

        self._last_snapshot = snapshot
        old_state = self._state
        self._state = self._compute_state(snapshot)

        if self._state != old_state:
            self._total_state_changes += 1

        # Adjust poll interval multiplier
        if self._state == GovernorState.FULL:
            self._poll_interval_multiplier = 1
        elif self._state == GovernorState.THROTTLED:
            self._poll_interval_multiplier = 2
        elif self._state == GovernorState.PAUSED:
            self._poll_interval_multiplier = 4
        else:  # DORMANT
            self._poll_interval_multiplier = 8

    def _compute_state(self, snapshot: ResourceSnapshot) -> GovernorState:
        """Determine governor state from resource snapshot."""
        cfg = self._config

        # System crisis -> dormant
        if snapshot.system_cpu_percent > 95 or snapshot.system_memory_percent > 95:
            return GovernorState.DORMANT

        # System under pressure OR watchtower at hard limits -> paused
        if (
            snapshot.system_cpu_percent > cfg.system_cpu_ceiling
            or snapshot.system_memory_percent > cfg.system_memory_ceiling
            or snapshot.watchtower_cpu_percent > cfg.max_cpu_percent
            or snapshot.watchtower_memory_mb > cfg.max_memory_mb
        ):
            return GovernorState.PAUSED

        # Watchtower approaching soft limits -> throttled
        if (
            snapshot.watchtower_cpu_percent > cfg.throttle_cpu_percent
            or snapshot.watchtower_memory_mb > cfg.throttle_memory_mb
        ):
            return GovernorState.THROTTLED

        return GovernorState.FULL

    def _read_resources(self) -> ResourceSnapshot:
        """Read current resource usage from the system.

        Uses /proc on Linux, falls back to basic os calls elsewhere.
        """
        snapshot = ResourceSnapshot()

        try:
            # Process memory via /proc/self/status (Linux)
            if os.path.exists("/proc/self/status"):
                with open("/proc/self/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            kb = int(line.split()[1])
                            snapshot.watchtower_memory_mb = kb / 1024.0
                            break

            # System memory via /proc/meminfo (Linux)
            if os.path.exists("/proc/meminfo"):
                meminfo = {}
                with open("/proc/meminfo") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 2:
                            meminfo[parts[0].rstrip(":")] = int(parts[1])
                total = meminfo.get("MemTotal", 1)
                available = meminfo.get("MemAvailable", total)
                snapshot.system_memory_percent = (1 - available / total) * 100

            # System CPU via /proc/stat (Linux) -- simplified single-sample
            if os.path.exists("/proc/stat"):
                with open("/proc/stat") as f:
                    line = f.readline()
                parts = line.split()
                if len(parts) >= 5:
                    idle = int(parts[4])
                    total = sum(int(p) for p in parts[1:])
                    if total > 0:
                        snapshot.system_cpu_percent = (1 - idle / total) * 100

        except (OSError, ValueError, IndexError):
            pass

        return snapshot

    def get_status(self) -> dict:
        """Get current governor status for display/metrics."""
        return {
            "state": self._state.value,
            "watchtower_cpu_percent": self._last_snapshot.watchtower_cpu_percent,
            "watchtower_memory_mb": self._last_snapshot.watchtower_memory_mb,
            "system_cpu_percent": self._last_snapshot.system_cpu_percent,
            "system_memory_percent": self._last_snapshot.system_memory_percent,
            "poll_interval_multiplier": self._poll_interval_multiplier,
            "investigations_deferred": self._investigations_deferred,
            "state_changes": self._total_state_changes,
        }
