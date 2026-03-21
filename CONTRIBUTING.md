# Contributing to Watchtower

## Development Setup

```bash
git clone https://github.com/DendroLabs/sonic-watchtower.git
cd sonic-watchtower
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
python -m pytest tests/ -v
```

All tests use [fakeredis](https://github.com/cunla/fakeredis-py) to simulate
SONiC Redis databases -- no real switch or Redis instance needed.

## Code Style

This project uses [ruff](https://docs.astral.sh/ruff/) for linting and
formatting, and [mypy](https://mypy-lang.org/) for type checking.

```bash
ruff check .              # lint
ruff format .             # format
mypy watchtower/          # type check
```

Pre-commit hooks are configured to run these automatically:

```bash
pip install pre-commit
pre-commit install
```

### Style conventions

- **Type annotations** on all function signatures (PEP 604 union syntax: `str | None`)
- **`from __future__ import annotations`** at the top of every file
- **Double quotes** for strings
- **Line length** up to 99 characters
- **snake_case** for functions and variables, **PascalCase** for classes

## Project Structure

```
watchtower/
  main.py              # Event loop and entry point
  config.py            # Configuration management (dataclasses)
  governor.py          # Resource governor (CPU/RAM self-policing)
  collectors/          # Read-only Redis data collectors
  analyzers/           # Baseline comparison and anomaly detection
  store/               # SQLite event journal, baselines, findings
  output/              # Syslog emitter and login banner writer
  llm/                 # LLM integration (Phase 3+), template fallback
  peer/                # gRPC peer protocol (Phase 2+)
  cli/                 # Click-based CLI commands
tests/
  mock_redis/          # JSON fixtures simulating SONiC Redis databases
  test_collectors.py   # Collector happy-path tests
  test_collector_robustness.py  # Edge cases and failure modes
  test_analyzers.py    # Analyzer and anomaly detection tests
  test_store.py        # SQLite journal and store tests
  test_governor.py     # Resource governor tests
  test_output.py       # Syslog and banner tests
  test_cli.py          # CLI command tests
  test_main.py         # Event loop tests
```

## How to Contribute

1. **Open an issue** describing the bug or feature before starting work
2. **Fork and branch** -- create a feature branch from `main`
3. **Write tests** -- all new code should have corresponding tests
4. **Run the full suite** -- `pytest`, `ruff check`, `mypy` must all pass
5. **Submit a PR** -- reference the issue, describe what changed and why

## Design Principles

Before making changes, please read [WATCHTOWER_DESIGN.md](WATCHTOWER_DESIGN.md).
The most important invariant:

> **Watchtower NEVER writes to CONFIG_DB, APPL_DB, ASIC_DB, or STATE_DB.**
>
> Its only outputs are: the login banner, syslog messages, and peer gossip.

Any PR that violates this invariant will be rejected.
