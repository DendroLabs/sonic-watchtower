# SONiC Redis Key Patterns

This document records the actual Redis key patterns observed in a SONiC VS
(virtual switch) instance, and how they compare to Watchtower's mock fixtures.

**Status:** Not yet validated. Run `scripts/validate_sonic_vs.sh` to populate.

## How to Validate

```bash
# 1. Get the SONiC VS image (download or build)
# 2. Run the validation script
./scripts/validate_sonic_vs.sh

# 3. Review the raw dump
cat docs/sonic_redis_keys_raw.txt

# 4. Update this document with findings
# 5. Fix any mock fixture mismatches
```

## Expected Key Patterns

Based on SONiC documentation and our current mock fixtures:

### APPL_DB (Redis DB 0) — separator: `:`

| Table | Key Pattern | Used By |
|-------|-------------|---------|
| PORT_TABLE | `PORT_TABLE:<port_name>` | InterfaceStateCollector |
| LLDP_ENTRY_TABLE | `LLDP_ENTRY_TABLE:<port_name>` | LLDPTopologyCollector |
| BGP_NEIGHBOR_TABLE | `BGP_NEIGHBOR_TABLE:<neighbor_ip>` | BGPStateCollector |
| ROUTE_TABLE | `ROUTE_TABLE:<prefix>` | (future use) |

### COUNTERS_DB (Redis DB 2) — separator: `:`

| Table | Key Pattern | Used By |
|-------|-------------|---------|
| COUNTERS_PORT_NAME_MAP | `COUNTERS_PORT_NAME_MAP` (single key) | PortStatsCollector |
| COUNTERS | `COUNTERS:<oid>` | PortStatsCollector |

### STATE_DB (Redis DB 6) — separator: `|`

| Table | Key Pattern | Used By |
|-------|-------------|---------|
| PORT_TABLE | `PORT_TABLE\|<port_name>` | InterfaceStateCollector |
| TRANSCEIVER_DOM_SENSOR | `TRANSCEIVER_DOM_SENSOR\|<port_name>` | OpticHealthCollector |
| TRANSCEIVER_INFO | `TRANSCEIVER_INFO\|<port_name>` | OpticHealthCollector |

## Validation Results

_To be filled in after running `validate_sonic_vs.sh`._

### Discrepancies Found

_None yet — document any key pattern or field name mismatches here._

### Mock Fixture Updates

_List any changes made to `tests/mock_redis/*.json` files after validation._
