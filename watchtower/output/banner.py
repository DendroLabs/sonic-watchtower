"""Login banner writer -- formats and writes active findings to the banner file."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from watchtower.config import BannerConfig


class BannerWriter:
    """Writes the login banner file with current active findings."""

    def __init__(self, config: BannerConfig | None = None):
        self._config = config or BannerConfig()

    def write(self, findings: list[dict]):
        """Write the banner file with the given active findings.

        Args:
            findings: List of finding dicts with 'severity' and 'summary' keys,
                      pre-sorted by severity (critical first).
        """
        if not self._config.enabled:
            return

        banner_path = Path(self._config.findings_file)
        banner_path.parent.mkdir(parents=True, exist_ok=True)

        content = self._format(findings)
        banner_path.write_text(content)

    def _format(self, findings: list[dict]) -> str:
        """Format findings into the banner text."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        if not findings:
            if self._config.show_all_clear:
                return f"=== WATCHTOWER: No active findings. Last checked {now} ===\n"
            return ""

        # Count by severity
        counts = {"critical": 0, "warning": 0, "info": 0}
        for f in findings:
            sev = f.get("severity", "info")
            counts[sev] = counts.get(sev, 0) + 1
        total = sum(counts.values())

        # Header
        severity_parts = []
        for sev in ("critical", "warning", "info"):
            if counts[sev] > 0:
                severity_parts.append(f"{counts[sev]} {sev.upper()}")
        severity_str = ", ".join(severity_parts)

        lines = [
            "=" * 56,
            f" WATCHTOWER: {total} active finding{'s' if total != 1 else ''}  [{severity_str}]",
            f" Last updated: {now}",
            " Details: watchtower show findings",
            "-" * 56,
        ]

        # Findings (respect max_lines, reserving 7 lines for header/footer/overflow)
        max_finding_lines = self._config.max_lines - 7
        used_lines = 0
        shown = 0

        for f in findings:
            if used_lines >= max_finding_lines:
                remaining = total - shown
                if remaining > 0:
                    lines.append(f" ... and {remaining} more finding{'s' if remaining != 1 else ''}")
                break

            sev = f.get("severity", "info").upper()
            summary = f.get("summary", "")

            # Wrap summary to ~52 chars per line, max 3 lines per finding
            finding_lines = self._wrap_finding(sev, summary, width=52, max_lines=3)
            if used_lines + len(finding_lines) + 1 > max_finding_lines:
                remaining = total - shown
                if remaining > 0:
                    lines.append(f" ... and {remaining} more finding{'s' if remaining != 1 else ''}")
                break

            lines.append("")  # blank line before finding
            lines.extend(finding_lines)
            used_lines += len(finding_lines) + 1
            shown += 1

        lines.append("=" * 56)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _wrap_finding(severity: str, summary: str, width: int = 52, max_lines: int = 3) -> list[str]:
        """Wrap a finding into indented lines for the banner."""
        prefix = f" [{severity}] "
        indent = "   "
        first_width = width - len(prefix)
        rest_width = width - len(indent)

        words = summary.split()
        lines = []
        current = ""

        for word in words:
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= (first_width if not lines else rest_width):
                current += " " + word
            else:
                if not lines:
                    lines.append(prefix + current)
                else:
                    lines.append(indent + current)
                current = word
                if len(lines) >= max_lines:
                    break

        if current and len(lines) < max_lines:
            if not lines:
                lines.append(prefix + current)
            else:
                lines.append(indent + current)

        return lines
