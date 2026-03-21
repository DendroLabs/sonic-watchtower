# FBOSS Architecture Summary

## What is FBOSS?

**Facebook Open Switching System (FBOSS)** is Meta's software stack for controlling and managing network switches in their data center infrastructure. It's a large C++ codebase (~500K+ lines) that provides a complete control plane for whitebox switches with hardware forwarding ASICs.

---

## High-Level Architecture

FBOSS follows a **distributed, microservices architecture** with a central state distribution bus:

```
                    +-------------+
                    | BGP / OpenR |  (external routing daemons)
                    |  (routing)  |
                    +------+------+
                           | Thrift RPC (route injection)
                           v
+---------------+   +-------------+   +---------------+
| QsfpService   |   |   Agent     |   | PlatformMgr   |
| (optics/PHY)  |   | (core ctrl) |   | (HW discovery)|
|  :5910        |   |  :5909      |   |  :5975        |
+-------+-------+   +------+------+   +---------------+
        |                  |
        |   +--------------+--------------+
        |   |  FSDB (State Database)      |
        +---+  Pub/Sub state bus :5908    +---+
            +-----------------------------+   |
                                              |
                  +--------------+   +--------+------+
                  | LedService   |   | Fan/Sensor    |
                  |  :5930       |   | Services      |
                  +--------------+   +---------------+
```

---

## Core Components

### 1. The Agent Daemon (fboss/agent/)

The central piece -- runs on each switch and controls the hardware ASIC. It has a **three-layer internal architecture**:

- **SwitchState** -- An immutable, **copy-on-write tree** representing the entire switch state (ports, VLANs, routes, interfaces, neighbors, ACLs, etc.). Lock-free reads, synchronized writes via StateDelta diffs.
- **SwSwitch** -- The hardware-independent software switch logic. Manages state transitions, packet processing, and protocol handling (ARP, NDP, DHCPv4/v6, LLDP, MPLS).
- **HwSwitch** -- Abstract hardware interface with multiple implementations:
  - **SAI** (Switch Abstraction Interface) -- vendor-neutral, supports Broadcom, Marvell, Tajo ASICs
  - **BCM** (legacy Broadcom SDK) -- direct Broadcom SDK integration
  - **Mock/Sim** -- for testing

**Concurrency model**: Separate event bases for state updates (single-threaded), background tasks (neighbor aging, LLDP), packet RX, and Thrift API serving.

**Key protocol handlers**: ARP, NDP, DHCPv4/v6, LLDP, MPLS, IPv4/IPv6 forwarding.

### 2. FSDB -- Forwarding State Database (fboss/fsdb/)

A **central pub/sub state distribution service**. Services publish operational state to hierarchical paths and others subscribe to deltas. This decouples services from each other:

- Agent publishes port status, routes, neighbor tables
- QsfpService subscribes to port state changes
- LedService subscribes to port/transceiver state
- Supports full state dumps, delta streams, and pattern-matched paths
- Binary, JSON, and Compact Thrift serialization

### 3. QsfpService (fboss/qsfp_service/)

Manages **optical transceivers** (SFP, QSFP, CMIS modules):

- Transceiver state machines and configuration
- DOM (Digital Optical Monitoring) data
- Firmware upgrades for optics
- PHY (external retimer) programming via SAI
- PRBS testing and signal integrity diagnostics
- MACsec key management

### 4. Platform Manager (fboss/platform/platform_manager/)

**Hardware discovery and inventory** service:

- Enumerates I2C buses, PCI devices, FPGAs
- Discovers platform topology (slots, PmUnits, FRUs)
- Provides EEPROM contents, BSP/firmware versions
- Validates platform configuration

### 5. LED Service (fboss/led_service/)

Subscribes to FSDB for port state, drives front-panel LEDs with platform-specific managers (20+ platform variants: Wedge, Minipack, Montblanc, Morgan, etc.).

### 6. Additional Services

- **Fan Service** -- thermal management and fan speed control
- **Sensor Service** -- temperature/voltage monitoring
- **MKA Service** -- MACsec Key Agreement for link encryption
- **Rackmon** -- rack-level monitoring
- **CLI (fboss/cli/fboss2/)** -- command-line interface for operators

---

## Hardware Abstraction

### SAI Layer (fboss/agent/hw/sai/)

The primary hardware abstraction, following the OCP SAI specification:

- **api/** -- Type-safe C++ wrappers around the SAI C API (52 API families)
- **store/** -- RAII, reference-counted object storage with warm boot persistence
- **switch/** -- SaiSwitch implementation + Manager pattern (one manager per SAI object type: Port, Route, NextHop, Buffer, ACL, Mirror, Queue, etc.)
- **fake/** -- Pure software SAI for testing without hardware

### Supported ASICs (fboss/agent/hw/switch_asics/)

| Vendor     | ASICs                                                  |
|------------|--------------------------------------------------------|
| Broadcom   | Tomahawk 3/4/5/6, TomahawkUltra1, Trident2, Jericho 2/3/4 |
| Marvell    | Chenab, Chenab2                                        |
| Tajo       | Custom ASIC                                            |
| Credo      | PHY retimers (Agera3)                                  |

Each ASIC defines a feature matrix (500+ features), resource limits, and MMU configuration.

### Supported Platforms

20+ hardware platforms including Wedge (100/400/800), Minipack (3BA/3N), Darwin, Montblanc, Meru800, Janga, Morgan, and others.

---

## Inter-Service Communication

All services communicate via **Apache Thrift RPC**. Key Thrift interface files:

| File                              | Purpose                                  |
|-----------------------------------|------------------------------------------|
| agent/if/ctrl.thrift              | Route, port, neighbor management (~1200 lines) |
| agent/if/switch_config.thrift     | Switch configuration schema              |
| agent/if/switch_state.thrift      | Runtime state schema                     |
| fsdb/if/fsdb.thrift               | Pub/sub state distribution               |
| qsfp_service/if/qsfp.thrift      | Transceiver management (336 methods)     |
| lib/phy/phy.thrift                | Common PHY control base                  |

---

## Key Design Patterns

1. **Copy-on-Write State** -- Immutable state tree enables lock-free reads and efficient diffing
2. **Manager Pattern** -- One manager class per hardware object type in the SAI layer
3. **Pub/Sub via FSDB** -- Services are loosely coupled through a central state bus
4. **Warm Boot** -- Non-disruptive restarts by persisting SAI object handles and replaying state
5. **Hardware Agnostic Core** -- SwSwitch is entirely independent of the underlying ASIC
6. **Pluggable ASIC Support** -- Adding a new ASIC means implementing HwAsic traits + SAI vendor library

---

## Routing Integration

FBOSS does **not** include its own BGP daemon. External routing processes (BGP, OpenR) inject routes into the Agent via Thrift RPC with admin distance priorities (EBGP=20, IBGP=200, OpenR=10, Static=1). The Agent's RIB resolves routes and programs the hardware FIB.

---

## Build System

- **CMake** (primary) and **Buck2** (secondary)
- Supports multiple SAI vendor implementations (BRCM, Tajo, Chenab)
- Configurable sanitizers and benchmarks
- getdeps.sh for dependency management
