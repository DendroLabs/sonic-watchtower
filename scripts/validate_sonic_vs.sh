#!/bin/bash
# validate_sonic_vs.sh -- Pull SONiC VS image, dump Redis key patterns, and
# compare against Watchtower's mock fixtures.
#
# Usage:
#   ./scripts/validate_sonic_vs.sh
#
# Prerequisites:
#   - Docker installed and running
#   - ~5GB disk space for the SONiC VS image
#   - Internet access to pull the image
#
# Output:
#   docs/sonic_redis_keys_raw.txt -- Full key dump from all databases

set -euo pipefail

SONIC_IMAGE="docker-sonic-vs:latest"
CONTAINER_NAME="sonic-vs-validate"
OUTPUT_FILE="docs/sonic_redis_keys_raw.txt"

echo "=== SONiC VS Redis Key Validation ==="
echo ""

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed or not in PATH."
    exit 1
fi

# Pull the SONiC VS image if not already present
if ! docker image inspect "$SONIC_IMAGE" &> /dev/null; then
    echo "Pulling SONiC VS image (this may take a while)..."
    echo "Try: docker pull docker-sonic-vs:latest"
    echo "Or download from: https://sonic-build.azurewebsites.net/ui/sonic/pipelines"
    echo ""
    echo "If the image is not available, you can load it from a .gz file:"
    echo "  docker load -i sonic-vs.img.gz"
    exit 1
fi

# Clean up any previous container
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

echo "Starting SONiC VS container..."
docker run -d --name "$CONTAINER_NAME" "$SONIC_IMAGE"

echo "Waiting for Redis to be ready..."
for i in $(seq 1 30); do
    if docker exec "$CONTAINER_NAME" redis-cli -n 0 PING 2>/dev/null | grep -q PONG; then
        echo "Redis is ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "ERROR: Redis did not become ready within 30 seconds."
        docker rm -f "$CONTAINER_NAME"
        exit 1
    fi
    sleep 1
done

# Give SONiC services time to populate Redis
echo "Waiting 30s for SONiC services to populate databases..."
sleep 30

echo ""
echo "Dumping Redis key patterns..."
echo ""

{
    echo "================================================================"
    echo "SONiC VS Redis Key Dump"
    echo "Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo "Image: $SONIC_IMAGE"
    echo "================================================================"
    echo ""

    for db_num in 0 1 2 4 6; do
        case $db_num in
            0) db_name="APPL_DB" ;;
            1) db_name="ASIC_DB" ;;
            2) db_name="COUNTERS_DB" ;;
            4) db_name="CONFIG_DB" ;;
            6) db_name="STATE_DB" ;;
        esac

        echo "================================================================"
        echo "Database $db_num ($db_name)"
        echo "================================================================"
        echo ""

        # List all keys
        echo "--- Keys ---"
        docker exec "$CONTAINER_NAME" redis-cli -n "$db_num" KEYS '*' | sort
        echo ""

        # Count keys
        count=$(docker exec "$CONTAINER_NAME" redis-cli -n "$db_num" DBSIZE | grep -o '[0-9]*')
        echo "Total keys: $count"
        echo ""

        # Dump sample values for representative keys
        echo "--- Sample Values ---"
        keys=$(docker exec "$CONTAINER_NAME" redis-cli -n "$db_num" KEYS '*' | head -5)
        for key in $keys; do
            key_type=$(docker exec "$CONTAINER_NAME" redis-cli -n "$db_num" TYPE "$key" | tail -1)
            echo "Key: $key (type: $key_type)"
            if [ "$key_type" = "hash" ]; then
                docker exec "$CONTAINER_NAME" redis-cli -n "$db_num" HGETALL "$key" 2>/dev/null || true
            elif [ "$key_type" = "string" ]; then
                docker exec "$CONTAINER_NAME" redis-cli -n "$db_num" GET "$key" 2>/dev/null || true
            fi
            echo ""
        done
        echo ""
    done
} > "$OUTPUT_FILE"

echo "Key dump saved to: $OUTPUT_FILE"
echo ""

# Clean up
echo "Stopping SONiC VS container..."
docker rm -f "$CONTAINER_NAME" > /dev/null

echo ""
echo "Done. Review $OUTPUT_FILE and compare against tests/mock_redis/ fixtures."
echo "Document findings in docs/sonic_redis_keys.md"
