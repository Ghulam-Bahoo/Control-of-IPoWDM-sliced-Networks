#!/usr/bin/env python3
"""
Integration Test Suite for SONiC Agent
Tests complete functionality from command to hardware response.
"""

import os
import sys
import json
import time
import logging
import argparse
from typing import Dict, Any, List, Optional
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import NoBrokersAvailable


class IntegrationTest:
    """Comprehensive integration test for SONiC Agent."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize test suite."""
        self.config = config
        self.setup_logging()
        
        # Test configuration
        self.pop_id = config.get('pop_id', 'pop1')
        self.router_id = config.get('router_id', 'router1')
        self.virtual_operator = config.get('virtual_operator', 'vOp2')
        self.kafka_broker = config.get('kafka_broker', '10.30.7.52:9092')
        self.test_interface = config.get('test_interface', 'Ethernet192')
        
        # Kafka topics
        self.config_topic = f"config_{self.virtual_operator}"
        self.monitoring_topic = f"monitoring_{self.virtual_operator}"
        self.health_topic = f"health_{self.virtual_operator}"
        
        # Test state
        self.test_results = {}
        self.start_time = time.time()
        
        # Kafka clients
        self.producer = None
        self.consumer = None
        
    def setup_logging(self):
        """Setup logging configuration."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.logger = logging.getLogger('integration-test')
        
        # File handler
        log_dir = 'logs'
        os.makedirs(log_dir, exist_ok=True)
        
        file_handler = logging.FileHandler(
            os.path.join(log_dir, f'test-{datetime.now().strftime("%Y%m%d-%H%M%S")}.log')
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        self.logger.addHandler(file_handler)
    
    def connect_kafka(self) -> bool:
        """Connect to Kafka broker."""
        self.logger.info(f"Connecting to Kafka broker: {self.kafka_broker}")
        
        try:
            # Create producer
            self.producer = KafkaProducer(
                bootstrap_servers=self.kafka_broker,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                request_timeout_ms=10000,
                max_block_ms=30000
            )
            
            # Create consumer
            self.consumer = KafkaConsumer(
                self.monitoring_topic,
                bootstrap_servers=self.kafka_broker,
                value_deserializer=lambda v: json.loads(v.decode('utf-8')),
                auto_offset_reset='latest',
                consumer_timeout_ms=5000,
                group_id=f'test-{int(time.time())}'
            )
            
            # Test connection
            self.producer.flush(timeout=10)
            
            self.logger.info("Kafka connection established")
            return True
            
        except NoBrokersAvailable as e:
            self.logger.error(f"Kafka broker not available: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Kafka connection failed: {e}")
            return False
    
    def send_command(self, command: Dict[str, Any]) -> str:
        """Send command to agent and return command_id."""
        command_id = command.get('command_id', f'test-{int(time.time())}')
        command['command_id'] = command_id
        
        try:
            self.logger.info(f"Sending command: {command['action']} (ID: {command_id})")
            
            future = self.producer.send(
                topic=self.config_topic,
                key=command_id,
                value=command
            )
            future.get(timeout=10)
            
            self.producer.flush()
            self.logger.debug(f"Command sent successfully: {command_id}")
            
            return command_id
            
        except Exception as e:
            self.logger.error(f"Failed to send command: {e}")
            raise
    
    def wait_for_response(
        self,
        command_id: str,
        timeout: float = 30.0,
        expected_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Wait for response from agent."""
        self.logger.info(f"Waiting for response to command: {command_id}")
        
        start_time = time.time()
        last_log_time = start_time
        
        while time.time() - start_time < timeout:
            try:
                # Poll for messages
                batch = self.consumer.poll(timeout_ms=1000)
                
                for _, messages in batch.items():
                    for message in messages:
                        msg_data = message.value
                        
                        # Check if this is the response we're waiting for
                        if msg_data.get('command_id') == command_id:
                            if expected_type and msg_data.get('type') != expected_type:
                                continue
                            
                            self.logger.info(f"Received response for {command_id}")
                            return msg_data
                
                # Log progress every 5 seconds
                if time.time() - last_log_time >= 5:
                    elapsed = time.time() - start_time
                    self.logger.info(f"Waiting... {elapsed:.1f}s elapsed")
                    last_log_time = time.time()
                    
            except Exception as e:
                self.logger.error(f"Error while waiting for response: {e}")
        
        self.logger.warning(f"Timeout waiting for response to {command_id}")
        return None
    
    def test_kafka_connection(self) -> bool:
        """Test basic Kafka connectivity."""
        test_name = "kafka_connection"
        self.logger.info(f"Running test: {test_name}")
        
        try:
            if not self.connect_kafka():
                self.test_results[test_name] = {
                    'passed': False,
                    'error': 'Failed to connect to Kafka'
                }
                return False
            
            # Test topic existence by trying to get metadata
            topics = self.producer.partitions_for(self.config_topic)
            
            self.test_results[test_name] = {
                'passed': True,
                'topics': list(topics) if topics else []
            }
            
            self.logger.info(f"Test passed: {test_name}")
            return True
            
        except Exception as e:
            self.test_results[test_name] = {
                'passed': False,
                'error': str(e)
            }
            self.logger.error(f"Test failed: {test_name}: {e}")
            return False
    
    def test_health_check(self) -> bool:
        """Test health check command."""
        test_name = "health_check"
        self.logger.info(f"Running test: {test_name}")
        
        try:
            # Send health check command
            command = {
                'action': 'healthCheck',
                'command_id': f'health-test-{int(time.time())}',
                'target_pop': self.pop_id,
                'parameters': {},
                'timestamp': time.time()
            }
            
            command_id = self.send_command(command)
            
            # Wait for response
            response = self.wait_for_response(
                command_id=command_id,
                timeout=15.0,
                expected_type='health_check'
            )
            
            if not response:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': 'No response received'
                }
                return False
            
            # Check response
            if response.get('data', {}).get('status') in ['healthy', 'degraded']:
                self.test_results[test_name] = {
                    'passed': True,
                    'response': response
                }
                self.logger.info(f"Test passed: {test_name}")
                return True
            else:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': f'Unexpected status: {response.get("data", {}).get("status")}',
                    'response': response
                }
                return False
                
        except Exception as e:
            self.test_results[test_name] = {
                'passed': False,
                'error': str(e)
            }
            self.logger.error(f"Test failed: {test_name}: {e}")
            return False
    
    def test_interface_status(self) -> bool:
        """Test interface status command."""
        test_name = "interface_status"
        self.logger.info(f"Running test: {test_name}")
        
        try:
            # Send interface status command
            command = {
                'action': 'getInterfaceStatus',
                'command_id': f'status-test-{int(time.time())}',
                'target_pop': self.pop_id,
                'parameters': {
                    'interface': self.test_interface
                },
                'timestamp': time.time()
            }
            
            command_id = self.send_command(command)
            
            # Wait for response
            response = self.wait_for_response(
                command_id=command_id,
                timeout=15.0,
                expected_type='interface_status'
            )
            
            if not response:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': 'No response received'
                }
                return False
            
            # Check response
            data = response.get('data', {})
            if data.get('interface') == self.test_interface:
                self.test_results[test_name] = {
                    'passed': True,
                    'interface': data.get('interface'),
                    'present': data.get('present'),
                    'operational': data.get('operational'),
                    'response': response
                }
                self.logger.info(f"Test passed: {test_name}")
                return True
            else:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': f'Response mismatch: {data.get("interface")} != {self.test_interface}',
                    'response': response
                }
                return False
                
        except Exception as e:
            self.test_results[test_name] = {
                'passed': False,
                'error': str(e)
            }
            self.logger.error(f"Test failed: {test_name}: {e}")
            return False
    
    def test_pluggable_info(self) -> bool:
        """Test pluggable info command."""
        test_name = "pluggable_info"
        self.logger.info(f"Running test: {test_name}")
        
        try:
            # Send pluggable info command
            command = {
                'action': 'getPluggableInfo',
                'command_id': f'pluggable-test-{int(time.time())}',
                'target_pop': self.pop_id,
                'parameters': {},
                'timestamp': time.time()
            }
            
            command_id = self.send_command(command)
            
            # Wait for response
            response = self.wait_for_response(
                command_id=command_id,
                timeout=15.0,
                expected_type='pluggable_info'
            )
            
            if not response:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': 'No response received'
                }
                return False
            
            # Check response
            data = response.get('data', {})
            pluggables = data.get('pluggables', {})
            
            if self.test_interface in pluggables:
                info = pluggables[self.test_interface]
                self.test_results[test_name] = {
                    'passed': True,
                    'interface': self.test_interface,
                    'present': info.get('present'),
                    'vendor': info.get('vendor'),
                    'part_number': info.get('part_number'),
                    'response': response
                }
                self.logger.info(f"Test passed: {test_name}")
                return True
            else:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': f'Interface {self.test_interface} not in response',
                    'response': response
                }
                return False
                
        except Exception as e:
            self.test_results[test_name] = {
                'passed': False,
                'error': str(e)
            }
            self.logger.error(f"Test failed: {test_name}: {e}")
            return False
    
    def test_setup_connection(self) -> bool:
        """Test setup connection command."""
        test_name = "setup_connection"
        self.logger.info(f"Running test: {test_name}")
        
        try:
            # Create unique connection ID
            connection_id = f'test-conn-{int(time.time())}'
            
            # Send setup connection command
            command = {
                'action': 'setupConnection',
                'command_id': f'setup-test-{int(time.time())}',
                'target_pop': self.pop_id,
                'parameters': {
                    'connection_id': connection_id,
                    'frequency': 193100,  # 193.1 THz
                    'endpoint_config': [{
                        'pop_id': self.pop_id,
                        'port_id': self.test_interface,
                        'app': 1,  # 400ZR_DWDM
                        'tx_power_level': -10.0
                    }]
                },
                'timestamp': time.time()
            }
            
            command_id = self.send_command(command)
            
            # Wait for response
            response = self.wait_for_response(
                command_id=command_id,
                timeout=30.0,
                expected_type='setup_connection_result'
            )
            
            if not response:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': 'No response received'
                }
                return False
            
            # Check response
            data = response.get('data', {})
            
            if data.get('success'):
                self.test_results[test_name] = {
                    'passed': True,
                    'connection_id': data.get('connection_id'),
                    'frequency': data.get('frequency_mhz'),
                    'endpoints': data.get('endpoints', []),
                    'response': response
                }
                self.logger.info(f"Test passed: {test_name}")
                return True
            else:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': f'Setup failed: {data.get("errors", [])}',
                    'response': response
                }
                return False
                
        except Exception as e:
            self.test_results[test_name] = {
                'passed': False,
                'error': str(e)
            }
            self.logger.error(f"Test failed: {test_name}: {e}")
            return False
    
    def test_telemetry_collection(self) -> bool:
        """Test telemetry collection."""
        test_name = "telemetry_collection"
        self.logger.info(f"Running test: {test_name}")
        
        try:
            # First setup a connection
            connection_id = f'telemetry-test-{int(time.time())}'
            
            setup_command = {
                'action': 'setupConnection',
                'command_id': f'setup-telemetry-{int(time.time())}',
                'target_pop': self.pop_id,
                'parameters': {
                    'connection_id': connection_id,
                    'frequency': 193100,
                    'endpoint_config': [{
                        'pop_id': self.pop_id,
                        'port_id': self.test_interface,
                        'app': 1,
                        'tx_power_level': -10.0
                    }]
                },
                'timestamp': time.time()
            }
            
            setup_command_id = self.send_command(setup_command)
            setup_response = self.wait_for_response(
                command_id=setup_command_id,
                timeout=30.0
            )
            
            if not setup_response or not setup_response.get('data', {}).get('success'):
                self.test_results[test_name] = {
                    'passed': False,
                    'error': 'Setup failed, cannot test telemetry'
                }
                return False
            
            # Start telemetry
            start_command = {
                'action': 'startTelemetry',
                'command_id': f'start-telemetry-{int(time.time())}',
                'target_pop': self.pop_id,
                'parameters': {
                    'connection_id': connection_id
                },
                'timestamp': time.time()
            }
            
            start_command_id = self.send_command(start_command)
            start_response = self.wait_for_response(
                command_id=start_command_id,
                timeout=15.0
            )
            
            if not start_response:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': 'No response to start telemetry'
                }
                return False
            
            # Wait for telemetry data (up to 30 seconds)
            self.logger.info("Waiting for telemetry data (up to 30 seconds)...")
            
            telemetry_received = False
            start_time = time.time()
            telemetry_data = None
            
            while time.time() - start_time < 30 and not telemetry_received:
                batch = self.consumer.poll(timeout_ms=1000)
                
                for _, messages in batch.items():
                    for message in messages:
                        msg_data = message.value
                        
                        if (msg_data.get('type') == 'telemetry' and 
                            msg_data.get('data', {}).get('connection_id') == connection_id):
                            telemetry_received = True
                            telemetry_data = msg_data
                            break
                    
                    if telemetry_received:
                        break
            
            # Stop telemetry
            stop_command = {
                'action': 'stopTelemetry',
                'command_id': f'stop-telemetry-{int(time.time())}',
                'target_pop': self.pop_id,
                'parameters': {
                    'connection_id': connection_id
                },
                'timestamp': time.time()
            }
            
            stop_command_id = self.send_command(stop_command)
            
            # Check results
            if telemetry_received and telemetry_data:
                readings = telemetry_data.get('data', {}).get('readings', {})
                
                self.test_results[test_name] = {
                    'passed': True,
                    'connection_id': connection_id,
                    'telemetry_received': True,
                    'readings_available': list(readings.keys()),
                    'sample_reading': {
                        'tx_power_dbm': readings.get('tx_power_dbm'),
                        'osnr_db': readings.get('osnr_db')
                    }
                }
                self.logger.info(f"Test passed: {test_name}")
                return True
            else:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': 'No telemetry received within timeout',
                    'connection_id': connection_id
                }
                return False
                
        except Exception as e:
            self.test_results[test_name] = {
                'passed': False,
                'error': str(e)
            }
            self.logger.error(f"Test failed: {test_name}: {e}")
            return False
    
    def test_reconfig_connection(self) -> bool:
        """Test connection reconfiguration."""
        test_name = "reconfig_connection"
        self.logger.info(f"Running test: {test_name}")
        
        try:
            # First setup a connection
            connection_id = f'reconfig-test-{int(time.time())}'
            
            setup_command = {
                'action': 'setupConnection',
                'command_id': f'setup-reconfig-{int(time.time())}',
                'target_pop': self.pop_id,
                'parameters': {
                    'connection_id': connection_id,
                    'frequency': 193100,
                    'endpoint_config': [{
                        'pop_id': self.pop_id,
                        'port_id': self.test_interface,
                        'app': 1,
                        'tx_power_level': -10.0
                    }]
                },
                'timestamp': time.time()
            }
            
            setup_command_id = self.send_command(setup_command)
            setup_response = self.wait_for_response(
                command_id=setup_command_id,
                timeout=30.0
            )
            
            if not setup_response or not setup_response.get('data', {}).get('success'):
                self.test_results[test_name] = {
                    'passed': False,
                    'error': 'Setup failed, cannot test reconfiguration'
                }
                return False
            
            # Wait a moment
            time.sleep(2)
            
            # Reconfigure connection (change TX power)
            reconfig_command = {
                'action': 'reconfigConnection',
                'command_id': f'reconfig-test-{int(time.time())}',
                'target_pop': self.pop_id,
                'parameters': {
                    'connection_id': connection_id,
                    'endpoint_config': [{
                        'pop_id': self.pop_id,
                        'port_id': self.test_interface,
                        'tx_power_level': -9.0  # Increase power by 1 dB
                    }]
                },
                'timestamp': time.time()
            }
            
            reconfig_command_id = self.send_command(reconfig_command)
            reconfig_response = self.wait_for_response(
                command_id=reconfig_command_id,
                timeout=30.0,
                expected_type='reconfig_connection_result'
            )
            
            if not reconfig_response:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': 'No reconfiguration response'
                }
                return False
            
            # Check response
            data = reconfig_response.get('data', {})
            
            if data.get('success'):
                self.test_results[test_name] = {
                    'passed': True,
                    'connection_id': connection_id,
                    'reconfigured': data.get('reconfigured', []),
                    'response': reconfig_response
                }
                self.logger.info(f"Test passed: {test_name}")
                return True
            else:
                self.test_results[test_name] = {
                    'passed': False,
                    'error': f'Reconfiguration failed: {data.get("errors", [])}',
                    'response': reconfig_response
                }
                return False
                
        except Exception as e:
            self.test_results[test_name] = {
                'passed': False,
                'error': str(e)
            }
            self.logger.error(f"Test failed: {test_name}: {e}")
            return False
    
    def run_all_tests(self) -> Dict[str, Any]:
        """Run all integration tests."""
        self.logger.info("Starting comprehensive integration tests")
        self.logger.info(f"Test configuration: {self.config}")
        
        # Test execution order
        tests = [
            ('kafka_connection', self.test_kafka_connection),
            ('health_check', self.test_health_check),
            ('interface_status', self.test_interface_status),
            ('pluggable_info', self.test_pluggable_info),
            ('setup_connection', self.test_setup_connection),
            ('telemetry_collection', self.test_telemetry_collection),
            ('reconfig_connection', self.test_reconfig_connection),
        ]
        
        # Run tests
        passed_tests = 0
        failed_tests = 0
        
        for test_name, test_func in tests:
            try:
                if test_func():
                    passed_tests += 1
                else:
                    failed_tests += 1
                
                # Small delay between tests
                time.sleep(2)
                
            except Exception as e:
                self.logger.error(f"Test {test_name} crashed: {e}")
                self.test_results[test_name] = {
                    'passed': False,
                    'error': f'Test crashed: {str(e)}'
                }
                failed_tests += 1
        
        # Cleanup
        self.cleanup()
        
        # Calculate statistics
        total_time = time.time() - self.start_time
        success_rate = (passed_tests / len(tests)) * 100 if tests else 0
        
        summary = {
            'total_tests': len(tests),
            'passed_tests': passed_tests,
            'failed_tests': failed_tests,
            'success_rate': success_rate,
            'total_time_seconds': total_time,
            'test_results': self.test_results,
            'timestamp': time.time(),
            'config': self.config
        }
        
        return summary
    
    def cleanup(self):
        """Cleanup test resources."""
        self.logger.info("Cleaning up test resources...")
        
        try:
            if self.producer:
                self.producer.flush(timeout=5)
                self.producer.close()
            
            if self.consumer:
                self.consumer.close()
                
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
    
    def print_summary(self, summary: Dict[str, Any]):
        """Print test summary."""
        print("\n" + "="*60)
        print("INTEGRATION TEST SUMMARY")
        print("="*60)
        
        print(f"\nConfiguration:")
        print(f"  POP ID: {self.pop_id}")
        print(f"  Router ID: {self.router_id}")
        print(f"  Virtual Operator: {self.virtual_operator}")
        print(f"  Kafka Broker: {self.kafka_broker}")
        print(f"  Test Interface: {self.test_interface}")
        
        print(f"\nResults:")
        print(f"  Total Tests: {summary['total_tests']}")
        print(f"  Passed: {summary['passed_tests']}")
        print(f"  Failed: {summary['failed_tests']}")
        print(f"  Success Rate: {summary['success_rate']:.1f}%")
        print(f"  Total Time: {summary['total_time_seconds']:.1f} seconds")
        
        print(f"\nDetailed Results:")
        for test_name, result in summary['test_results'].items():
            status = "PASS" if result.get('passed') else "FAIL"
            print(f"  {test_name:30} [{status}]")
            
            if not result.get('passed'):
                error = result.get('error', 'Unknown error')
                print(f"      Error: {error}")
        
        print("\n" + "="*60)
        
        # Save summary to file
        self.save_summary(summary)
    
    def save_summary(self, summary: Dict[str, Any]):
        """Save test summary to file."""
        summary_dir = 'test-results'
        os.makedirs(summary_dir, exist_ok=True)
        
        filename = os.path.join(
            summary_dir,
            f'test-summary-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'
        )
        
        with open(filename, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        
        self.logger.info(f"Test summary saved to {filename}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='SONiC Agent Integration Test Suite')
    
    parser.add_argument('--pop-id', default='pop1', help='POP ID to test')
    parser.add_argument('--router-id', default='router1', help='Router ID to test')
    parser.add_argument('--virtual-operator', default='vOp2', help='Virtual operator name')
    parser.add_argument('--kafka-broker', default='10.30.7.52:9092', help='Kafka broker address')
    parser.add_argument('--test-interface', default='Ethernet192', help='Interface to test')
    parser.add_argument('--skip-tests', nargs='+', help='Tests to skip')
    
    args = parser.parse_args()
    
    # Create test configuration
    config = {
        'pop_id': args.pop_id,
        'router_id': args.router_id,
        'virtual_operator': args.virtual_operator,
        'kafka_broker': args.kafka_broker,
        'test_interface': args.test_interface
    }
    
    # Run tests
    tester = IntegrationTest(config)
    summary = tester.run_all_tests()
    
    # Print results
    tester.print_summary(summary)
    
    # Exit with appropriate code
    if summary['failed_tests'] > 0:
        print("\n❌ Some tests failed")
        sys.exit(1)
    else:
        print("\n✅ All tests passed!")
        sys.exit(0)


if __name__ == '__main__':
    main()