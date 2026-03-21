"""Log filter collector -- reads and filters recent syslog entries.

In production, this reads from /var/log/syslog on the SONiC host.
For development/testing, it can read from a provided log file path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from watchtower.collectors.base import BaseCollector

# Noise patterns to filter out (common high-frequency, low-value messages)
_NOISE_PATTERNS = [
    re.compile(r"systemd\[1\]: Started Session"),
    re.compile(r"CRON\["),
    re.compile(r"pam_unix.*session (opened|closed)"),
    re.compile(r"rsyslogd.*action.*resumed"),
    re.compile(r"supervisord.*INFO"),
]

# Interesting patterns for network events
_INTERESTING_PATTERNS = [
    re.compile(r"bgp", re.IGNORECASE),
    re.compile(r"orchagent", re.IGNORECASE),
    re.compile(r"syncd", re.IGNORECASE),
    re.compile(r"swss", re.IGNORECASE),
    re.compile(r"ERR|CRIT|WARN|EMERG|ALERT", re.IGNORECASE),
    re.compile(r"link (up|down)", re.IGNORECASE),
    re.compile(r"neighbor", re.IGNORECASE),
]


class LogFilterCollector(BaseCollector):
    """Filters syslog entries for network-relevant events."""

    def __init__(self, log_path: str = "/var/log/syslog", **kwargs):
        super().__init__(**kwargs)
        self._log_path = log_path

    def collect(self, tail_lines: int = 200) -> list[dict]:
        """Collect and filter recent log entries.

        Args:
            tail_lines: Number of lines to read from the end of the log.

        Returns:
            List of filtered log entries as dicts with 'line' and 'interesting' keys.
        """
        log_path = Path(self._log_path)
        if not log_path.exists():
            return []

        lines = self._tail(log_path, tail_lines)
        results = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if self._is_noise(line):
                continue

            results.append({
                "line": line,
                "interesting": self._is_interesting(line),
            })

        return results

    @staticmethod
    def _tail(path: Path, n: int) -> list[str]:
        """Read last N lines from a file."""
        try:
            with open(path) as f:
                all_lines = f.readlines()
            return all_lines[-n:]
        except (OSError, PermissionError):
            return []

    @staticmethod
    def _is_noise(line: str) -> bool:
        return any(p.search(line) for p in _NOISE_PATTERNS)

    @staticmethod
    def _is_interesting(line: str) -> bool:
        return any(p.search(line) for p in _INTERESTING_PATTERNS)
