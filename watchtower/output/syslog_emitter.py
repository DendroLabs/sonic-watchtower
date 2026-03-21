"""Syslog emitter -- writes findings to host syslog."""

from __future__ import annotations

import syslog as _syslog

from watchtower.config import SyslogConfig

# Map config facility strings to syslog constants
_FACILITY_MAP = {
    "LOG_LOCAL0": _syslog.LOG_LOCAL0,
    "LOG_LOCAL1": _syslog.LOG_LOCAL1,
    "LOG_LOCAL2": _syslog.LOG_LOCAL2,
    "LOG_LOCAL3": _syslog.LOG_LOCAL3,
    "LOG_LOCAL4": _syslog.LOG_LOCAL4,
    "LOG_LOCAL5": _syslog.LOG_LOCAL5,
    "LOG_LOCAL6": _syslog.LOG_LOCAL6,
    "LOG_LOCAL7": _syslog.LOG_LOCAL7,
    "LOG_USER": _syslog.LOG_USER,
}

# Map finding severity to syslog priority
_SEVERITY_MAP = {
    "critical": _syslog.LOG_CRIT,
    "warning": _syslog.LOG_WARNING,
    "info": _syslog.LOG_INFO,
    "debug": _syslog.LOG_DEBUG,
}

# Minimum severity ordering for filtering
_SEVERITY_ORDER = {"debug": 0, "info": 1, "warning": 2, "critical": 3}


class SyslogEmitter:
    """Emits findings and status messages to syslog."""

    def __init__(self, config: SyslogConfig | None = None, dry_run: bool = False):
        self._config = config or SyslogConfig()
        self._dry_run = dry_run
        self._emitted: list[dict] = []  # for testing in dry_run mode

        if not dry_run:
            facility = _FACILITY_MAP.get(self._config.facility, _syslog.LOG_LOCAL4)
            _syslog.openlog("watchtower", _syslog.LOG_PID, facility)

    def emit_finding(self, finding_id: str, severity: str, summary: str,
                     is_peer: bool = False):
        """Emit a finding to syslog if it meets the minimum severity threshold."""
        if not self._config.enabled:
            return

        if is_peer and not self._config.include_peer_findings:
            return

        min_level = _SEVERITY_ORDER.get(self._config.min_severity, 2)
        msg_level = _SEVERITY_ORDER.get(severity, 1)
        if msg_level < min_level:
            return

        priority = _SEVERITY_MAP.get(severity, _syslog.LOG_INFO)
        tag = severity.upper()
        message = f"[{tag}] {summary} finding_id={finding_id}"

        if self._dry_run:
            self._emitted.append({
                "priority": priority,
                "severity": severity,
                "message": message,
                "finding_id": finding_id,
            })
        else:
            _syslog.syslog(priority, message)

    def emit_status(self, message: str):
        """Emit a status/debug message (e.g., governor state changes)."""
        if not self._config.enabled:
            return

        if self._dry_run:
            self._emitted.append({
                "priority": _syslog.LOG_DEBUG,
                "severity": "debug",
                "message": message,
            })
        else:
            _syslog.syslog(_syslog.LOG_DEBUG, message)

    @property
    def emitted(self) -> list[dict]:
        """Messages emitted (only available in dry_run mode)."""
        return self._emitted
