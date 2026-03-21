# SONiC Redis Key Patterns

Actual Redis key patterns observed in a SONiC VS (virtual switch) instance,
compared against Watchtower's mock fixtures.

**Validated:** 2026-03-21 against SONiC VS image `sonic-vs.img` (SONiC-OS-master.1025703-68843c5ef)

## APPL_DB (Redis DB 0) — separator: `:`

178 keys total. Tables observed:

| Table | Key Pattern | Fields (sample) |
|-------|-------------|-----------------|
| PORT_TABLE | `PORT_TABLE:<port_name>` | admin_status, alias, index, lanes, mtu, speed, dhcp_rate_limit, **oper_status**, flap_count, description |
| LLDP_LOC_CHASSIS | `LLDP_LOC_CHASSIS` | (local chassis info) |
| INTF_TABLE | `INTF_TABLE:<intf>` | (interface IP config) |
| NEIGH_TABLE | `NEIGH_TABLE:<intf>:<ip>` | (ARP/NDP neighbor) |
| ROUTE_TABLE | `ROUTE_TABLE:<prefix>` | (routing data) |
| COPP_TABLE | `COPP_TABLE:*` | (control plane policing) |
| SWITCH_TABLE | `SWITCH_TABLE:switch` | (switch-level config) |
| TUNNEL_DECAP_TABLE | `TUNNEL_DECAP_TABLE:*` | (tunnel config) |

**Notes:**
- `LLDP_ENTRY_TABLE` and `BGP_NEIGHBOR_TABLE` keys are absent on a standalone VS
  (no neighbors). Key patterns are expected to match when neighbors are present.
- PORT_TABLE in APPL_DB contains `oper_status` (in addition to STATE_DB).
  This is dynamic — orchagent updates it.

## COUNTERS_DB (Redis DB 2) — separator: `:`

3661 keys total.

| Table | Key Pattern | Notes |
|-------|-------------|-------|
| COUNTERS_PORT_NAME_MAP | `COUNTERS_PORT_NAME_MAP` (single hash) | Maps port name -> OID (e.g., `Ethernet0` -> `oid:0x100000000003a`) |
| COUNTERS | `COUNTERS:<oid>` | 41 SAI counter fields per port |

### SAI Counter Fields (per port)

All fields present in VS, grouped by what Watchtower collects:

**Collected by PortStatsCollector:**
| SAI Field | Friendly Name | Present in VS |
|-----------|---------------|:---:|
| SAI_PORT_STAT_IF_IN_OCTETS | rx_bytes | Yes |
| SAI_PORT_STAT_IF_OUT_OCTETS | tx_bytes | Yes |
| SAI_PORT_STAT_IF_IN_ERRORS | rx_errors | Yes |
| SAI_PORT_STAT_IF_OUT_ERRORS | tx_errors | Yes |
| SAI_PORT_STAT_IF_IN_DISCARDS | rx_drops | Yes |
| SAI_PORT_STAT_IF_OUT_DISCARDS | tx_drops | Yes |
| SAI_PORT_STAT_IF_IN_UCAST_PKTS | rx_packets | Yes |
| SAI_PORT_STAT_IF_OUT_UCAST_PKTS | tx_packets | Yes |
| SAI_PORT_STAT_ETHER_STATS_CRC_ALIGN_ERRORS | rx_crc_errors | **No** |

**Additional fields available in VS (not yet collected):**
- SAI_PORT_STAT_IF_IN_BROADCAST_PKTS, SAI_PORT_STAT_IF_IN_MULTICAST_PKTS
- SAI_PORT_STAT_IF_OUT_BROADCAST_PKTS, SAI_PORT_STAT_IF_OUT_MULTICAST_PKTS
- SAI_PORT_STAT_IF_IN_NON_UCAST_PKTS, SAI_PORT_STAT_IF_OUT_NON_UCAST_PKTS
- SAI_PORT_STAT_IF_OUT_QLEN
- SAI_PORT_STAT_ETHER_STATS_UNDERSIZE_PKTS, SAI_PORT_STAT_ETHER_STATS_FRAGMENTS
- SAI_PORT_STAT_ETHER_STATS_JABBERS
- SAI_PORT_STAT_ETHER_RX_OVERSIZE_PKTS, SAI_PORT_STAT_ETHER_TX_OVERSIZE_PKTS
- SAI_PORT_STAT_PFC_{0-7}_RX_PKTS, SAI_PORT_STAT_PFC_{0-7}_TX_PKTS
- SAI_PORT_STAT_TRIM_PACKETS, SAI_PORT_STAT_DROPPED_TRIM_PACKETS, SAI_PORT_STAT_TX_TRIM_PACKETS

## STATE_DB (Redis DB 6) — separator: `|`

987 keys total. Tables observed:

| Table | Key Pattern | Fields (sample) |
|-------|-------------|-----------------|
| PORT_TABLE | `PORT_TABLE\|<port_name>` | state, **netdev_oper_status**, admin_status, mtu, supported_speeds, host_tx_ready |
| TRANSCEIVER_DOM_SENSOR | `TRANSCEIVER_DOM_SENSOR\|<port_name>` | (absent on VS — no physical optics) |
| TRANSCEIVER_INFO | `TRANSCEIVER_INFO\|<port_name>` | (absent on VS — no physical optics) |
| FEATURE | `FEATURE\|<name>` | (service feature status) |
| DEVICE_METADATA | `DEVICE_METADATA\|localhost` | (device info) |
| REBOOT_CAUSE | `REBOOT_CAUSE\|*` | (reboot history) |

**Additional tables:** ACL_STAGE_CAPABILITY_TABLE, BUFFER_MAX_PARAM_TABLE,
COPP_GROUP_TABLE, COPP_TRAP_TABLE, DEBUG_COUNTER_CAPABILITIES,
FLOW_COUNTER_CAPABILITY_TABLE, NEIGH_STATE_TABLE, PORT_COUNTER_CAPABILITIES,
QUEUE_COUNTER_CAPABILITIES, SWITCH_CAPABILITY, WARM_RESTART_TABLE, and others.

## Discrepancies Found

### 1. STATE_DB oper_status field name (FIXED)

**Issue:** Our InterfaceStateCollector reads `oper_status` from STATE_DB, but the
actual field is `netdev_oper_status`. APPL_DB PORT_TABLE also has `oper_status`.

**Fix:** Read `oper_status` from APPL_DB (already available since the collector
reads APPL_DB for admin_status). Fall back to `netdev_oper_status` from STATE_DB.

### 2. CRC counter field missing from VS

**Issue:** `SAI_PORT_STAT_ETHER_STATS_CRC_ALIGN_ERRORS` is in our mock fixture but
not present on the VS. The VS SAI implementation (vslib) does not expose this
counter.

**Impact:** Low. The collector uses `.get(sai_name, "0")` so missing fields default
to 0. Real hardware ASICs (Memory, memory, Broadcom, etc.) do expose this field.
Our mock fixture is correct for real hardware. No code change needed.

### 3. Additional APPL_DB PORT_TABLE fields

**Observed:** `dhcp_rate_limit`, `flap_count`, `index`, `lanes` are present in real
APPL_DB but not in our mock. These are not currently collected. No action needed
unless we add them to InterfaceStateCollector later.

## Mock Fixture Updates

- `tests/mock_redis/state_db.json`: Added `netdev_oper_status` field to PORT_TABLE entries
- `watchtower/collectors/interface_state.py`: Updated to read `oper_status` from
  APPL_DB first, fall back to STATE_DB `netdev_oper_status`
