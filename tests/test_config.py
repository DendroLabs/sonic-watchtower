"""Tests for Watchtower configuration."""

import socket

import yaml
import pytest

from watchtower.config import WatchtowerConfig, load_config


class TestWatchtowerConfig:
    def test_defaults(self):
        config = WatchtowerConfig()
        assert config.hostname == socket.gethostname()
        assert config.poll_interval == 30
        assert config.log_level == "info"

    def test_hostname_auto(self):
        config = WatchtowerConfig(hostname="auto")
        assert config.hostname == socket.gethostname()

    def test_hostname_explicit(self):
        config = WatchtowerConfig(hostname="switch-a")
        assert config.hostname == "switch-a"

    def test_resource_defaults(self):
        config = WatchtowerConfig()
        assert config.resources.max_cpu_percent == 20.0
        assert config.resources.max_memory_mb == 768
        assert config.resources.system_cpu_ceiling == 80.0

    def test_anomaly_defaults(self):
        config = WatchtowerConfig()
        assert config.anomaly.deviation_threshold == 5.0
        assert config.anomaly.baseline_warmup_hours == 168

    def test_journal_defaults(self):
        config = WatchtowerConfig()
        assert config.journal.retention_detail_days == 7
        assert config.journal.retention_summary_days == 30


class TestLoadConfig:
    def test_load_nonexistent_returns_defaults(self, tmp_path):
        config = load_config(tmp_path / "nonexistent.yml")
        assert config.poll_interval == 30

    def test_load_none_returns_defaults(self):
        config = load_config(None)
        assert config.poll_interval == 30

    def test_load_partial_override(self, tmp_path):
        cfg_file = tmp_path / "watchtower.yml"
        cfg_file.write_text(yaml.dump({
            "poll_interval": 60,
            "resources": {"max_cpu_percent": 10},
        }))
        config = load_config(cfg_file)
        assert config.poll_interval == 60
        assert config.resources.max_cpu_percent == 10.0
        # Non-overridden values keep defaults
        assert config.resources.max_memory_mb == 768
        assert config.log_level == "info"

    def test_load_nested_override(self, tmp_path):
        cfg_file = tmp_path / "watchtower.yml"
        cfg_file.write_text(yaml.dump({
            "syslog": {"min_severity": "critical", "enabled": False},
        }))
        config = load_config(cfg_file)
        assert config.syslog.min_severity == "critical"
        assert config.syslog.enabled is False
        assert config.syslog.facility == "LOG_LOCAL4"

    def test_load_ignores_unknown_keys(self, tmp_path):
        cfg_file = tmp_path / "watchtower.yml"
        cfg_file.write_text(yaml.dump({
            "unknown_key": "value",
            "poll_interval": 45,
        }))
        config = load_config(cfg_file)
        assert config.poll_interval == 45
        assert not hasattr(config, "unknown_key")

    def test_load_empty_file(self, tmp_path):
        cfg_file = tmp_path / "watchtower.yml"
        cfg_file.write_text("")
        config = load_config(cfg_file)
        assert config.poll_interval == 30

    def test_load_full_example(self):
        """Ensure the example config file parses without error."""
        from pathlib import Path
        example = Path(__file__).parent.parent / "watchtower.yml.example"
        if example.exists():
            config = load_config(example)
            assert config.poll_interval == 30
            assert config.resources.max_cpu_percent == 20
