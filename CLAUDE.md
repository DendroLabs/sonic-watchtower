# Watchtower

Distributed, read-only network observability system for SONiC switches.

## Project Overview

Watchtower runs as a Docker container on each SONiC switch, monitors local state via Redis (read-only), communicates with peer instances on neighboring switches via gRPC/mTLS, and uses a small local LLM to orchestrate investigation scripts and produce human-readable findings.

**Core invariant:** Watchtower NEVER writes to CONFIG_DB, APPL_DB, ASIC_DB, or STATE_DB. Its only outputs are: login banner, syslog messages, and peer gossip.

## Design Document

Full design spec: `WATCHTOWER_DESIGN.md`
FBOSS architecture reference: `FBOSS_ARCHITECTURE.md`
FBOSS source: `fboss-repo/`

## Architecture

- **Collectors** — Python scripts that read Redis DBs and return structured JSON (port_stats, bgp_state, lldp_topology, interface_state, optic_health, etc.)
- **Analyzers** — Process collected data (baseline_compare, path_trace, impact_assess, peer_correlate, topology_diff, etc.)
- **Event Journal** — SQLite database (baselines, events, topology, findings, peer_state tables)
- **Resource Governor** — Self-policing CPU/RAM monitor with graduated throttle/pause/dormant states
- **Peer Protocol** — gRPC/mTLS on port 5950, auto-discovered via LLDP, TTL-limited gossip
- **Syslog Emitter** — Findings emitted as syslog (tag: watchtower, facility: LOG_LOCAL4)
- **Login Banner** — `/etc/profile.d/watchtower.sh` reads `/var/run/watchtower/banner.txt`
- **LLM (Phase 3+)** — Local quantized model via llama-cpp-python, orchestrates tool calls
- **CLI** — `watchtower show|simulate|ask` commands

## Implementation Phases

1. **Foundation** — Local observer, no LLM, no peers. Docker container, collectors, baseline engine, event journal, anomaly detection (template findings), resource governor, syslog, banner, CLI, mock Redis test harness.
2. **Peer Protocol** — gRPC/protobuf, mTLS, LLDP-based peer discovery, cross-switch correlation.
3. **LLM Integration** — llama-cpp-python, tool-use wrapper, natural language findings, `watchtower ask`.
4. **Pre-Flight Simulation** — path_trace, impact_assess, change_simulate, `watchtower simulate`.
5. **Central Watchtower** — Hierarchy election, upward reporting, fleet-wide aggregation, REST API.
6. **Polish** — Prometheus, long-term baselines, SONiC build integration, docs.

## Project Structure (target)

```
sonic-watchtower/
  Dockerfile                    # python:3.11-slim (Phase 1-2)
  docker-compose.yml
  watchtower.yml.example
  watchtower/
    main.py                     # entry point, event loop
    config.py                   # configuration management
    governor.py                 # resource governor
    collectors/                 # one script per data source
    analyzers/                  # one script per analysis type
    llm/                        # LLM engine, tools, prompts, fallback
    peer/                       # gRPC protocol, discovery, hierarchy
    store/                      # SQLite journal, baselines, topology, findings
    output/                     # banner, syslog, prometheus
    cli/                        # click-based CLI
  proto/watchtower.proto        # gRPC service definition
  scripts/watchtower.sh         # login banner script
  tests/
    mock_redis/                 # fake Redis data (JSON fixtures)
    test_collectors/
    test_analyzers/
    test_store/
    test_output/
    test_governor/
    test_cli/
```

## Tech Stack

- **Language:** Python 3.11
- **Container:** python:3.11-slim-bookworm (Phase 1-2), SONiC-native base (Phase 3+)
- **Redis client:** redis-py (direct key access, no swsscommon dependency in Phase 1-2)
- **Database:** SQLite (event journal)
- **Peer protocol:** gRPC + protobuf, mTLS (SONiC gNMI cert infrastructure)
- **LLM (Phase 3+):** llama-cpp-python with quantized model (Qwen2-0.5B or similar)
- **CLI:** click
- **Testing:** pytest with mock Redis fixtures

## Key Configuration (watchtower.yml)

