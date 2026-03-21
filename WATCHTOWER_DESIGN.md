# Watchtower: Distributed Network Observer for SONiC

## Overview

Watchtower is a read-only, distributed observability system for SONiC switches.
It runs as a Docker container on each switch, monitors local state via Redis,
communicates with peer Watchtower instances on neighboring switches, and uses a
small local LLM to orchestrate investigation scripts and produce human-readable
findings.

Watchtower never modifies routing, switching, or forwarding state. Its external
outputs are limited to: the login banner, syslog messages, and peer gossip.

---

## Design Principles

1. **Read-only** -- never writes to CONFIG_DB, APPL_DB, ASIC_DB, or STATE_DB
2. **Cannot cause an outage** -- crash it, restart it, ignore it; network is fine
3. **Locally intelligent** -- each instance makes its own inferences from local
   and peer data; no central controller required
4. **Incrementally useful** -- value on a single switch, more value across a fabric
5. **Resource-aware** -- self-policing resource limits, configurable budgets
6. **Operator-facing** -- produces findings humans can act on, not raw data dumps

---

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
|  +--------------------+  | ASIC_DB          |         ^ banner   |
|         |                +--------+---------+         |          |
|         | /dev/log                | read-only         |          |
|         v                        v                    |          |
|  +------------------------------------------------------------+  |
|  |  Watchtower Container                                      |  |
|  |                                                            |  |
|  |  +----------------+     +------------------------------+   |  |
|  |  |  Tiny LLM      |<--->|  Script Library              |   |  |
|  |  |  (orchestrator) |     |  collectors/ + analyzers/    |   |  |
|  |  +-------+--------+     +------------------------------+   |  |
|  |          |                                                 |  |
|  |  +-------v--------+     +------------------------------+   |  |
|  |  | Event Journal  |     | Resource Governor            |   |  |
|  |  | (SQLite)       |     | - CPU/RAM self-monitoring    |   |  |
|  |  +----------------+     | - throttle/backoff logic     |   |  |
|  |                         +------------------------------+   |  |
|  |  +------------------------------------------------------+  |  |
|  |  | Syslog Emitter                                       |  |  |
|  |  | - findings -> syslog (tag: watchtower)               |  |  |
|  |  | - flows to host rsyslog -> external SIEM/NOC         |  |  |
|  |  +------------------------------------------------------+  |  |
|  |  +------------------------------------------------------+  |  |
|  |  | Peer Protocol (gRPC + mTLS)                          |  |  |
|  |  | - discovers neighbors via local LLDP data            |  |  |
|  |  | - exchanges event summaries with peer Watchtowers    |  |  |
|  |  | - uses SONiC gNMI certificate infrastructure         |  |  |
|  |  | - port: 5950                                         |  |  |
|  |  +------------------------------------------------------+  |  |
|  +------------------------------------------------------------+  |
+------------------------------------------------------------------+
    |               |                                |
    | gRPC/mTLS     | gRPC/mTLS                      | gRPC/mTLS
    v               v                                v
[Peer Watchtower] [Peer Watchtower]    [Central Watchtower (optional)]
```

---

## Components

### 1. Tiny LLM (Orchestrator)

**Model:** A quantized model in the 0.5B-3B parameter range. Candidates:
- Qwen2-0.5B (very small, good tool-use)
- Phi-3-mini-4k (3.8B but strong reasoning)
- TinyLlama-1.1B
- SmolLM2-1.7B

The model runs locally via llama.cpp. It does NOT need to be fast. It is
event-triggered or runs on a slow poll cycle (e.g., every 30-60 seconds,
or on-demand when events arrive).

**Role:** The LLM is an orchestrator, not a data processor. It:
- Receives event notifications ("port stats anomaly detected", "peer alert
  received", "BGP session state changed")
- Decides which scripts to call to investigate
- Reads script output (structured JSON)
- Synthesizes findings in natural language
- Decides severity (info / warning / critical)
- Updates the event journal
- Updates the login banner if warranted

**Tool-use pattern:**
```
System: You are Watchtower, a network observer on switch {hostname}.
        You monitor network health and produce findings for operators.
        You are read-only. You cannot make changes to the network.
        Use the available tools to investigate events and report findings.

Event trigger: "anomaly detected on Ethernet48: RX CRC errors 1,847/min
               (baseline: 2/min)"

LLM: I should check what is connected on Ethernet48 and whether the
     neighbor sees the same issue.

     -> calls: get_lldp_neighbor(port="Ethernet48")
     -> calls: get_optic_health(port="Ethernet48")
     -> calls: get_peer_events(neighbor="switch-b")

LLM: [reads results, synthesizes finding]

     Finding (WARNING): Optic degradation on Ethernet48 (link to switch-b).
     RX CRC errors at 1,847/min vs baseline 2/min. Optic RX power is
     -8.2 dBm, down from baseline -2.1 dBm. switch-b confirms TX power
     normal on its side. Likely failing RX optic on this end. This is the
     primary path to spine-3; if this link fails, traffic reroutes through
     spine-4 (currently at 71% utilization).
