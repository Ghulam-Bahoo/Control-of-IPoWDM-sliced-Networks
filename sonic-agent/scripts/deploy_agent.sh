#!/bin/bash
#
# SONiC Agent Deployment Script
# Deploys agent on Edgecore SONiC switches with dynamic configuration
#

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default configuration
DEFAULT_POP_ID="pop1"
DEFAULT_ROUTER_ID="router1"
DEFAULT_VIRTUAL_OPERATOR="vOp2"
DEFAULT_KAFKA_BROKER="10.30.7.52:9092"
DEFAULT_LINKDB_URL="http://10.30.7.52:8000"

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
    print_success "Docker is available"
    
    # Check Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        # Try Docker Compose V2
        if ! docker compose version &> /dev/null; then
            print_error "Docker Compose is not installed"
            exit 1
        else
            DOCKER_COMPOSE_CMD="docker compose"
        fi
    else
        DOCKER_COMPOSE_CMD="docker-compose"
    fi
    print_success "Docker Compose is available"
    
    # Check Python
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 is not installed"
        exit 1
    fi
    print_success "Python 3 is available"
    
    # Check project structure
    if [ ! -f "$PROJECT_ROOT/docker-compose.yml" ]; then
        print_error "docker-compose.yml not found in $PROJECT_ROOT"
        exit 1
    fi
    
    if [ ! -f "$PROJECT_ROOT/scripts/configure_agent.sh" ]; then
        print_error "configure_agent.sh not found in $PROJECT_ROOT/scripts/"
        exit 1
    fi
    
    print_success "Project structure is valid"
    
    # Check Python dependencies
    print_info "Checking Python dependencies..."
    if python3 -c "import kafka, pydantic, requests" &> /dev/null; then
        print_success "Python dependencies are satisfied"
    else
        print_warning "Some Python dependencies missing, attempting to install..."
        if pip3 install kafka-python pydantic requests &> /dev/null; then
            print_success "Python dependencies installed"
        else
            print_error "Failed to install Python dependencies"
            exit 1
        fi
    fi
}

