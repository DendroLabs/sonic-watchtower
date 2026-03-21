"""Configuration management for Watchtower."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ResourceConfig:
    max_cpu_percent: float = 20.0
    max_memory_mb: int = 768
    max_disk_mb: int = 200
    throttle_cpu_percent: float = 15.0
    throttle_memory_mb: int = 600
    system_cpu_ceiling: float = 80.0
    system_memory_ceiling: float = 85.0


@dataclass
class AnomalyConfig:
    deviation_threshold: float = 5.0
    immediate_threshold: float = 10.0
    baseline_warmup_hours: int = 168


@dataclass
class SyslogConfig:
    enabled: bool = True
    facility: str = "LOG_LOCAL4"
    min_severity: str = "warning"
    include_peer_findings: bool = False


@dataclass
class BannerConfig:
    enabled: bool = True
    max_lines: int = 20
    findings_file: str = "/var/run/watchtower/banner.txt"
    show_all_clear: bool = True


@dataclass
class TLSConfig:
    cert: str = "/etc/sonic/credentials/watchtower.crt"
    key: str = "/etc/sonic/credentials/watchtower.key"
    ca: str = "/etc/sonic/credentials/ca.crt"


@dataclass
class PeerConfig:
    enabled: bool = True
    port: int = 5950
    tls: TLSConfig = field(default_factory=TLSConfig)
    verify_hostname: bool = True
    heartbeat_interval: int = 10
    topology_share_interval: int = 60
    finding_ttl: int = 3


@dataclass
class HierarchyConfig:
    role: str = "auto"
    priority: int = 100


@dataclass
class LLMConfig:
    enabled: bool = False
    backend: str = "llama_cpp"
    model_path: str = "/opt/watchtower/models/qwen2-0.5b-q4_k_m.gguf"
    max_tool_calls: int = 10
    max_output_tokens: int = 2000
    max_concurrent_investigations: int = 5
    context_size: int = 4096


@dataclass
class JournalConfig:
    path: str = "/var/lib/watchtower/journal.db"
    retention_detail_days: int = 7
    retention_summary_days: int = 30
    max_size_mb: int = 100


@dataclass
class WatchtowerConfig:
    hostname: str = ""
    poll_interval: int = 30
    log_level: str = "info"
    resources: ResourceConfig = field(default_factory=ResourceConfig)
    anomaly: AnomalyConfig = field(default_factory=AnomalyConfig)
    syslog: SyslogConfig = field(default_factory=SyslogConfig)
    banner: BannerConfig = field(default_factory=BannerConfig)
    peer: PeerConfig = field(default_factory=PeerConfig)
    hierarchy: HierarchyConfig = field(default_factory=HierarchyConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    journal: JournalConfig = field(default_factory=JournalConfig)

    def __post_init__(self):
        if not self.hostname or self.hostname == "auto":
            self.hostname = socket.gethostname()


def _merge_dataclass(dc: Any, overrides: dict) -> None:
    """Recursively merge a dict of overrides into a dataclass instance."""
    for key, value in overrides.items():
        if not hasattr(dc, key):
            continue
        current = getattr(dc, key)
        if isinstance(value, dict) and hasattr(current, "__dataclass_fields__"):
            _merge_dataclass(current, value)
        else:
            setattr(dc, key, value)


def load_config(path: str | Path | None = None) -> WatchtowerConfig:
    """Load configuration from a YAML file, falling back to defaults."""
    config = WatchtowerConfig()
    if path is None:
        return config

    path = Path(path)
    if not path.exists():
        return config

    with open(path) as f:
        raw = yaml.safe_load(f)

    if raw and isinstance(raw, dict):
        _merge_dataclass(config, raw)

    return config