```

### 2. Script Library

Scripts are the workhorses. They are deterministic, fast, and independently
testable. Each script does one thing and returns structured JSON.

**Collectors** -- gather raw data from Redis and system sources:

| Script                | Reads From      | Returns                          |
|-----------------------|-----------------|----------------------------------|
| port_stats.py         | COUNTERS_DB     | Per-port counters (RX/TX bytes, errors, drops) |
| bgp_state.py          | APPL_DB         | BGP session states, prefixes, timers |
| lldp_topology.py      | APPL_DB         | LLDP neighbors, local/remote port mapping |
| log_filter.py         | syslog          | Recent log entries, noise-filtered |
| interface_state.py    | APPL_DB/STATE_DB| Port admin/oper state, speed, MTU |
| queue_depths.py       | COUNTERS_DB     | Per-port per-queue watermarks    |
| optic_health.py       | STATE_DB        | DOM data: TX/RX power, temperature, voltage |
| route_table.py        | APPL_DB         | Routes, next hops, ECMP groups   |
| acl_state.py          | CONFIG_DB       | ACL rules and counters           |
| peer_events.py        | Peer gRPC       | Recent events from a specific peer |
| system_resources.py   | /proc           | CPU, RAM, disk usage (for self-policing) |

**Analyzers** -- process collected data and produce assessments:

| Script                | Purpose                                            |
|-----------------------|----------------------------------------------------|
| baseline_compare.py   | Compare current metrics against stored baselines   |
| path_trace.py         | Trace a prefix through the topology (which links/switches carry it) |
| impact_assess.py      | Given a link/switch failure, what prefixes/traffic are affected |
| change_simulate.py    | Given a proposed change, predict routing/traffic shifts |
| peer_correlate.py     | Match local events with peer events by timestamp   |
| log_correlate.py      | Correlate log entries with state changes in Redis  |
| topology_diff.py      | Compare current topology against last known good   |

### 3. Event Journal (SQLite)

Local persistent store. Small footprint. Stores:

**baselines table:**
- port, metric, hour_of_week, p50, p95, p99, sample_count
- Updated continuously via exponential moving averages
- Bucketed by hour-of-week to capture diurnal patterns

**events table:**
- timestamp, source (local/peer/syslog), category, severity, port, raw_data
- Indexed by timestamp and severity
- Auto-pruned (keep last 7 days of detail, 30 days of summaries)

**topology table:**
- local_port, neighbor_hostname, neighbor_port, last_seen, first_seen
- Updated from LLDP data on each poll cycle

**findings table:**
- timestamp, severity (info/warning/critical), summary, detail, related_events
- These are the LLM-generated outputs
- Queryable by operators via a simple local CLI or API

**peer_state table:**
- peer_hostname, last_heartbeat, last_event_summary, peer_severity

### 4. Peer Protocol

**Discovery:** Automatic via LLDP. When Watchtower reads LLDP neighbors from
APPL_DB, it attempts to connect to each neighbor's Watchtower on the designated
port. No manual configuration needed.

**Transport:** gRPC over TCP (port 5950). Mandatory mTLS.

**Authentication and Encryption:**

SONiC's gNMI telemetry container already uses x509 certificates for TLS.
Watchtower leverages the same PKI infrastructure:

- Certificates are stored in /etc/sonic/credentials/ (or wherever the
  deployment's cert management places them)
- Certificates are provisioned during switch deployment via ZTP, Ansible,
  or whatever certificate management the operator already uses
- Watchtower reads the same CA bundle, server cert, and key that gNMI uses
- No new PKI infrastructure required -- just mount the existing cert
  directory into the Watchtower container
- If certs are not present, peer protocol is disabled (local-only mode)
  rather than falling back to unencrypted

**Configuration (watchtower.yml):**
```yaml
peer:
  enabled: true
  port: 5950
  tls:
    cert: /etc/sonic/credentials/watchtower.crt  # or shared gNMI cert
    key: /etc/sonic/credentials/watchtower.key
    ca: /etc/sonic/credentials/ca.crt
    # If cert/key not found, peer protocol disabled gracefully
  verify_hostname: true
```

**Message types:**

```
Heartbeat (periodic, every 10s):
  - hostname
  - role (leaf / central)
  - uptime
  - current_severity (ok / info / warning / critical)
  - active_finding_count

EventShare (event-triggered):
  - hostname
  - timestamp
  - event_type (link_flap, bgp_change, anomaly, optic_degradation, ...)
  - affected_port
  - summary (one line)
  - metrics (key-value pairs relevant to the event)

TopologyFragment (periodic, every 60s):
  - hostname
  - neighbors: [{local_port, remote_host, remote_port, link_state}]

FindingShare (on new finding):
  - hostname
  - timestamp
  - severity
  - summary
  - affected_scope (ports, prefixes, paths)
