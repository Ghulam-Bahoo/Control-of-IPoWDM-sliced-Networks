#!/bin/bash
#
# SONiC Agent Deployment Script
# Deploys the SONiC agent on Edgecore switches
#

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CONFIG_DIR="$PROJECT_ROOT/config"
LOG_DIR="$PROJECT_ROOT/logs"
DATA_DIR="$PROJECT_ROOT/data"

# Default configuration
DEFAULT_POP_ID="pop1"
DEFAULT_ROUTER_ID="router1"
DEFAULT_VIRTUAL_OPERATOR="vOp2"
DEFAULT_KAFKA_BROKER="10.30.7.52:9092"
DEFAULT_INTERFACE="Ethernet192"
DEFAULT_PORT_NUM="192"

print_header() {
    echo -e "${BLUE}"
    echo "=========================================="
    echo "  SONiC Agent Deployment"
    echo "=========================================="
    echo -e "${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}➜ $1${NC}"
}

check_prerequisites() {
    print_info "Checking prerequisites..."
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed"
        exit 1
    fi
    print_success "Docker is installed"
    
    # Check Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose is not installed"
        exit 1
    fi
    print_success "Docker Compose is installed"
    
    # Check Python
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 is not installed"
        exit 1
    fi
    print_success "Python 3 is installed"
    
    # Check project structure
    if [ ! -f "$PROJECT_ROOT/docker-compose.yml" ]; then
        print_error "docker-compose.yml not found in $PROJECT_ROOT"
        exit 1
    fi
    print_success "Project structure is valid"
    
    # Create directories
    mkdir -p "$LOG_DIR" "$DATA_DIR"
    print_success "Created required directories"
}

load_configuration() {
    print_info "Loading configuration..."
    
    # Check for environment file
    if [ -f "$CONFIG_DIR/.env" ]; then
        print_info "Loading environment from $CONFIG_DIR/.env"
        source "$CONFIG_DIR/.env"
    elif [ -f ".env" ]; then
        print_info "Loading environment from .env"
        source ".env"
    else
        print_warning "No .env file found, using defaults"
    fi
    
    # Set defaults if not provided
    POP_ID=${POP_ID:-$DEFAULT_POP_ID}
    ROUTER_ID=${ROUTER_ID:-$DEFAULT_ROUTER_ID}
    VIRTUAL_OPERATOR=${VIRTUAL_OPERATOR:-$DEFAULT_VIRTUAL_OPERATOR}
    KAFKA_BROKER=${KAFKA_BROKER:-$DEFAULT_KAFKA_BROKER}
    ASSIGNED_TRANSCEIVERS=${ASSIGNED_TRANSCEIVERS:-$DEFAULT_INTERFACE}
    
    # Generate interface mapping if not provided
    if [ -z "${IFNAME_TO_PORTNUM_JSON:-}" ]; then
        IFNAME_TO_PORTNUM_JSON="{\"$DEFAULT_INTERFACE\": $DEFAULT_PORT_NUM}"
        print_info "Generated interface mapping: $IFNAME_TO_PORTNUM_JSON"
    fi
    
    # Export variables for Docker Compose
    export POP_ID ROUTER_ID VIRTUAL_OPERATOR KAFKA_BROKER
    export ASSIGNED_TRANSCEIVERS IFNAME_TO_PORTNUM_JSON
    
    print_info "Configuration:"
    echo "  POP_ID: $POP_ID"
    echo "  ROUTER_ID: $ROUTER_ID"
    echo "  VIRTUAL_OPERATOR: $VIRTUAL_OPERATOR"
    echo "  KAFKA_BROKER: $KAFKA_BROKER"
    echo "  ASSIGNED_TRANSCEIVERS: $ASSIGNED_TRANSCEIVERS"
    echo "  IFNAME_TO_PORTNUM_JSON: $IFNAME_TO_PORTNUM_JSON"
}

build_agent_image() {
    print_info "Building agent Docker image..."
    
    # Check for Dockerfile
    if [ ! -f "$PROJECT_ROOT/Dockerfile.sonic-agent" ]; then
        print_error "Dockerfile.sonic-agent not found"
        exit 1
    fi
    
    # Build the image
    docker build \
        -f "$PROJECT_ROOT/Dockerfile.sonic-agent" \
        -t sonic-agent:latest \
        "$PROJECT_ROOT"
    
    if [ $? -eq 0 ]; then
        print_success "Agent image built successfully"
    else
        print_error "Failed to build agent image"
        exit 1
    fi
}

