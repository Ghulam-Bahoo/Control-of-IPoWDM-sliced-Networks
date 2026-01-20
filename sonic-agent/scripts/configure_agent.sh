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
LINKDB_URL="http://10.30.7.52:8000"  # Link Database API

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

print_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Load existing .env if exists
if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
    print_info "Loaded existing configuration"
else
    print_info "Creating new configuration"
fi

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
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --pop-id ID           Set POP ID (required)"
            echo "  --router-id ID        Set router ID (required)"
            echo "  --virtual-operator OP Set virtual operator (required)"
            echo "  --kafka-broker URL    Set Kafka broker URL"
            echo "  --linkdb-url URL      Set Link Database URL"
            echo "  --force-discovery     Force hardware discovery"
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
print_info "  POP: $POP_ID"
print_info "  Router: $ROUTER_ID"
print_info "  Virtual Operator: $VIRTUAL_OPERATOR"

# Generate agent ID
AGENT_ID="${POP_ID}-${ROUTER_ID}"

# Discover hardware if needed or forced
if [ -z "${ASSIGNED_TRANSCEIVERS:-}" ] || [ "${FORCE_DISCOVERY:-false}" = true ]; then
    print_info "Discovering hardware..."
    
    # Run discovery script
    DISCOVERY_OUTPUT=$(python3 "$SCRIPT_DIR/discover_hardware.py" \
        --linkdb-url "$LINKDB_URL" \
        --vop-id "$VIRTUAL_OPERATOR" \
        --pop-id "$POP_ID" \
        --router-id "$ROUTER_ID" \
        --output env)
    
    # Parse output
    while IFS= read -r line; do
        if [[ "$line" =~ ^ASSIGNED_TRANSCEIVERS= ]]; then
            ASSIGNED_TRANSCEIVERS="${line#ASSIGNED_TRANSCEIVERS=}"
        elif [[ "$line" =~ ^IFNAME_TO_PORTNUM_JSON= ]]; then
            IFNAME_TO_PORTNUM_JSON="${line#IFNAME_TO_PORTNUM_JSON=}"
        fi
    done <<< "$DISCOVERY_OUTPUT"
    
    print_info "Discovered interfaces: $ASSIGNED_TRANSCEIVERS"
else
    print_info "Using existing interface configuration"
fi

# Set Kafka topics
CONFIG_TOPIC="config_$VIRTUAL_OPERATOR"
MONITORING_TOPIC="monitoring_$VIRTUAL_OPERATOR"
HEALTH_TOPIC="health_$VIRTUAL_OPERATOR"

# Write configuration to .env file
print_info "Writing configuration to $ENV_FILE"

cat > "$ENV_FILE" << EOF
# SONiC Agent Configuration - Auto-generated on $(date)

# Required Identity
POP_ID=$POP_ID
ROUTER_ID=$ROUTER_ID
VIRTUAL_OPERATOR=$VIRTUAL_OPERATOR
AGENT_ID=$AGENT_ID

# Kafka Configuration
KAFKA_BROKER=${KAFKA_BROKER:-10.30.7.52:9092}
CONFIG_TOPIC=$CONFIG_TOPIC
MONITORING_TOPIC=$MONITORING_TOPIC
HEALTH_TOPIC=$HEALTH_TOPIC

# Hardware Configuration (Auto-discovered)
ASSIGNED_TRANSCEIVERS=${ASSIGNED_TRANSCEIVERS:-[]}
IFNAME_TO_PORTNUM_JSON=${IFNAME_TO_PORTNUM_JSON:-{}}

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
print_info "  Agent ID: $AGENT_ID"
print_info "  Kafka Topics:"
print_info "    Commands: $CONFIG_TOPIC"
print_info "    Telemetry: $MONITORING_TOPIC"
print_info "    Health: $HEALTH_TOPIC"
print_info "  Assigned Interfaces: $ASSIGNED_TRANSCEIVERS"

# Validate configuration
if [ -z "$ASSIGNED_TRANSCEIVERS" ]; then
    print_error "Warning: No interfaces assigned. Agent may not function properly."
fi