```

**Propagation rules:**
- Local events: shared with direct peers only (1-hop)
- Findings with severity >= warning: shared with peers, who may re-share
  (with TTL decrement, max 3 hops) to build fabric-wide awareness
- No flooding -- TTL-limited gossip, not link-state broadcast

---

## Syslog Integration

Watchtower emits findings as syslog messages so external systems (SIEM,
NOC alerting, ticketing) can consume them without any special integration.

**Mechanism:** The container mounts /dev/log from the host (standard SONiC
container pattern). Watchtower writes syslog messages using Python's
syslog module. Messages flow through the host's rsyslog to wherever
syslog is already being forwarded.

**Syslog format:**
```
facility: LOG_LOCAL4 (configurable)
tag: watchtower

Severity mapping:
  Finding CRITICAL -> syslog LOG_CRIT
  Finding WARNING  -> syslog LOG_WARNING
  Finding INFO     -> syslog LOG_INFO
  Heartbeat/status -> syslog LOG_DEBUG (disabled by default)

Example message:
Mar 19 14:23:07 switch-a watchtower: [CRITICAL] Optic degradation on
  Ethernet48 (link to spine-3). RX power declining. Single path to
  rack-7 at risk. finding_id=f-20260319-0042
```

**What this enables:**
- External syslog collectors (Splunk, Graylog, ELK) pick up findings
  automatically
- NOC alerting rules can trigger on "watchtower" + severity
- SONiC's own "show logging" displays Watchtower findings alongside
  other switch logs
- No new integration required -- uses existing syslog forwarding pipeline
- The finding_id allows correlation between syslog, banner, and CLI output

**Configurable verbosity:**
```yaml
syslog:
  enabled: true
  facility: LOG_LOCAL4
  min_severity: warning    # only emit warning+ to syslog (default)
  # Options: debug, info, warning, critical
  include_peer_findings: false  # don't spam syslog with neighbor findings
```

---

## Resource Governor

Watchtower self-polices its resource usage. Operators configure hard limits
and Watchtower adjusts its own behavior to stay within them.

**How it works:**

The resource governor runs as a background thread that samples /proc/stat
and /proc/meminfo every 5 seconds. It exposes a simple API to the rest of
Watchtower: "can I do expensive work right now?"

**Configuration (watchtower.yml):**
```yaml
resources:
  # Hard limits -- Watchtower will not exceed these
  max_cpu_percent: 20       # of total system CPU (not per-core)
  max_memory_mb: 768        # RSS limit for the Watchtower process tree
  max_disk_mb: 200          # SQLite journal + model file

  # Soft limits -- Watchtower starts throttling at these thresholds
  throttle_cpu_percent: 15  # start deferring investigations
  throttle_memory_mb: 600   # start pruning caches

  # Behavior when system is under pressure (any process, not just us)
  system_cpu_ceiling: 80    # if total system CPU > 80%, pause all work
  system_memory_ceiling: 85 # if total system RAM > 85%, pause all work
```

**Throttle behavior (graduated response):**

```
System load normal, Watchtower below soft limits:
  -> Full operation: all collectors, analyzers, LLM investigations

Watchtower approaching soft limits:
  -> Reduce poll frequency (30s -> 60s -> 120s)
  -> Defer non-critical investigations (INFO severity)
  -> Limit concurrent LLM investigations to 1

Watchtower at hard limits OR system under pressure:
  -> Pause LLM entirely (most expensive component)
  -> Collectors continue at minimum frequency (120s)
  -> Template-based findings only (no LLM synthesis)
  -> Log: "watchtower: resource governor paused LLM inference"

System in crisis (CPU > 95% or RAM > 95%):
  -> Watchtower goes fully dormant
  -> Only heartbeat to peers: "I'm alive but paused"
  -> Resume automatically when pressure drops
  -> Log: "watchtower: dormant due to system resource pressure"
```

**Docker-level enforcement (belt AND suspenders):**

In addition to self-policing, the Docker container itself has cgroup limits:
```yaml
# docker-compose.yml
services:
  watchtower:
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 1G
        reservations:
          cpus: "0.1"
          memory: 256M
```

The self-policing is the graceful layer (backs off intelligently). The
Docker cgroup limit is the hard backstop (kills if exceeded). The self-
policing thresholds should always be set below the Docker limits so
Watchtower throttles before Docker has to intervene.

**Self-monitoring metrics (exposed via CLI and optionally Prometheus):**
```
watchtower_cpu_percent        # current CPU usage
watchtower_memory_mb          # current RSS
watchtower_disk_mb            # current SQLite + model size
watchtower_governor_state     # full / throttled / paused / dormant
watchtower_investigations_deferred  # count of skipped investigations
watchtower_llm_invocations_total    # total LLM calls since start
watchtower_poll_interval_current    # current (possibly throttled) interval
```

---

## Login Banner

**Mechanism:** A shell script at /etc/profile.d/watchtower.sh that runs on
every interactive login. It reads a findings summary file maintained by the
Watchtower container and prints it.

This approach:
- Does not interfere with the user's /etc/motd or SONiC banner config
- Appears after the MOTD (profile.d scripts run after MOTD is displayed)
- Is trivially removable (delete the script, banner disappears)
- Works even if the Watchtower container is stopped (shows stale data
  with a timestamp so the operator knows)

**The script (/etc/profile.d/watchtower.sh):**
```bash
#!/bin/bash
# Watchtower login banner -- displays active findings on SSH login
FINDINGS_FILE="/var/run/watchtower/banner.txt"
if [ -f "$FINDINGS_FILE" ] && [ -s "$FINDINGS_FILE" ]; then
    cat "$FINDINGS_FILE"