load_configuration() {
    print_info "Loading configuration..."
    
    # Source environment file if exists
    if [ -f "$PROJECT_ROOT/.env" ]; then
        print_info "Loading existing .env file"
        # Use grep to safely load variables
        while IFS='=' read -r key value; do
            if [[ ! "$key" =~ ^# ]] && [[ -n "$key" ]]; then
                # Remove quotes and comments from value
                value="${value%%#*}"
                value="${value%\"}"
                value="${value#\"}"
                value="${value%\'}"
                value="${value#\'}"
                export "$key=$value"
            fi
        done < "$PROJECT_ROOT/.env"
    fi
    
    # Set defaults if not provided
    POP_ID=${POP_ID:-$DEFAULT_POP_ID}
    ROUTER_ID=${ROUTER_ID:-$DEFAULT_ROUTER_ID}
    VIRTUAL_OPERATOR=${VIRTUAL_OPERATOR:-$DEFAULT_VIRTUAL_OPERATOR}
    KAFKA_BROKER=${KAFKA_BROKER:-$DEFAULT_KAFKA_BROKER}
    LINKDB_URL=${LINKDB_URL:-$DEFAULT_LINKDB_URL}
    
    print_info "Current configuration:"
    echo "  POP ID: $POP_ID"
    echo "  Router ID: $ROUTER_ID"
    echo "  Virtual Operator: $VIRTUAL_OPERATOR"
    echo "  Kafka Broker: $KAFKA_BROKER"
    echo "  LinkDB URL: $LINKDB_URL"
}

configure_agent() {
    print_info "Configuring agent..."
    
    # Run configuration script (bash, not python)
    if bash "$PROJECT_ROOT/scripts/configure_agent.sh" \
        --pop-id "$POP_ID" \
        --router-id "$ROUTER_ID" \
        --virtual-operator "$VIRTUAL_OPERATOR" \
        --kafka-broker "$KAFKA_BROKER" \
        --linkdb-url "$LINKDB_URL" \
        --force-discovery; then
        print_success "Agent configured successfully"
        
        # Reload configuration
        load_configuration
        
        # Show discovered interfaces
        if [ -n "${ASSIGNED_TRANSCEIVERS:-}" ]; then
            print_info "Discovered interfaces: $ASSIGNED_TRANSCEIVERS"
        else
            print_warning "No interfaces discovered or assigned"
        fi
    else
        print_error "Failed to configure agent"
        exit 1
    fi
}

build_agent_image() {
    print_info "Building agent Docker image..."
    
    # Check for Dockerfile
    if [ ! -f "$PROJECT_ROOT/Dockerfile" ]; then
        print_error "Dockerfile not found in $PROJECT_ROOT"
        exit 1
    fi
    
    # Build the image using host network (so apt and DNS behave like on the host)
    if docker build --network=host -t sonic-agent:latest "$PROJECT_ROOT"; then
        print_success "Agent image built successfully"
    else
        print_error "Failed to build agent image"
        exit 1
    fi
}

deploy_agent() {
    print_info "Deploying agent..."
    
    # Configure agent before deployment
    configure_agent
    
    # Stop existing agent if running
    print_info "Checking for existing agent..."
    if $DOCKER_COMPOSE_CMD -f "$PROJECT_ROOT/docker-compose.yml" ps | grep -q "sonic-agent"; then
        print_info "Stopping existing agent..."
        $DOCKER_COMPOSE_CMD -f "$PROJECT_ROOT/docker-compose.yml" down
        sleep 2
    fi
    
    # Start the agent
    print_info "Starting agent..."
    if $DOCKER_COMPOSE_CMD -f "$PROJECT_ROOT/docker-compose.yml" up -d; then
        print_success "Agent deployed successfully"
    else
        print_error "Failed to start agent"
        exit 1
    fi
    
    # Wait for agent to start
    print_info "Waiting for agent to start (30 seconds)..."
    sleep 30
    
    # Check if agent is running
    if $DOCKER_COMPOSE_CMD -f "$PROJECT_ROOT/docker-compose.yml" ps | grep -q "Up"; then
        print_success "Agent is running"
        
        # Get container ID
        CONTAINER_ID=$($DOCKER_COMPOSE_CMD -f "$PROJECT_ROOT/docker-compose.yml" ps -q)
        
        # Show initial logs
        print_info "Agent logs (last 10 lines):"
        docker logs --tail 10 "$CONTAINER_ID" 2>/dev/null || print_warning "Could not fetch logs"
        
    else
        print_error "Agent failed to start"
        print_info "Full logs:"
        $DOCKER_COMPOSE_CMD -f "$PROJECT_ROOT/docker-compose.yml" logs
        exit 1
    fi
}

test_connections() {
    print_info "Testing connections..."
    
    CONTAINER_ID=$($DOCKER_COMPOSE_CMD -f "$PROJECT_ROOT/docker-compose.yml" ps -q 2>/dev/null)
    
    if [ -z "$CONTAINER_ID" ]; then
        print_error "Agent container not found"
        return 1
    fi
    
    # Test 1: Container health
    print_info "Testing container health..."
    CONTAINER_HEALTH=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_ID" 2>/dev/null || echo "unknown")
    
    if [ "$CONTAINER_HEALTH" = "healthy" ]; then
        print_success "Container is healthy"
    elif [ "$CONTAINER_HEALTH" = "starting" ]; then
        print_warning "Container is still starting"
    else
        print_warning "Container health status: $CONTAINER_HEALTH"
    fi
    
    # Test 2: Kafka connection
    print_info "Testing Kafka connection..."
    if docker exec "$CONTAINER_ID" python3 -c "
import sys
sys.path.append('/app')
try:
    from kafka import KafkaProducer
    producer = KafkaProducer(bootstrap_servers='$KAFKA_BROKER', request_timeout_ms=10000)
    producer.flush()
    producer.close()
    print('SUCCESS: Kafka connection established')
    sys.exit(0)
except Exception as e:
    print(f'ERROR: Kafka connection failed: {e}')
    sys.exit(1)
" 2>/dev/null; then
        print_success "Kafka connection OK"
    else
        print_error "Kafka connection failed"
        return 1
    fi
    
    # Test 3: Hardware access
    print_info "Testing hardware access..."
    if docker exec "$CONTAINER_ID" python3 -c "
import sys
sys.path.append('/app')
try:
    from config.settings import settings
    from core.cmis_driver import CMISDriver
    
    # Initialize driver
    driver = CMISDriver(settings.interface_mappings, mock_mode=settings.MOCK_HARDWARE)
    
    # Check health
    healthy = driver.check_health()
    
    if healthy:
        print('SUCCESS: Hardware access established')
        sys.exit(0)
    else:
        print('WARNING: Hardware check failed or no interfaces healthy')
        sys.exit(0)  # Not fatal
except Exception as e:
    print(f'ERROR: Hardware access failed: {e}')
    sys.exit(1)
" 2>/dev/null; then
        print_success "Hardware access OK"
    else
        print_warning "Hardware access issues - check if running on SONiC switch"
    fi
    
    return 0
}

send_test_command() {
    print_info "Sending test command to agent..."
    
    # Check if kafka-console-producer is available
    if ! command -v kafka-console-producer &> /dev/null; then
        print_warning "kafka-console-producer not available, skipping command test"
        return 0
    fi
    
    # Create test health check command
    TEST_COMMAND=$(cat << EOF
{
    "action": "healthCheck",
    "parameters": {},
    "timestamp": $(date +%s)
}
EOF
    )
    
    # Send command to config topic
    CONFIG_TOPIC="config_$VIRTUAL_OPERATOR"
    
    print_info "Sending health check command to $CONFIG_TOPIC..."
    echo "$TEST_COMMAND" | kafka-console-producer \
        --broker-list "$KAFKA_BROKER" \
        --topic "$CONFIG_TOPIC" \
        --property "parse.key=true" \
        --property "key.separator=:" \
        --property "key=test-command-$(date +%s)"
    
    if [ $? -eq 0 ]; then
        print_success "Test command sent successfully"
        
        # Monitor for response (10 seconds)
        print_info "Monitoring for response (10 seconds)..."
        timeout 10 kafka-console-consumer \
            --bootstrap-server "$KAFKA_BROKER" \
            --topic "monitoring_$VIRTUAL_OPERATOR" \
            --from-beginning \
            --max-messages 1 \
            --timeout-ms 10000 2>/dev/null || true
    else
        print_warning "Failed to send test command"
    fi
}

show_deployment_info() {
    print_info "Deployment Information:"
    echo ""
    
    # Get container info
    CONTAINER_ID=$($DOCKER_COMPOSE_CMD -f "$PROJECT_ROOT/docker-compose.yml" ps -q 2>/dev/null)
    
    if [ -n "$CONTAINER_ID" ]; then
        echo "Container ID: $CONTAINER_ID"
        echo "Container Name: sonic-agent-$POP_ID-$ROUTER_ID"
        
        # Get status
        CONTAINER_STATUS=$(docker inspect -f '{{.State.Status}}' "$CONTAINER_ID" 2>/dev/null || echo "unknown")
        echo "Status: $CONTAINER_STATUS"
        
        # Get health
        CONTAINER_HEALTH=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_ID" 2>/dev/null || echo "unknown")
        echo "Health: $CONTAINER_HEALTH"
        
        # Get IP address (if using bridge network)
        CONTAINER_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$CONTAINER_ID" 2>/dev/null || echo "host network")
        echo "Network: $CONTAINER_IP"
        
        # Get creation time
        CREATED=$(docker inspect -f '{{.Created}}' "$CONTAINER_ID" 2>/dev/null || echo "unknown")
        echo "Created: $CREATED"
    else
        echo "Container: Not found"
    fi
    
    echo ""
    echo "Kafka Topics:"
    echo "  Commands: config_$VIRTUAL_OPERATOR"
    echo "  Telemetry: monitoring_$VIRTUAL_OPERATOR"
    echo "  Health: health_$VIRTUAL_OPERATOR"
    
    echo ""
    echo "Agent Identity:"
    echo "  Agent ID: ${POP_ID}-${ROUTER_ID}"
    echo "  POP: $POP_ID"
    echo "  Router: $ROUTER_ID"
    echo "  Virtual Operator: $VIRTUAL_OPERATOR"
    
    if [ -n "${ASSIGNED_TRANSCEIVERS:-}" ]; then
        echo ""
        echo "Assigned Interfaces:"
        echo "  $ASSIGNED_TRANSCEIVERS"
    fi
    
    echo ""
    echo "Management Commands:"
    echo "  View logs: $DOCKER_COMPOSE_CMD -f $PROJECT_ROOT/docker-compose.yml logs -f"
    echo "  Stop agent: $DOCKER_COMPOSE_CMD -f $PROJECT_ROOT/docker-compose.yml down"
    echo "  Restart agent: $DOCKER_COMPOSE_CMD -f $PROJECT_ROOT/docker-compose.yml restart"
    echo "  Shell access: docker exec -it $CONTAINER_ID /bin/bash"
    echo "  Test agent: docker exec $CONTAINER_ID python3 scripts/test_cmis.py"
    
    echo ""
    echo "Log Files:"
    echo "  Container logs: docker logs $CONTAINER_ID"
    echo "  Application logs: docker exec $CONTAINER_ID tail -f /var/log/sonic-agent/agent.log"
    echo "  Local logs: $PROJECT_ROOT/logs/"
}

cleanup() {
    print_info "Cleaning up..."
    
    # Remove any temporary files created during deployment
    rm -f /tmp/agent-test-* /tmp/kafka-test-* 2>/dev/null || true
    
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
            --linkdb-url)
                LINKDB_URL="$2"
                shift 2
                ;;
            --skip-build)
                SKIP_BUILD=true
                shift
                ;;
            --skip-tests)
                SKIP_TESTS=true
                shift
                ;;
            --help)
                echo "Usage: $0 [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --pop-id ID           Set POP ID (default: $DEFAULT_POP_ID)"
                echo "  --router-id ID        Set router ID (default: $DEFAULT_ROUTER_ID)"
                echo "  --virtual-operator OP Set virtual operator (default: $DEFAULT_VIRTUAL_OPERATOR)"
                echo "  --kafka-broker BROKER Set Kafka broker (default: $DEFAULT_KAFKA_BROKER)"
                echo "  --linkdb-url URL      Set Link Database URL (default: $DEFAULT_LINKDB_URL)"
                echo "  --skip-build          Skip Docker image building"
                echo "  --skip-tests          Skip connection tests"
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
    
    if [ "${SKIP_BUILD:-false}" != "true" ]; then
        build_agent_image
    else
        print_info "Skipping image build (--skip-build)"
    fi
    
    deploy_agent
    
    if [ "${SKIP_TESTS:-false}" != "true" ]; then
        test_connections
        send_test_command
    else
        print_info "Skipping tests (--skip-tests)"
    fi
    
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
