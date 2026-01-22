#!/bin/bash
#
# Agent Configuration Script
# Dynamically configures agent based on hardware and LinkDB assignments
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"

# Default configuration
LINKDB_URL="http://10.30.7.52:8000"

# Minimal logging helpers (no bash color escapes to avoid weird terminals)
print_info() { echo "[INFO] $1"; }
print_warn() { echo "[WARN] $1" >&2; }
print_error() { echo "[ERROR] $1" >&2; }

# Load existing .env if exists (safe-ish)
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    print_info "Loaded existing configuration"
else
    print_info "Creating new configuration"
fi

FORCE_DISCOVERY=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --pop-id) POP_ID="$2"; shift 2 ;;
        --router-id) ROUTER_ID="$2"; shift 2 ;;
        --virtual-operator) VIRTUAL_OPERATOR="$2"; shift 2 ;;
        --kafka-broker) KAFKA_BROKER="$2"; shift 2 ;;
        --linkdb-url) LINKDB_URL="$2"; shift 2 ;;
        --force-discovery) FORCE_DISCOVERY=true; shift ;;
        --help)
            cat <<'USAGE'
Usage: ./configure_agent.sh [OPTIONS]

Options:
  --pop-id ID            Set POP ID (required)
  --router-id ID         Set router ID (required)
  --virtual-operator OP  Set virtual operator (required)
  --kafka-broker HOST:PORT  Set Kafka broker URL
  --linkdb-url URL       Set Link Database URL
  --force-discovery      Force hardware discovery
USAGE
            exit 0
            ;;
        *) print_error "Unknown option: $1"; exit 1 ;;
    esac
done

# Validate required parameters
if [ -z "${POP_ID:-}" ] || [ -z "${ROUTER_ID:-}" ] || [ -z "${VIRTUAL_OPERATOR:-}" ]; then
    print_error "Missing required parameters. Use --pop-id, --router-id, --virtual-operator"
    exit 1
fi

print_info "Configuring agent for:"
print_info "  POP: ${POP_ID}"
print_info "  Router: ${ROUTER_ID}"
print_info "  Virtual Operator: ${VIRTUAL_OPERATOR}"
print_info "  Kafka Broker: ${KAFKA_BROKER:-10.30.7.52:9092}"
print_info "  LinkDB URL: ${LINKDB_URL}"

AGENT_ID="${POP_ID}-${ROUTER_ID}"

# Discover hardware if needed or forced
if [ -z "${ASSIGNED_TRANSCEIVERS:-}" ] || [ "${FORCE_DISCOVERY}" = true ]; then
    print_info "Discovering hardware..."

    DISCOVERY_OUTPUT="$(
        python3 "$SCRIPT_DIR/discover_hardware.py" \
            --linkdb-url "$LINKDB_URL" \
            --vop-id "$VIRTUAL_OPERATOR" \
            --pop-id "$POP_ID" \
            --router-id "$ROUTER_ID" \
            --output env
    )"

    # Extract lines
    ASSIGNED_TRANSCEIVERS="$(printf "%s\n" "$DISCOVERY_OUTPUT" | awk -F= '/^ASSIGNED_TRANSCEIVERS=/{print substr($0, index($0,$2))}')"
    IFNAME_TO_PORTNUM_JSON="$(printf "%s\n" "$DISCOVERY_OUTPUT" | awk -F= '/^IFNAME_TO_PORTNUM_JSON=/{print substr($0, index($0,$2))}')"

    if [ -z "${ASSIGNED_TRANSCEIVERS:-}" ] || [ -z "${IFNAME_TO_PORTNUM_JSON:-}" ]; then
        print_error "Discovery did not return ASSIGNED_TRANSCEIVERS and IFNAME_TO_PORTNUM_JSON"
        print_error "Raw discovery output:"
        printf "%s\n" "$DISCOVERY_OUTPUT" >&2
        exit 1
    fi

    print_info "Discovered interfaces: ${ASSIGNED_TRANSCEIVERS}"
else
    print_info "Using existing interface configuration"
fi