fi
```

**The findings file (/var/run/watchtower/banner.txt):**

Written by the Watchtower container via a volume mount. Updated only when
findings change.

```
========================================================
 WATCHTOWER: 2 active findings  [1 CRITICAL, 1 WARNING]
 Last updated: 2026-03-19 14:23:07 UTC
 Details: watchtower show findings
--------------------------------------------------------
 [CRITICAL] Optic degradation on Ethernet48 (link to
   spine-3). RX power declining. Single path to rack-7
   at risk.

 [WARNING] BGP session to leaf-12 flapping (3x in last
   hour). Peer Watchtower reports interface errors.
========================================================
```

When all clear:
```
=== WATCHTOWER: No active findings. Last checked 14:23 UTC ===
```

**Template variable (optional advanced mode):**

For operators who want to control banner placement, Watchtower can also
write to a file that the user's custom MOTD script sources:

```bash
# In the user's custom /etc/motd script or banner config:
# Include {{WATCHTOWER}} wherever you want findings to appear
# Watchtower provides: /var/run/watchtower/banner_inline.txt
```

This is optional. The /etc/profile.d approach works out of the box with
zero configuration.

**Banner length limits:**
- Maximum 20 lines by default (configurable)
- If more findings than fit, shows top N by severity + "and X more..."
- Each finding limited to 3 lines in the banner (full detail via CLI)

---

## Central Watchtower (Optional)

An optional Central Watchtower aggregates findings from across the fabric,
providing a fleet-wide view for NOC dashboards and alerting.

### Hierarchy Model

The hierarchy is inspired by spanning tree -- automatic, topology-aware,
and resilient to failures. But instead of blocking ports, it determines
which Watchtower instances report upward and which one aggregates.

**Roles:**

| Role     | Where it runs | What it does                           |
|----------|---------------|----------------------------------------|
| Leaf     | Switch        | Monitors local switch, peers with neighbors |
| Spine    | Switch        | Same as leaf, plus aggregates from connected leaves |
| Central  | Server/VM     | Aggregates from spines, provides fleet-wide API and dashboard |

**Election (automatic):**
```
1. Every Watchtower starts as a leaf.

2. Each Watchtower has a configurable priority (default: 100).
   Lower priority = more likely to become an aggregation point.

3. Aggregation role is determined by topology position:
   - If a Watchtower has peers that are all leaves (no upward
     connections), it is a leaf.
   - If a Watchtower connects leaves to other aggregation points,
     it naturally becomes a spine-level aggregator.
   - A Central Watchtower is explicitly configured (priority: 0,
     role: central). It does not run on a switch.

4. If a spine-level aggregator fails, its leaves fall back to
   gossip-only mode (they still share with direct peers). No
   single point of failure.
```

In practice, for a typical Clos fabric:
```
          [Central Watchtower]         <-- server/VM (optional)
           /        |        \
    [Spine-1 WT] [Spine-2 WT] [Spine-3 WT]   <-- aggregate from leaves
      / | \        / | \        / | \
   Leaf Leaf Leaf Leaf Leaf Leaf Leaf Leaf Leaf  <-- switch Watchtowers
```

**Upward reporting:**
- Leaves send finding summaries to their spine-level peers (not raw data)
- Spines aggregate and deduplicate, then send to central
- Central maintains the fleet-wide finding database
- If central is unreachable, spines queue summaries (bounded buffer)
  and replay when central returns

**What the Central Watchtower adds:**
- Fleet-wide topology map (assembled from all topology fragments)
- Cross-fabric correlation ("leaf-1 and leaf-8 both see anomalies on
  their spine-3 uplinks -- spine-3 may have a systemic issue")
- Single API/dashboard for NOC operators
- Historical trend analysis across the fleet
- REST API for integration with ticketing, PagerDuty, etc.

**What it does NOT do:**
- Does not control leaf/spine Watchtowers
- Does not modify any switch configuration
- Is not required for leaf/spine Watchtowers to function
- If it goes down, nothing changes on the switches

**Central Watchtower runs on a server, not a switch:**
- No resource constraints (can run a larger LLM if desired)
- Can store longer history, build richer dashboards
- Could expose a web UI (future phase)
- Could integrate with external APIs (PagerDuty, Slack, JIRA)

**Configuration for hierarchy:**
```yaml
# On a leaf switch (default, no config needed):
hierarchy:
  role: auto          # auto-detect based on topology position
  priority: 100

# On a spine switch (optional override):
hierarchy:
  role: auto
  priority: 50        # lower priority = preferred aggregator

