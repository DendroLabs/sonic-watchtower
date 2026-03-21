"""Tests for syslog emitter and login banner."""

import pytest

from watchtower.output.syslog_emitter import SyslogEmitter
from watchtower.output.banner import BannerWriter
from watchtower.config import SyslogConfig, BannerConfig


# --- SyslogEmitter ---

class TestSyslogEmitter:
    def test_emit_finding_dry_run(self):
        emitter = SyslogEmitter(dry_run=True)
        emitter.emit_finding("f-001", "critical", "Test critical finding")
        assert len(emitter.emitted) == 1
        assert "CRITICAL" in emitter.emitted[0]["message"]
        assert "f-001" in emitter.emitted[0]["message"]

    def test_respects_min_severity(self):
        config = SyslogConfig(min_severity="warning")
        emitter = SyslogEmitter(config=config, dry_run=True)

        emitter.emit_finding("f-001", "info", "Info finding")
        emitter.emit_finding("f-002", "warning", "Warning finding")
        emitter.emit_finding("f-003", "critical", "Critical finding")

        assert len(emitter.emitted) == 2
        severities = {e["severity"] for e in emitter.emitted}
        assert "info" not in severities
        assert "warning" in severities
        assert "critical" in severities

    def test_filters_peer_findings(self):
        config = SyslogConfig(include_peer_findings=False)
        emitter = SyslogEmitter(config=config, dry_run=True)

        emitter.emit_finding("f-001", "critical", "Local finding", is_peer=False)
        emitter.emit_finding("f-002", "critical", "Peer finding", is_peer=True)

        assert len(emitter.emitted) == 1
        assert "Local" in emitter.emitted[0]["message"]

    def test_disabled(self):
        config = SyslogConfig(enabled=False)
        emitter = SyslogEmitter(config=config, dry_run=True)
        emitter.emit_finding("f-001", "critical", "Should not emit")
        assert len(emitter.emitted) == 0

    def test_emit_status(self):
        emitter = SyslogEmitter(dry_run=True)
        emitter.emit_status("governor paused LLM inference")
        assert len(emitter.emitted) == 1
        assert "governor" in emitter.emitted[0]["message"]


# --- BannerWriter ---

class TestBannerWriter:
    def test_write_all_clear(self, tmp_path):
        banner_file = tmp_path / "banner.txt"
        config = BannerConfig(findings_file=str(banner_file), show_all_clear=True)
        writer = BannerWriter(config)
        writer.write([])

        content = banner_file.read_text()
        assert "No active findings" in content
        assert "WATCHTOWER" in content

    def test_write_all_clear_hidden(self, tmp_path):
        banner_file = tmp_path / "banner.txt"
        config = BannerConfig(findings_file=str(banner_file), show_all_clear=False)
        writer = BannerWriter(config)
        writer.write([])

        content = banner_file.read_text()
        assert content == ""

    def test_write_single_finding(self, tmp_path):
        banner_file = tmp_path / "banner.txt"
        config = BannerConfig(findings_file=str(banner_file))
        writer = BannerWriter(config)

        writer.write([{
            "severity": "critical",
            "summary": "Optic degradation on Ethernet48 (link to spine-3).",
        }])

        content = banner_file.read_text()
        assert "1 active finding" in content
        assert "1 CRITICAL" in content
        assert "CRITICAL" in content
        assert "Ethernet48" in content
        assert "watchtower show findings" in content

    def test_write_multiple_findings(self, tmp_path):
        banner_file = tmp_path / "banner.txt"
        config = BannerConfig(findings_file=str(banner_file))
        writer = BannerWriter(config)

        writer.write([
            {"severity": "critical", "summary": "Critical issue on Ethernet48."},
            {"severity": "warning", "summary": "BGP session flapping."},
            {"severity": "info", "summary": "New LLDP neighbor detected."},
        ])

        content = banner_file.read_text()
        assert "3 active findings" in content
        assert "1 CRITICAL" in content
        assert "1 WARNING" in content
        assert "1 INFO" in content

    def test_max_lines_truncation(self, tmp_path):
        banner_file = tmp_path / "banner.txt"
        config = BannerConfig(findings_file=str(banner_file), max_lines=10)
        writer = BannerWriter(config)

        # Create many findings
        findings = [
            {"severity": "warning", "summary": f"Warning issue number {i}."}
            for i in range(20)
        ]
        writer.write(findings)

        content = banner_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) <= 10
        assert "more" in content

    def test_disabled(self, tmp_path):
        banner_file = tmp_path / "banner.txt"
        config = BannerConfig(findings_file=str(banner_file), enabled=False)
        writer = BannerWriter(config)
        writer.write([{"severity": "critical", "summary": "test"}])
        assert not banner_file.exists()

    def test_creates_parent_directories(self, tmp_path):
        banner_file = tmp_path / "deep" / "nested" / "banner.txt"
        config = BannerConfig(findings_file=str(banner_file))
        writer = BannerWriter(config)
        writer.write([])
        assert banner_file.exists()
