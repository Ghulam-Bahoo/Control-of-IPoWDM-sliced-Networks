#!/bin/bash
# deploy-link-db.sh
# Deployment script for Link Database

set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# Check if Docker is running
check_docker() {
    if ! docker info > /dev/null 2>&1; then
        print_error "Docker is not running. Please start Docker daemon."
        exit 1
    fi
    print_info "Docker is running"
}

# Check if required ports are available
check_ports() {
    local ports=("8000" "6379")
    for port in "${ports[@]}"; do
        if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null ; then
            print_warn "Port $port is already in use"
        fi
    done
}

# Build and start services
deploy_services() {
    print_info "Building Link Database image..."
    docker-compose build
    
    print_info "Starting services..."
    docker-compose up -d
    
    # Wait for services to be ready
    print_info "Waiting for services to be ready..."
    sleep 10
    
    # Check if services are running
    if docker-compose ps | grep -q "Up"; then
        print_info "Services are running"
    else
        print_error "Services failed to start"
        docker-compose logs
        exit 1
    fi
}

# Test the API
test_api() {
    print_info "Testing API connectivity..."
    
    # Wait a bit more for API to be ready
    sleep 5
    
    # Test root endpoint
    if curl -s http://localhost:8000/ | grep -q "IPoWDM Link Database"; then
        print_info "API is responding correctly"
    else
        print_error "API test failed"
        exit 1
    fi
    
    # Test health endpoint
    if curl -s http://localhost:8000/health | grep -q "healthy"; then
        print_info "Health check passed"
    else
        print_error "Health check failed"
        exit 1
    fi
}

# Create sample data
create_sample_data() {
    print_info "Creating sample data..."
    
    # Create POPs
    curl -X POST "http://localhost:8000/api/pops" \
        -H "Content-Type: application/json" \
        -d '{"pop_id":"pop1","name":"Data Center 1","location":"40.7128,-74.0060"}' \
        -s > /dev/null
    
    curl -X POST "http://localhost:8000/api/pops" \
        -H "Content-Type: application/json" \
        -d '{"pop_id":"pop2","name":"Data Center 2","location":"34.0522,-118.2437"}' \
        -s > /dev/null
    
    # Create link
    curl -X POST "http://localhost:8000/api/links" \
        -H "Content-Type: application/json" \
        -d '{"link_id":"link-pop1-pop2","pop_a":"pop1","pop_b":"pop2","distance_km":100.5}' \
        -s > /dev/null
    
    print_info "Sample data created"
}

# Show deployment info
show_info() {
    echo ""
    echo "========================================="
    echo "   LINK DATABASE DEPLOYMENT COMPLETE     "
    echo "========================================="
    echo ""
    echo "Services:"
    echo "---------"
    docker-compose ps
    echo ""
    echo "API Endpoints:"
    echo "--------------"
    echo "• API Root:      http://localhost:8000/"
    echo "• Health:        http://localhost:8000/health"
    echo "• API Docs:      http://localhost:8000/docs"
    echo "• Redoc:         http://localhost:8000/redoc"
    echo ""
    echo "Sample Commands:"
    echo "----------------"
    echo "1. View topology:"
    echo "   curl http://localhost:8000/api/topology | jq ."
    echo ""
    echo "2. Allocate connection:"
    echo '   curl -X POST "http://localhost:8000/api/connections/allocate" \'
    echo '     -H "Content-Type: application/json" \'
    echo '     -d '\''{"connection_id":"conn1","pop_a":"pop1","pop_b":"pop2","bandwidth":400,"virtual_operator":"vOp1"}'\'' | jq .'
    echo ""
    echo "3. View logs:"
    echo "   docker-compose logs -f link-database"
    echo ""
    echo "4. Stop services:"
    echo "   docker-compose down"
    echo ""
    echo "========================================="
}

# Main deployment flow
main() {
    print_info "Starting Link Database deployment..."
    
    check_docker
    check_ports
    deploy_services
    test_api
    create_sample_data
    show_info
    
    print_info "Deployment completed successfully!"
}

# Handle command line arguments
case "$1" in
    "up"|"start")
        main
        ;;
    "down"|"stop")
        print_info "Stopping services..."
        docker-compose down
        ;;
    "restart")
        print_info "Restarting services..."
        docker-compose restart
        ;;
    "logs")
        docker-compose logs -f
        ;;
    "status")
        docker-compose ps
        ;;
    "test")
        test_api
        ;;
    *)
        echo "Usage: $0 {up|down|restart|logs|status|test}"
        echo ""
        echo "Commands:"
        echo "  up, start    - Start services"
        echo "  down, stop   - Stop services"
        echo "  restart      - Restart services"
        echo "  logs         - View logs"
        echo "  status       - Check service status"
        echo "  test         - Test API connectivity"
        exit 1
        ;;
esac