# Central Watchtower (explicit):
hierarchy:
  role: central
  priority: 0
  listen: 0.0.0.0:5950
  api:
    enabled: true
    port: 8080        # REST API for NOC tools
```

---

## Operational Modes

### Passive Monitoring (default)

Watchtower polls Redis and syslog on a regular interval (configurable,
default 30s). Runs baseline comparison. If anomalies are detected, triggers
the LLM to investigate. Otherwise, quietly updates baselines and topology.

### Event-Triggered Investigation

When a significant state change is detected (link down, BGP session change,
new peer alert), Watchtower immediately triggers the LLM to investigate
rather than waiting for the next poll cycle. Events that trigger immediate
investigation:

- Link state change (up->down or down->up)
- BGP session state change
- Peer finding with severity >= warning
- Counter anomaly exceeding 10x baseline
- New or missing LLDP neighbor

### Pre-Flight Mode (operator-initiated)

An operator can ask Watchtower to simulate a change:

```
$ watchtower simulate --drain switch-b
$ watchtower simulate --shutdown Ethernet48
$ watchtower simulate --upgrade switch-b --duration 120s
```

Watchtower uses its topology map, current routing state, and current traffic
baselines to predict:
- Which BGP sessions will drop
- Which prefixes will reconverge and to where
- Which links will see increased load (and whether they can handle it)
- Estimated reconvergence time based on observed BGP timers

This is read-only -- it simulates in memory, does not execute anything.

### Query Mode

Operators can interact with Watchtower locally:

```
$ watchtower show findings           # current active findings
$ watchtower show findings --history # resolved findings from last 7 days
$ watchtower show topology           # current topology map (local view)
$ watchtower show topology --fabric  # topology including peer data
$ watchtower show baselines Ethernet48  # what is normal for this port
$ watchtower show events --last 1h   # event journal for last hour
$ watchtower show peers              # peer Watchtower status
$ watchtower show resources          # current resource usage + governor state
$ watchtower ask "why is traffic to 10.1.0.0/16 slow?"  # free-form query
```

The "ask" command passes the question to the LLM, which uses the script
library to investigate and returns a plain-English answer.

---

## LLM Details

### System Prompt (draft)

```
You are Watchtower, a read-only network observer running on SONiC switch
{hostname} ({platform}, {asic_type}).

Your job is to monitor network health, investigate anomalies, correlate
events across this switch and its peers, and produce clear, actionable
findings for network operators.

You have access to tools (Python scripts) that read data from the switch.
You CANNOT modify the switch configuration or state. You are an observer.

When investigating, follow this pattern:
1. Gather relevant data using collector tools
2. Compare against baselines using analyzer tools
3. Check if peers see related events
4. Assess impact using topology and routing data
5. Produce a finding with: what is happening, why it matters, what the
   operator should consider doing

Be concise. Operators are busy. Lead with the conclusion.

Severity levels:
- INFO: Notable but not actionable (e.g., new neighbor detected)
- WARNING: Degradation that may become a problem (e.g., optic power
  declining, link near capacity)
- CRITICAL: Active or imminent traffic impact (e.g., single point of
  failure with degraded component, all paths to a destination lost)
```

### Tool Definitions

Each script is registered as a tool with a name, description, and parameter
schema. Example:

```
Tool: get_port_stats
Description: Get current counter values for a port or all ports.
Parameters:
  - port (optional, string): Port name (e.g., "Ethernet48"). If omitted,
    returns summary for all ports.
  - period (optional, int): Seconds to average over. Default 30.
Returns: JSON with rx_bytes, tx_bytes, rx_errors, tx_errors, rx_drops,
         tx_drops, rx_crc_errors, etc.

Tool: get_lldp_neighbor
Description: Get LLDP neighbor information for a port.
Parameters:
  - port (required, string): Port name.
Returns: JSON with neighbor_hostname, neighbor_port, neighbor_description,
         chassis_id.

Tool: check_baseline
Description: Compare current metric value against historical baseline.
Parameters:
  - port (required, string): Port name.
  - metric (required, string): Metric name (e.g., "rx_crc_errors").
Returns: JSON with current_value, baseline_p50, baseline_p95, baseline_p99,
         deviation_factor, is_anomaly (bool).

Tool: trace_path
Description: Trace the forwarding path for a given destination prefix.
Parameters:
  - prefix (required, string): IP prefix (e.g., "10.1.0.0/16").
Returns: JSON with local_nexthops, ecmp_members, downstream_topology
         (from peer data if available).

Tool: assess_impact
Description: Predict the impact of a link or device failure.
Parameters:
  - failure_type (required, string): "link" or "device"
  - target (required, string): Port name or hostname.
Returns: JSON with affected_prefixes, reroute_paths, capacity_impact.

Tool: get_peer_events
Description: Get recent events from a peer Watchtower.
Parameters:
  - peer (required, string): Peer hostname.
  - last_seconds (optional, int): Time window. Default 300.
Returns: JSON array of events from that peer.

Tool: update_banner
Description: Update the login banner with current findings.
Parameters:
  - findings (required, list): Findings to display.