- `poll_interval: 30` — seconds between collection cycles
- `resources.max_cpu_percent: 20` / `max_memory_mb: 768`
- `anomaly.deviation_threshold: 5` / `immediate_threshold: 10`
- `syslog.min_severity: warning`
- `peer.port: 5950` / `peer.finding_ttl: 3`
- `journal.retention_detail_days: 7` / `retention_summary_days: 30`

## Current Status

**Phase 1 is complete.** 132 tests passing across 8 test files.

| Component | Status | Tests |
|-----------|--------|-------|
| Config (`config.py`) | Done | 13 |
| Store (journal, baselines, topology, findings, events) | Done | 31 |
| Collectors (port_stats, interface_state, lldp_topology, bgp_state, optic_health, log_filter) | Done | 19 |
| Analyzers (baseline_compare) + template findings (fallback.py) | Done | 17 |
| Resource Governor | Done | 18 |
| Output (syslog_emitter, banner) | Done | 12 |
| CLI (`watchtower show findings/topology/events/resources/baselines`) | Done | 12 |
| Main event loop + Dockerfile + docker-compose.yml | Done | 10 |

## Next Steps: Community Readiness (pre-Phase 2)

Before sharing or starting Phase 2, these items need to be done:

### 1. Repo Setup (do first)
- `git init` and initial commit
- `.gitignore` (Python, venv, __pycache__, .db files, .pyc)
- `LICENSE` — Apache 2.0 (standard for SONiC ecosystem)
- `README.md` — Short, compelling: what it is, why it matters, quickstart, architecture diagram, demo output
- `CONTRIBUTING.md` — How to contribute, dev setup, test instructions

### 2. Code Quality Tooling
- Add `ruff` for linting (add to pyproject.toml `[tool.ruff]`)
- Add `mypy` for type checking (add to pyproject.toml `[tool.mypy]`)
- Run both and fix all issues
- Add `pre-commit` config (`.pre-commit-config.yaml`) with ruff + mypy hooks

### 3. CI Pipeline
- `.github/workflows/ci.yml` — GitHub Actions running:
  - `ruff check .`
  - `mypy watchtower/`
  - `pytest tests/ -v`
  - Matrix: Python 3.11, 3.12, 3.13
- Badge in README for CI status

### 4. Robustness Testing (critical)
- Collectors must handle gracefully:
  - Missing Redis keys (key exists but hash fields are absent)
  - Empty hashes (key exists, no fields)
  - Malformed data (non-numeric values in counter fields)
  - Redis connection failures
- Add `tests/test_collector_robustness.py` with these edge cases

### 5. SONiC VS Validation (most important for credibility)
- Pull the SONiC VS (virtual switch) Docker image
- Start it and inspect actual Redis key patterns:
  - `redis-cli -n 0 KEYS '*'` (APPL_DB)
  - `redis-cli -n 2 KEYS '*'` (COUNTERS_DB)
  - `redis-cli -n 6 KEYS '*'` (STATE_DB)
- Compare against our mock fixtures
- Fix any key pattern mismatches
- Document actual key patterns discovered in `docs/sonic_redis_keys.md`
- Create a SONiC VS integration test that runs against the real image

### Priority Order
1. Repo setup (git, LICENSE, README) — 30 min
2. Code quality (ruff, mypy) — 1 hour
3. Robustness tests — 1 hour
4. CI pipeline — 30 min
5. SONiC VS validation — 2-3 hours (depends on image setup)

## Development Notes

- SONiC Redis DBs are standard Redis — redis-py with correct key patterns works without swsscommon
- Baseline computation and anomaly detection happen in Python, NOT in the LLM
- Resource governor must throttle before Docker cgroup limits intervene
- Peer findings propagate via TTL-limited gossip (max 3 hops), not flooding
- If certs are missing, peer protocol disables gracefully (local-only mode)
- If LLM is unavailable, template-based findings are used as fallback
- Python 3.14 is on this machine; venv is at `.venv/` — activate with `source .venv/bin/activate`
- Run tests: `python -m pytest tests/ -v`
- Run CLI: `watchtower show findings`