deploy_agent() {
    print_info "Deploying SONiC agent..."
    
    # Stop existing agent if running
    if docker-compose -f "$PROJECT_ROOT/docker-compose.yml" ps | grep -q "sonic-agent"; then
        print_info "Stopping existing agent..."
        docker-compose -f "$PROJECT_ROOT/docker-compose.yml" down
    fi
    
    # Start the agent
    print_info "Starting agent with Docker Compose..."
    docker-compose -f "$PROJECT_ROOT/docker-compose.yml" up -d
    
    # Wait for agent to start
    print_info "Waiting for agent to start (30 seconds)..."
    sleep 30
    
    # Check if agent is running
    if docker-compose -f "$PROJECT_ROOT/docker-compose.yml" ps | grep -q "Up"; then
        print_success "Agent deployed successfully"
        
        # Get container ID
        CONTAINER_ID=$(docker-compose -f "$PROJECT_ROOT/docker-compose.yml" ps -q)
        
        # Show logs
        print_info "Agent logs (last 10 lines):"
        docker logs --tail 10 "$CONTAINER_ID"
        
    else
        print_error "Agent failed to start"
        docker-compose -f "$PROJECT_ROOT/docker-compose.yml" logs
        exit 1
    fi
}

test_connection() {
    print_info "Testing Kafka connection..."
    
    # Create a test Python script
    TEST_SCRIPT=$(mktemp)
    cat > "$TEST_SCRIPT" << 'EOF'
#!/usr/bin/env python3
"""
Test Kafka connection from agent container.
"""
import json
import time
from kafka import KafkaProducer

def test_kafka_connection(broker):
    try:
        producer = KafkaProducer(
            bootstrap_servers=broker,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            request_timeout_ms=10000
        )
        
        # Send test message
        test_msg = {
            "test": True,
            "timestamp": time.time(),
            "message": "Kafka connection test"
        }
        
        future = producer.send('test_topic', value=test_msg)
        future.get(timeout=10)
        
        producer.flush()
        producer.close()
        
        return True
        
    except Exception as e:
        print(f"Kafka connection test failed: {e}")
        return False

if __name__ == "__main__":
    import sys
    broker = sys.argv[1] if len(sys.argv) > 1 else "10.30.7.52:9092"
    
    if test_kafka_connection(broker):
        print("SUCCESS: Kafka connection test passed")
        sys.exit(0)
    else:
        print("ERROR: Kafka connection test failed")
        sys.exit(1)
EOF
    
    # Run test inside agent container
    CONTAINER_ID=$(docker-compose -f "$PROJECT_ROOT/docker-compose.yml" ps -q)
    
    if [ -z "$CONTAINER_ID" ]; then
        print_error "Agent container not found"
        rm -f "$TEST_SCRIPT"
        exit 1
    fi
    
    # Copy test script to container
    docker cp "$TEST_SCRIPT" "$CONTAINER_ID:/tmp/test_kafka.py"
    
    # Run test
    if docker exec "$CONTAINER_ID" python3 /tmp/test_kafka.py "$KAFKA_BROKER"; then
        print_success "Kafka connection test passed"
    else
        print_error "Kafka connection test failed"
        rm -f "$TEST_SCRIPT"
        exit 1
    fi
    
    rm -f "$TEST_SCRIPT"
}

test_hardware_access() {
    print_info "Testing hardware access..."
    
    CONTAINER_ID=$(docker-compose -f "$PROJECT_ROOT/docker-compose.yml" ps -q)
    
    # Test SFP presence
    print_info "Testing SFP presence detection..."
    if docker exec "$CONTAINER_ID" python3 -c "
import sys
sys.path.append('/app')
try:
    from core.cmis_driver import CMISDriver
    from config.settings import settings
    
    driver = CMISDriver(settings.hardware_config)
    
    for interface in settings.assigned_transceivers:
        sfp = driver._get_sfp(interface)
        if sfp:
            present = sfp.get_presence()
            print(f'{interface}: {''present'' if present else ''not present''}')
        else:
            print(f'{interface}: SFP object not found')
    
    print('SUCCESS: Hardware access test completed')
    
except Exception as e:
    print(f'ERROR: {e}')
    sys.exit(1)
"; then
        print_success "Hardware access test completed"
    else
        print_warning "Hardware access test issues detected"
    fi
}

check_agent_health() {
    print_info "Checking agent health..."
    
    CONTAINER_ID=$(docker-compose -f "$PROJECT_ROOT/docker-compose.yml" ps -q)
    
    # Wait for health check
    print_info "Waiting for agent health check (15 seconds)..."
    sleep 15
    
    # Check container health
    HEALTH_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_ID" 2>/dev/null || echo "unknown")
    
    if [ "$HEALTH_STATUS" = "healthy" ]; then
        print_success "Agent container is healthy"
    elif [ "$HEALTH_STATUS" = "starting" ]; then
        print_warning "Agent container is still starting"
    else
        print_warning "Agent container health status: $HEALTH_STATUS"
        
        # Show recent logs
        print_info "Recent agent logs:"
        docker logs --tail 20 "$CONTAINER_ID"
    fi
}

