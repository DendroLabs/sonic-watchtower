# Watchtower

**Distributed, read-only network observer for SONiC switches.**

[![CI](https://github.com/DendroLabs/sonic-watchtower/actions/workflows/ci.yml/badge.svg)](https://github.com/DendroLabs/sonic-watchtower/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

Watchtower runs as a Docker container on each SONiC switch. It reads local
state from Redis, detects anomalies against learned baselines, communicates
with peer Watchtower instances on neighboring switches via gRPC/mTLS, and
produces human-readable findings via syslog and the SSH login banner. It
**never writes to CONFIG_DB, APPL_DB, ASIC_DB, or STATE_DB** -- its only
outputs are observations and peer gossip.

## Why Watchtower?

Network operators don't know something is wrong until they SSH into a switch
and start poking around. Watchtower watches for them -- continuously monitoring
port counters, BGP sessions, optic health, and LLDP topology. When something
deviates from the baseline, operators see it immediately:

- **On login** -- findings appear in the SSH banner before you type a command
- **In syslog** -- findings flow to your existing SIEM/NOC pipeline
- **On demand** -- `watchtower show findings` from the CLI

## Key Properties

- **Read-only** -- never modifies switch configuration or forwarding state
- **Cannot cause an outage** -- crash it, restart it, ignore it; the network is fine
- **Self-policing** -- built-in resource governor throttles before hitting cgroup limits
- **Distributed** -- peers share events and findings via gRPC/mTLS, auto-discovered via LLDP
- **Incrementally useful** -- value on a single switch, more with peer correlation

## Architecture

```
+------------------------------------------------------------------+
|  SONiC Host                                                      |
|                                                                  |
|  +--------------------+  +------------------+  +--------------+  |
|  | bgp / orchagent /  |  | CONFIG_DB        |  | /etc/        |  |
|  | syncd / lldp / ... |  | APPL_DB          |  | profile.d/   |  |
|  |                    |  | STATE_DB         |  | watchtower.sh|  |
|  |  (SONiC services)  |  | COUNTERS_DB      |  +------+-------+  |
|  +--------------------+  +--------+---------+         ^ banner   |
|                                   | read-only         |          |
|                                   v                   |          |
|  +------------------------------------------------------------+  |
|  |  Watchtower Container                                      |  |
|  |                                                            |  |
|  |  Collectors -----> Analyzers -----> Findings               |  |
|  |  (Redis read)      (baselines,      (syslog + banner)      |  |
|  |                     peer correlate,                         |  |
|  |                     topology diff)                          |  |
|  |                                                            |  |
|  |  Event Journal (SQLite)    Resource Governor               |  |
|  |                                                            |  |
|  |  Peer Protocol (gRPC/mTLS, port 5950)                      |  |
|  |  - LLDP-based auto-discovery                                |  |
|  |  - Heartbeats, event sharing, finding gossip (TTL=3)        |  |
|  +------------------------------------------------------------+  |
|         |               |                |                       |
|         | gRPC/mTLS     | gRPC/mTLS      | gRPC/mTLS             |
|         v               v                v                       |
|  [Peer Watchtower] [Peer Watchtower]  [Peer Watchtower]          |
+------------------------------------------------------------------+
```

**Collectors** read from SONiC Redis databases every 30 seconds:
port counters, interface state, BGP sessions, LLDP neighbors, optic DOM data,
and filtered syslog entries.

**Analyzers** compare collected data against learned baselines, flag
statistical anomalies (p95/p99 deviation thresholds), correlate local events
with peer events on the same link, and detect topology changes.

**Peer Protocol** exchanges heartbeats, events, topology fragments, and
findings between Watchtower instances on neighboring switches. Peers are
auto-discovered from LLDP data. Findings propagate via TTL-limited gossip
(max 3 hops). mTLS is mandatory; if certificates are missing, the peer
protocol disables gracefully (local-only mode).

**Findings** are emitted to syslog (`tag: watchtower`, `facility: LOG_LOCAL4`)
and written to the login banner file.

## Quick Start

### On a SONiC switch (Docker)

```bash
# Copy and edit the configuration
cp watchtower.yml.example watchtower.yml

# Start Watchtower
docker compose up -d

# Check findings
docker exec watchtower watchtower show findings
```

### Local development

```bash
git clone https://github.com/DendroLabs/sonic-watchtower.git
cd sonic-watchtower
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v    # 239 tests
```

## Demo Output

**Login banner** (displayed on SSH login):

```
========================================================
 WATCHTOWER: 2 active findings  [1 WARNING, 1 INFO]
 Last updated: 2024-11-15 14:32:07 UTC
 Details: watchtower show findings
--------------------------------------------------------

 [WARNING] Ethernet48 rx_errors: 1847 (baseline p99: 12,
   deviation: 153.9x) -- possible optic degradation

 [INFO] BGP neighbor 10.0.0.3 session state: Idle
   (previously Established)
========================================================
```

**CLI** (`watchtower show findings`):

```
Active findings: 2 (0 critical, 1 warning, 1 info)

  [WARNING ] Ethernet48 rx_errors: 1847 (baseline p99: 12, deviation: 153.9x)
             ID: f-20241115-a3b2  Time: 2024-11-15T14:32:07Z
             Possible optic degradation on Ethernet48

  [INFO    ] BGP neighbor 10.0.0.3 state changed to Idle
             ID: f-20241115-c7d1  Time: 2024-11-15T14:32:07Z
```

**Syslog**:

```
Nov 15 14:32:07 switch01 watchtower[1234]: [WARNING] Ethernet48 rx_errors: 1847 (baseline p99: 12, deviation: 153.9x) finding_id=f-20241115-a3b2
```

## Project Status

**Phase 1 (Foundation) and Phase 2 (Peer Protocol) are complete.** 239 tests
passing. Watchtower runs as a distributed observer with anomaly detection,
baseline learning, peer-to-peer communication, cross-switch event correlation,
a resource governor, syslog output, login banner, and a CLI.

| Component | Status |
|-----------|--------|
| Collectors (port stats, interface, LLDP, BGP, optics, logs) | Done |
| Analyzers (baseline compare, peer correlate, topology diff) | Done |
| Event journal (SQLite) | Done |
| Resource governor (CPU/RAM self-policing) | Done |
| Output (syslog + login banner) | Done |
| Peer protocol (gRPC/mTLS, discovery, gossip) | Done |
| CLI (`watchtower show findings/topology/events/resources/baselines/peers`) | Done |

## Roadmap

1. **Phase 1 -- Foundation** -- Local observer, anomaly detection, CLI (done)
2. **Phase 2 -- Peer Protocol** -- gRPC/mTLS, LLDP-based peer discovery, cross-switch correlation (done)
3. **Phase 3 -- LLM Integration** -- Local quantized model for natural language findings and `watchtower ask`
4. **Phase 4 -- Pre-Flight Simulation** -- `watchtower simulate` for change impact analysis
5. **Phase 5 -- Central Watchtower** -- Hierarchy election, fleet-wide aggregation, REST API
6. **Phase 6 -- Polish** -- Prometheus metrics, long-term baselines, SONiC build integration

See [WATCHTOWER_DESIGN.md](WATCHTOWER_DESIGN.md) for the full design specification.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and how to submit changes.

## License

[Apache License 2.0](LICENSE)