Returns: Success/failure.

Tool: get_resource_status
Description: Get current Watchtower resource usage and governor state.
Parameters: none
Returns: JSON with cpu_percent, memory_mb, governor_state, throttle_level.
```

### Inference Budget

To keep resource usage predictable:
- Maximum 10 tool calls per investigation
- Maximum 2,000 output tokens per finding
- Maximum 5 concurrent investigations (queue the rest)
- If the poll cycle finds nothing anomalous, the LLM is not invoked at all
- Baseline computation and anomaly detection happen in Python, NOT in the LLM
- Resource governor can reduce these limits dynamically under pressure

---

## Container Base Image

### Decision: SONiC-native base + llama-cpp-python (Phase 3)

There are four realistic options. Here is the analysis:

**Option A: python:3.11-slim-bookworm + llama-cpp-python**
```
Base:       python:3.11-slim (~120MB)
Add:        llama-cpp-python (~50MB compiled), grpcio, redis client
Add (Ph3):  Quantized model file (~300-500MB)
Total:      ~500MB Phase 1, ~900MB Phase 3
Pros:       Simple, portable, easy to develop and test locally
Cons:       Not native to SONiC build system, swsscommon requires
            manual installation or Redis used directly
Best for:   Standalone development and testing
```

**Option B: SONiC Debian Bookworm base (sonic-slave)**
```
Base:       SONiC's Debian Bookworm base (~200MB, same as other containers)
Add:        Python packages, swsscommon (native .deb), grpcio
Add (Ph3):  llama-cpp-python + model file
Total:      ~600MB Phase 1, ~1.1GB Phase 3
Pros:       Native integration with SONiC build system, swsscommon
            available as .deb, consistent with other SONiC containers
Cons:       Tied to SONiC's Debian version, harder to develop outside
            a SONiC build environment
Best for:   Production deployment, upstreaming to SONiC community
```

**Option C: Ollama baked in**
```
Base:       Ollama container (~800MB) + Python runtime
Total:      ~1.2-1.5GB
Pros:       Easy model management, swap models without rebuild
Cons:       Heaviest option, Ollama is a separate process, overkill
            for a single small model on constrained hardware
Best for:   Not recommended for switch deployment
```

**Option D: Two-stage (recommended)**
```
Phase 1-2:  Option A (slim Python). No LLM, ~300MB image.
            Use redis-py directly (no swsscommon dependency).
            Easy to develop, test, deploy anywhere.

Phase 3+:   Transition to Option B for production. Add llama-cpp-python
            and model. Target ~1GB image. Integrate with SONiC build.

Both:       Same Python code, same script library. Only the container
            base and LLM runtime differ.
```

**Recommendation: Option D.**

Start with a slim Python image for rapid development. The redis-py library
can read SONiC's Redis databases directly (they are standard Redis). You
don't strictly need swsscommon -- it provides convenience wrappers and
pub/sub helpers, but redis-py with the right key patterns works fine.

When ready for production and SONiC community integration, transition to
the SONiC-native base image. The Python code is identical either way.

**Why not Ollama:** On a switch with 8-16GB RAM total, Ollama's overhead
(Go runtime, HTTP API server, model management) is wasteful. llama-cpp-
python is a single shared library that loads the model into memory and
runs inference. No extra processes, no HTTP layer, minimal overhead.
The model file is baked into the container image (or downloaded once on
first start and cached on a persistent volume).

**Model file delivery:**
```yaml
# Option 1: Baked into image (simplest)
# Dockerfile:
COPY models/qwen2-0.5b-q4_k_m.gguf /opt/watchtower/models/

# Option 2: Downloaded on first start (smaller image, needs internet)
# watchtower.yml:
llm:
  model_url: "https://internal-repo.example.com/models/qwen2-0.5b-q4_k_m.gguf"
  model_path: /var/lib/watchtower/models/
  # Downloaded once, cached on persistent volume

# Option 3: Mounted from host (ops team manages model files)
# docker-compose.yml:
volumes:
  - /opt/watchtower-models:/opt/watchtower/models:ro