send_test_command() {
    print_info "Sending test command to agent..."
    
    # Create test command
    TEST_COMMAND=$(mktemp)
    cat > "$TEST_COMMAND" << EOF
{
    "action": "healthCheck",
    "command_id": "deploy-test-$(date +%s)",
    "target_pop": "$POP_ID",
    "parameters": {},
    "timestamp": $(date +%s)
}
EOF
    
    # Send command using kafka-console-producer if available
    if command -v kafka-console-producer &> /dev/null; then
        print_info "Sending health check command via Kafka..."
        
        # Extract broker host and port
        BROKER_HOST=$(echo "$KAFKA_BROKER" | cut -d: -f1)
        BROKER_PORT=$(echo "$KAFKA_BROKER" | cut -d: -f2)
        
        cat "$TEST_COMMAND" | kafka-console-producer \
            --broker-list "$KAFKA_BROKER" \
            --topic "config_$VIRTUAL_OPERATOR"
        
        if [ $? -eq 0 ]; then
            print_success "Test command sent successfully"
            
            # Monitor for response
            print_info "Monitoring for response (30 seconds)..."
            timeout 30 kafka-console-consumer \
                --bootstrap-server "$KAFKA_BROKER" \
                --topic "monitoring_$VIRTUAL_OPERATOR" \
                --from-beginning \
                --max-messages 1 || true
        else
            print_warning "Failed to send test command"
        fi
    else
        print_warning "kafka-console-producer not available, skipping command test"
    fi
    
    rm -f "$TEST_COMMAND"
}

show_deployment_info() {
    print_info "Deployment Information:"
    echo ""
    
    # Get container info
    CONTAINER_ID=$(docker-compose -f "$PROJECT_ROOT/docker-compose.yml" ps -q)
    
    if [ -n "$CONTAINER_ID" ]; then
        echo "Container ID: $CONTAINER_ID"
        echo "Container Name: sonic-agent-$POP_ID-$ROUTER_ID"
        
        # Get IP address (if using bridge network)
        CONTAINER_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$CONTAINER_ID" 2>/dev/null || echo "host network")
        echo "Network: $CONTAINER_IP"
        
        # Get status
        CONTAINER_STATUS=$(docker inspect -f '{{.State.Status}}' "$CONTAINER_ID")
        echo "Status: $CONTAINER_STATUS"
        
        # Get ports
        PORTS=$(docker port "$CONTAINER_ID" 2>/dev/null || echo "No ports exposed")
        echo "Ports: $PORTS"
    fi
    
    echo ""
    echo "Kafka Topics:"
    echo "  Commands: config_$VIRTUAL_OPERATOR"
    echo "  Telemetry: monitoring_$VIRTUAL_OPERATOR"
    echo "  Health: health_$VIRTUAL_OPERATOR"
    
    echo ""
    echo "Log Files:"
    echo "  Container logs: docker logs $CONTAINER_ID"
    echo "  Application logs: $LOG_DIR/"
    
    echo ""
    echo "Management Commands:"
    echo "  Stop agent: docker-compose -f $PROJECT_ROOT/docker-compose.yml down"
    echo "  View logs: docker-compose -f $PROJECT_ROOT/docker-compose.yml logs -f"
    echo "  Restart agent: docker-compose -f $PROJECT_ROOT/docker-compose.yml restart"
}

cleanup() {
    print_info "Cleaning up..."
    
    # Remove temporary files
    rm -f /tmp/test_kafka.py /tmp/test_command.json 2>/dev/null || true
    
    print_success "Cleanup completed"
}

main() {
    print_header
    
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --pop-id)
                POP_ID="$2"
                shift 2
                ;;
            --router-id)
                ROUTER_ID="$2"
                shift 2
                ;;
            --virtual-operator)
                VIRTUAL_OPERATOR="$2"
                shift 2
                ;;
            --kafka-broker)
                KAFKA_BROKER="$2"
                shift 2
                ;;
            --interface)
                ASSIGNED_TRANSCEIVERS="$2"
                shift 2
                ;;
            --port-num)
                DEFAULT_PORT_NUM="$2"
                shift 2
                ;;
            --help)
                echo "Usage: $0 [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --pop-id ID           Set POP ID (default: $DEFAULT_POP_ID)"
                echo "  --router-id ID        Set router ID (default: $DEFAULT_ROUTER_ID)"
                echo "  --virtual-operator OP Set virtual operator (default: $DEFAULT_VIRTUAL_OPERATOR)"
                echo "  --kafka-broker BROKER Set Kafka broker (default: $DEFAULT_KAFKA_BROKER)"
                echo "  --interface IFACE     Set interface name (default: $DEFAULT_INTERFACE)"
                echo "  --port-num NUM        Set port number (default: $DEFAULT_PORT_NUM)"
                echo "  --help                Show this help message"
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                echo "Use --help for usage information"
                exit 1
                ;;
        esac
    done
    
    # Run deployment steps
    check_prerequisites
    load_configuration
    build_agent_image
    deploy_agent
    test_connection
    test_hardware_access
    check_agent_health
    send_test_command
    show_deployment_info
    cleanup
    
    echo ""
    print_success "SONiC agent deployment completed successfully!"
    echo ""
    
    exit 0
}

# Handle script termination
trap 'print_error "Deployment interrupted"; cleanup; exit 1' INT TERM

# Run main function
main "$@"