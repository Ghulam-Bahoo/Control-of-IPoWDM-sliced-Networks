#!/usr/bin/env python3
"""
Health Check Script for SONiC Agent
Used by Docker healthcheck and manual monitoring.
"""

import os
import sys
import json
import time
import socket
import logging
from typing import Dict, Any, Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config.settings import settings
    from core.cmis_driver import CMISDriver
    from core.kafka_manager import KafkaManager
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)


class HealthChecker:
    """Comprehensive health checker for SONiC Agent."""
    
    def __init__(self):
        """Initialize health checker."""
        self.setup_logging()
        
        # Health check components
        self.components = [
            ('kafka_connection', self.check_kafka),
            ('hardware_access', self.check_hardware),
            ('telemetry_manager', self.check_telemetry),
            ('system_resources', self.check_system),
            ('network_connectivity', self.check_network),
        ]
        
        # Health state
        self.health_status = {}
        self.overall_healthy = False
        self.start_time = time.time()
    
    def setup_logging(self):
        """Setup logging for health checks."""
        logging.basicConfig(
            level=logging.WARNING,  # Health checks should be quiet
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger('health-check')
    
    def check_kafka(self) -> Dict[str, Any]:
        """Check Kafka connection and functionality."""
        check_result = {
            'healthy': False,
            'details': {},
            'errors': []
        }
        
        try:
            # Test Kafka connection
            kafka_config = settings.kafka_config
            kafka_manager = KafkaManager(kafka_config)
            
            # Check connection
            if not kafka_manager.check_connection():
                check_result['errors'].append('Kafka connection failed')
                return check_result
            
            # Get statistics
            stats = kafka_manager.get_stats()
            check_result['details'].update(stats)
            
            # Check if connected
            if stats.get('connected'):
                check_result['healthy'] = True
            else:
                check_result['errors'].append('Kafka not connected')
            
            # Cleanup
            kafka_manager.close()
            
        except Exception as e:
            check_result['errors'].append(f'Kafka check exception: {str(e)}')
            self.logger.error(f"Kafka health check failed: {e}")
        
        return check_result
    
    def check_hardware(self) -> Dict[str, Any]:
        """Check hardware access and transceiver status."""
        check_result = {
            'healthy': False,
            'details': {},
            'errors': []
        }
        
        try:
            # Initialize CMIS driver
            driver = CMISDriver(settings.hardware_config)
            
            # Check overall health
            hardware_ok = driver.check_health()
            check_result['healthy'] = hardware_ok
            
            # Check each interface
            interface_status = {}
            for interface in settings.assigned_transceivers:
                try:
                    status = driver.get_interface_status(interface)
                    interface_status[interface] = {
                        'present': status.get('present', False),
                        'operational': status.get('operational', False),
                        'module_state': status.get('module_state', 'unknown')
                    }
                    
                    if not status.get('present'):
                        check_result['errors'].append(f'{interface}: not present')
                    elif not status.get('operational'):
                        check_result['errors'].append(f'{interface}: not operational')
                        
                except Exception as e:
                    interface_status[interface] = {'error': str(e)}
                    check_result['errors'].append(f'{interface}: check failed: {str(e)}')
            
            check_result['details']['interfaces'] = interface_status
            
            # Count healthy interfaces
            healthy_interfaces = sum(
                1 for status in interface_status.values()
                if status.get('present') and status.get('operational')
            )
            
            check_result['details']['healthy_interface_count'] = healthy_interfaces
            check_result['details']['total_interfaces'] = len(interface_status)
            
            if healthy_interfaces == 0 and settings.assigned_transceivers:
                check_result['healthy'] = False
                check_result['errors'].append('No healthy interfaces found')
            
        except Exception as e:
            check_result['errors'].append(f'Hardware check exception: {str(e)}')
            self.logger.error(f"Hardware health check failed: {e}")
        
        return check_result
    
    def check_telemetry(self) -> Dict[str, Any]:
        """Check telemetry system health."""
        check_result = {
            'healthy': True,  # Default to true since telemetry is optional
            'details': {},
            'errors': []
        }
        
        try:
            # Check if telemetry manager module exists
            from core.telemetry_manager import TelemetryManager
            
            # We can't instantiate without all dependencies,
            # so we'll do a basic check
            check_result['details']['telemetry_system'] = 'available'
            
        except ImportError:
            check_result['details']['telemetry_system'] = 'not_available'
            check_result['healthy'] = True  # Not required for basic operation
            
        except Exception as e:
            check_result['errors'].append(f'Telemetry check exception: {str(e)}')
            check_result['healthy'] = False
        
        return check_result
    
    def check_system(self) -> Dict[str, Any]:
        """Check system resources."""
        check_result = {
            'healthy': True,
            'details': {},
            'errors': []
        }
        
        try:
            import psutil
            
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=0.1)
            check_result['details']['cpu_percent'] = cpu_percent
            
            if cpu_percent > 90:
                check_result['errors'].append(f'High CPU usage: {cpu_percent}%')
                check_result['healthy'] = False
            
            # Memory usage
            memory = psutil.virtual_memory()
            check_result['details']['memory_percent'] = memory.percent
            check_result['details']['memory_available_mb'] = memory.available / 1024 / 1024
            
            if memory.percent > 90:
                check_result['errors'].append(f'High memory usage: {memory.percent}%')
                check_result['healthy'] = False
            
            # Disk usage
            disk = psutil.disk_usage('/')
            check_result['details']['disk_percent'] = disk.percent
            
            if disk.percent > 95:
                check_result['errors'].append(f'High disk usage: {disk.percent}%')
                check_result['healthy'] = False
            
            # Process count
            process_count = len(psutil.pids())
            check_result['details']['process_count'] = process_count
            
        except ImportError:
            # psutil not available, skip detailed checks
            check_result['details']['system_checks'] = 'psutil_not_available'
            
        except Exception as e:
            check_result['errors'].append(f'System check exception: {str(e)}')
            check_result['healthy'] = False
        
        return check_result
    
    def check_network(self) -> Dict[str, Any]:
        """Check network connectivity."""
        check_result = {
            'healthy': True,
            'details': {},
            'errors': []
        }
        
        try:
            # Parse Kafka broker
            broker = settings.kafka_broker
            if ':' in broker:
                host, port = broker.split(':', 1)
                port = int(port)
            else:
                host = broker
                port = 9092
            
            # Test TCP connection
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            
            try:
                sock.connect((host, port))
                check_result['details']['kafka_connectivity'] = 'connected'
                sock.close()
            except (socket.timeout, ConnectionRefusedError) as e:
                check_result['errors'].append(f'Kafka connectivity failed: {e}')
                check_result['healthy'] = False
                check_result['details']['kafka_connectivity'] = 'failed'
            
            # Check DNS resolution
            try:
                socket.gethostbyname(host)
                check_result['details']['dns_resolution'] = 'ok'
            except socket.gaierror as e:
                check_result['errors'].append(f'DNS resolution failed: {e}')
                check_result['healthy'] = False
                check_result['details']['dns_resolution'] = 'failed'
            
        except Exception as e:
            check_result['errors'].append(f'Network check exception: {str(e)}')
            check_result['healthy'] = False
        
        return check_result
    
    def run_health_checks(self) -> Dict[str, Any]:
        """Run all health checks."""
        overall_healthy = True
        component_results = {}
        
        for component_name, check_func in self.components:
            try:
                result = check_func()
                component_results[component_name] = result
                
                if not result['healthy']:
                    overall_healthy = False
                    
            except Exception as e:
                component_results[component_name] = {
                    'healthy': False,
                    'error': f'Check execution failed: {str(e)}'
                }
                overall_healthy = False
                self.logger.error(f"Health check {component_name} failed: {e}")
        
        # Prepare final health status
        health_status = {
            'healthy': overall_healthy,
            'timestamp': time.time(),
            'uptime': time.time() - self.start_time,
            'agent_id': settings.agent_id,
            'pop_id': settings.pop_id,
            'router_id': settings.router_id,
            'virtual_operator': settings.virtual_operator,
            'components': component_results,
            'summary': {
                'total_checks': len(self.components),
                'healthy_checks': sum(1 for r in component_results.values() if r.get('healthy')),
                'failed_checks': sum(1 for r in component_results.values() if not r.get('healthy')),
            }
        }
        
        # Collect all errors
        all_errors = []
        for component, result in component_results.items():
            if 'errors' in result:
                for error in result['errors']:
                    all_errors.append(f"{component}: {error}")
        
        if all_errors:
            health_status['errors'] = all_errors
        
        self.health_status = health_status
        self.overall_healthy = overall_healthy
        
        return health_status
    
    def print_health_status(self, status: Dict[str, Any], verbose: bool = False):
        """Print health status in human-readable format."""
        if status['healthy']:
            print("✅ Agent is healthy")
        else:
            print("❌ Agent has issues")
        
        print(f"\nSummary:")
        print(f"  Agent: {status['agent_id']}")
        print(f"  POP: {status['pop_id']}")
        print(f"  Router: {status['router_id']}")
        print(f"  Virtual Operator: {status['virtual_operator']}")
        print(f"  Uptime: {status['uptime']:.1f}s")
        print(f"  Checks: {status['summary']['healthy_checks']}/{status['summary']['total_checks']} healthy")
        
        if verbose:
            print(f"\nComponent Details:")
            for component, result in status['components'].items():
                status_icon = "✅" if result.get('healthy') else "❌"
                print(f"  {component:20} {status_icon}")
                
                if 'details' in result and result['details']:
                    for key, value in result['details'].items():
                        print(f"    {key}: {value}")
        
        if 'errors' in status and status['errors']:
            print(f"\nErrors:")
            for error in status['errors']:
                print(f"  • {error}")
    
    def exit_with_status(self, status: Dict[str, Any]):
        """Exit with appropriate status code."""
        if status['healthy']:
            sys.exit(0)
        else:
            sys.exit(1)


def main():
    """Main entry point for health check."""
    import argparse
    
    parser = argparse.ArgumentParser(description='SONiC Agent Health Check')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    parser.add_argument('--json', '-j', action='store_true', help='Output JSON format')
    parser.add_argument('--timeout', '-t', type=int, default=30, help='Timeout in seconds')
    
    args = parser.parse_args()
    
    # Run health checks with timeout
    import signal
    
    def timeout_handler(signum, frame):
        print("Health check timeout", file=sys.stderr)
        sys.exit(1)
    
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(args.timeout)
    
    try:
        checker = HealthChecker()
        health_status = checker.run_health_checks()
        
        if args.json:
            print(json.dumps(health_status, indent=2))
        else:
            checker.print_health_status(health_status, verbose=args.verbose)
        
        checker.exit_with_status(health_status)
        
    except Exception as e:
        print(f"Health check failed: {e}", file=sys.stderr)
        sys.exit(1)
    
    finally:
        signal.alarm(0)  # Disable alarm


if __name__ == '__main__':
    main()