```

---

## Resource Budget

Target steady-state resource usage on a SONiC switch:

**Phase 1-2 (no LLM):**

| Resource        | Budget    | Notes                              |
|-----------------|-----------|------------------------------------|
| CPU             | 0.1-0.3 core | Collectors + baseline math only |
| RAM             | 128-256MB | Python + SQLite + Redis connections |
| Disk            | 50MB      | SQLite journal only                |
| Network (peer)  | <50 Kbps  | Heartbeats + event summaries       |
| Redis impact    | Minimal   | Read-only, polled every 30s        |

**Phase 3+ (with LLM):**

| Resource        | Budget    | Notes                              |
|-----------------|-----------|------------------------------------|
| CPU             | 1 core    | Bursty during inference, idle otherwise |
| RAM             | 512MB-1GB | Model (~300-500MB) + Python + SQLite |
| Disk            | 500MB     | Model file + SQLite journal        |
| Network (peer)  | <100 Kbps | Heartbeats + event summaries       |
| Redis impact    | Minimal   | Read-only, polled every 30s        |

If the model is too large, fallback option: run without the LLM and use
structured templates for findings. The scripts and analyzers still provide
value. The LLM adds polish, free-form queries, and better correlation --
but it is not strictly required. The resource governor handles this
transition automatically.

---

## Implementation Phases

### Phase 1: Foundation (Local Observer, No LLM, No Peers)

Deliverables:
- Docker container (python:3.11-slim base)
- Redis reader library (redis-py, direct key access)
- Collector scripts: port_stats, interface_state, lldp_topology, bgp_state,
  optic_health, log_filter
- Baseline engine (exponential moving averages in baseline_compare.py)
- Event journal (SQLite schema and writer)
- Anomaly detection (threshold-based, template findings)
- Resource governor (CPU/RAM self-monitoring)
- Syslog emitter (findings -> host syslog)
- Login banner (/etc/profile.d/watchtower.sh + findings file)
- CLI: watchtower show topology, watchtower show events, watchtower show
  findings, watchtower show resources
- Mock Redis test harness for development without a switch

Value: Anomaly detection and topology awareness on a single switch.
Findings are template-generated ("Port Ethernet48 RX errors above p99").
Syslog output enables external alerting from day one.

### Phase 2: Peer Protocol

Deliverables:
- protobuf/gRPC service definition (heartbeat, event share, topology fragment,
  finding share)
- mTLS using SONiC gNMI certificate infrastructure
- Peer discovery via LLDP data
- Peer state table in SQLite
- peer_events.py collector
- peer_correlate.py analyzer
- topology_diff.py analyzer
- CLI: watchtower show peers, watchtower show topology --fabric
- Cross-switch event correlation (template-based)

Value: Multi-switch awareness. Correlated findings like "link error on
Ethernet48 -- neighbor switch-b also reports errors on its end."

### Phase 3: LLM Integration

Deliverables:
- llama-cpp-python runtime in container
- Model selection and benchmarking on switch-class hardware
- Tool-use wrapper (registers scripts as callable tools)
- Investigation loop (event -> LLM -> tools -> finding)
- Natural language findings replacing templates
- Resource governor LLM-aware throttling
- CLI: watchtower ask "..." for free-form queries
- Upgraded banner with LLM-generated summaries
- Graceful degradation (LLM unavailable -> templates)

Value: Richer correlation, natural language output, free-form investigation.

### Phase 4: Pre-Flight Simulation

Deliverables:
- path_trace.py using topology + routing data
- impact_assess.py using topology + baselines
- change_simulate.py combining the above
- CLI: watchtower simulate --drain/--shutdown/--upgrade
- LLM integration for simulation summary

Value: Operators can predict the impact of maintenance and changes before
executing them.

### Phase 5: Central Watchtower + Hierarchy

Deliverables:
- Hierarchy election protocol (priority-based, topology-aware)
- Upward reporting (leaf -> spine -> central)
- Central Watchtower server image (no switch resource constraints)
- Fleet-wide finding aggregation and deduplication
- REST API on central for NOC tools
- Cross-fabric correlation at central level
- Configuration for role/priority

Value: Fleet-wide visibility from a single pane of glass. NOC integration.

### Phase 6: Polish and Hardening

Deliverables:
- Prometheus metrics endpoint (optional)
- Long-term baseline storage and trend analysis
- Configuration validation and schema
- Log rotation and journal pruning tuning
- Integration tests with SONiC virtual switch (vs image)
- Documentation and packaging for SONiC build system (sonic-buildimage)
- Contribution to SONiC community (if desired)

---

## Configuration Reference (watchtower.yml)

```yaml
# General
hostname: auto                  # auto-detect from system
poll_interval: 30               # seconds between collection cycles
log_level: info                 # debug, info, warning, critical

# Resource governor
resources:
  max_cpu_percent: 20
  max_memory_mb: 768
  max_disk_mb: 200
  throttle_cpu_percent: 15
  throttle_memory_mb: 600
  system_cpu_ceiling: 80
  system_memory_ceiling: 85

# Anomaly detection
anomaly:
  deviation_threshold: 5        # flag if current > baseline_p95 * N
  immediate_threshold: 10       # trigger immediate investigation if > N
  baseline_warmup_hours: 168    # 1 week before baselines are trusted

# Syslog
syslog:
  enabled: true
  facility: LOG_LOCAL4
  min_severity: warning
  include_peer_findings: false

# Banner
banner:
  enabled: true
  max_lines: 20
  findings_file: /var/run/watchtower/banner.txt
  show_all_clear: true          # show "no findings" or hide banner

# Peer protocol
peer:
  enabled: true
  port: 5950
  tls:
    cert: /etc/sonic/credentials/watchtower.crt
    key: /etc/sonic/credentials/watchtower.key
    ca: /etc/sonic/credentials/ca.crt
  verify_hostname: true
  heartbeat_interval: 10        # seconds
  topology_share_interval: 60   # seconds
  finding_ttl: 3                # max hops for finding propagation