# Validate JSON strictly (no bash interpolation inside python)
python3 - "$ASSIGNED_TRANSCEIVERS" "$IFNAME_TO_PORTNUM_JSON" <<'PY'
import json, sys

assigned = sys.argv[1]
mapping = sys.argv[2]

def check(label, s, expected):
    try:
        obj = json.loads(s)
    except Exception as e:
        print(f"[ERROR] {label} is invalid JSON: {e}", file=sys.stderr)
        print(f"[ERROR] {label} value: {s}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(obj, expected):
        print(f"[ERROR] {label} must be {expected.__name__}, got {type(obj).__name__}", file=sys.stderr)
        sys.exit(1)
    return obj

a = check("ASSIGNED_TRANSCEIVERS", assigned, list)
m = check("IFNAME_TO_PORTNUM_JSON", mapping, dict)

# Basic sanity: each assigned iface has a mapping
missing = [x for x in a if x not in m]
if missing:
    print(f"[WARN] Missing mappings for: {missing}", file=sys.stderr)

print("[INFO] JSON validation OK", file=sys.stderr)
PY

# Kafka topics
CONFIG_TOPIC="config_${VIRTUAL_OPERATOR}"
MONITORING_TOPIC="monitoring_${VIRTUAL_OPERATOR}"
HEALTH_TOPIC="health_${VIRTUAL_OPERATOR}"

# Write .env
print_info "Writing configuration to $ENV_FILE"

cat > "$ENV_FILE" <<EOF
# SONiC Agent Configuration - Auto-generated on $(date)

# Required Identity
POP_ID=${POP_ID}
ROUTER_ID=${ROUTER_ID}
VIRTUAL_OPERATOR=${VIRTUAL_OPERATOR}
AGENT_ID=${AGENT_ID}

# Kafka Configuration
KAFKA_BROKER=${KAFKA_BROKER:-10.30.7.52:9092}
CONFIG_TOPIC=${CONFIG_TOPIC}
MONITORING_TOPIC=${MONITORING_TOPIC}
HEALTH_TOPIC=${HEALTH_TOPIC}

# Hardware Configuration (Auto-discovered)
ASSIGNED_TRANSCEIVERS=${ASSIGNED_TRANSCEIVERS}
IFNAME_TO_PORTNUM_JSON=${IFNAME_TO_PORTNUM_JSON}

# Operational Settings
TELEMETRY_INTERVAL_SEC=${TELEMETRY_INTERVAL_SEC:-3.0}
COMMAND_TIMEOUT_SEC=${COMMAND_TIMEOUT_SEC:-30}
MAX_TELEMETRY_SESSIONS=${MAX_TELEMETRY_SESSIONS:-10}
ENABLE_QOT_MONITORING=${ENABLE_QOT_MONITORING:-true}
QOT_SAMPLES=${QOT_SAMPLES:-3}
QOT_COOLDOWN_SEC=${QOT_COOLDOWN_SEC:-20}
OSNR_THRESHOLD_DB=${OSNR_THRESHOLD_DB:-18.0}
BER_THRESHOLD=${BER_THRESHOLD:-0.001}

# Logging
LOG_LEVEL=${LOG_LEVEL:-INFO}
LOG_FILE=${LOG_FILE:-/var/log/sonic-agent/agent.log}
LOG_MAX_SIZE_MB=${LOG_MAX_SIZE_MB:-10}
LOG_BACKUP_COUNT=${LOG_BACKUP_COUNT:-5}

# Debug
DEBUG_MODE=${DEBUG_MODE:-false}
MOCK_HARDWARE=${MOCK_HARDWARE:-false}
EOF

print_info "Configuration saved successfully"
print_info ""
print_info "Summary:"
print_info "  Agent ID: ${AGENT_ID}"
print_info "  Kafka Topics:"
print_info "    Commands: ${CONFIG_TOPIC}"
print_info "    Telemetry: ${MONITORING_TOPIC}"
print_info "    Health: ${HEALTH_TOPIC}"
print_info "  Assigned Interfaces: ${ASSIGNED_TRANSCEIVERS}"