# Hierarchy
hierarchy:
  role: auto                    # auto, leaf, central
  priority: 100                 # lower = preferred aggregator (0 = central)

# LLM (Phase 3+)
llm:
  enabled: false                # enable when model is available
  backend: llama_cpp            # llama_cpp or none
  model_path: /opt/watchtower/models/qwen2-0.5b-q4_k_m.gguf
  max_tool_calls: 10
  max_output_tokens: 2000
  max_concurrent_investigations: 5
  context_size: 4096            # model context window

# Journal
journal:
  path: /var/lib/watchtower/journal.db
  retention_detail_days: 7
  retention_summary_days: 30
  max_size_mb: 100
```

---

## Project Structure (proposed)

```
sonic-watchtower/
  Dockerfile
  Dockerfile.sonic              # SONiC-native base (Phase 3+)
  docker-compose.yml
  README.md
  setup.py
  watchtower.yml.example        # example configuration

  watchtower/
    __init__.py
    main.py                     # entry point, event loop
    config.py                   # configuration management
    governor.py                 # resource governor

    collectors/
      __init__.py
      base.py                   # base collector class
      port_stats.py
      bgp_state.py
      lldp_topology.py
      log_filter.py
      interface_state.py
      queue_depths.py
      optic_health.py
      route_table.py
      acl_state.py
      peer_events.py
      system_resources.py

    analyzers/
      __init__.py
      base.py                   # base analyzer class
      baseline_compare.py
      path_trace.py
      impact_assess.py
      change_simulate.py
      peer_correlate.py
      log_correlate.py
      topology_diff.py

    llm/
      __init__.py
      engine.py                 # LLM inference wrapper (llama-cpp-python)
      tools.py                  # tool registration and dispatch
      prompts.py                # system prompts and templates
      fallback.py               # template-based findings (no LLM)

    peer/
      __init__.py
      protocol.py               # gRPC service implementation
      discovery.py              # LLDP-based peer discovery
      client.py                 # gRPC client for connecting to peers
      hierarchy.py              # role election and upward reporting

    store/
      __init__.py
      journal.py                # SQLite event journal
      baselines.py              # baseline computation and storage
      topology.py               # topology graph storage
      findings.py               # findings storage and lifecycle
      schema.sql                # SQLite schema definition

    output/
      __init__.py
      banner.py                 # login banner writer and formatter
      syslog_emitter.py         # syslog output
      prometheus.py             # optional metrics endpoint

    cli/
      __init__.py
      main.py                   # CLI entry point (click-based)
      show.py                   # show commands
      simulate.py               # simulate commands
      ask.py                    # free-form query command

  proto/
    watchtower.proto            # gRPC service definition

  scripts/
    watchtower.sh               # /etc/profile.d banner script
    install.sh                  # install helper for non-SONiC-build deploys

  tests/
    conftest.py                 # shared test fixtures
    mock_redis/                 # fake Redis data for testing
      counters_db.json
      appl_db.json
      state_db.json
      config_db.json
    test_collectors/
    test_analyzers/
    test_peer/
    test_store/
    test_llm/
    test_output/
    test_governor/
    test_cli/
```

---

## Example Investigation Flow

```
1. Poll cycle runs. port_stats.py reads COUNTERS_DB.

2. baseline_compare.py flags: Ethernet48 rx_crc_errors = 1,847/min
   (baseline p99 = 12/min). Deviation factor: 154x. ANOMALY.

3. Event written to journal. LLM investigation triggered.
   (If LLM not available, template finding generated instead.)

4. LLM calls: get_lldp_neighbor(port="Ethernet48")
   Result: neighbor is switch-b, port Ethernet12

5. LLM calls: get_optic_health(port="Ethernet48")
   Result: RX power = -8.2 dBm (baseline = -2.1 dBm), TX power normal

6. LLM calls: get_peer_events(peer="switch-b", last_seconds=300)
   Result: switch-b reports no anomalies on Ethernet12, TX power normal

7. LLM calls: assess_impact(failure_type="link", target="Ethernet48")
   Result: this link carries 23 prefixes, alternate path via spine-4
   exists, spine-4 downlink currently at 71% utilization

8. LLM produces finding:

   [WARNING] Optic degradation on Ethernet48 (link to switch-b).
   RX CRC errors at 1,847/min (baseline: 2/min). Local RX power
   has dropped to -8.2 dBm from baseline -2.1 dBm. switch-b
   reports normal TX power, suggesting a failing RX optic on this
   switch. This link serves 23 prefixes; failover path via spine-4
   is available but would push spine-4 to ~85% utilization.
   Recommendation: schedule optic replacement on Ethernet48.

9. Finding stored in journal. Banner updated. Syslog emitted.
   Peers notified via FindingShare. If central exists, finding
   propagated upward.

10. Operator SSHs in, sees banner. Runs "watchtower show findings"
    for full detail. Runs "watchtower simulate --shutdown Ethernet48"
    to verify failover capacity before scheduling maintenance.
```
