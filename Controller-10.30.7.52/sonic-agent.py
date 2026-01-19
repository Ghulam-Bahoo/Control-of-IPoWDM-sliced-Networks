#!/usr/bin/env python3
import os, json, subprocess, time, socket, logging, re
from kafka import KafkaConsumer, KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic

# ==================== SHARED TOPICS CONFIGURATION ====================
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "10.30.7.52:9092")
SWITCH_IP = os.getenv("SWITCH_IP", "10.30.7.7")
POP_ID = os.getenv("POP_ID", "pop1")
ROUTER_ID = os.getenv("ROUTER_ID", "router1")
VIRTUAL_OPERATOR = os.getenv("VIRTUAL_OPERATOR", "vOp1")

# SHARED TOPICS - Both switches use same topics
CONFIG_TOPIC = os.getenv("CONFIG_TOPIC", "config-topic")
MONITORING_TOPIC = os.getenv("MONITORING_TOPIC", "monitoring-topic")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
AGENT_ID = f"{VIRTUAL_OPERATOR}-agent-{POP_ID}-{ROUTER_ID}"
logger = logging.getLogger(AGENT_ID)

def deserialize_message(message):
    """Safe message deserialization with error handling"""
    try:
        if not message:
            logger.warning("Received empty message")
            return {}
            
        decoded = message.decode('utf-8').strip()
        if not decoded:
            logger.warning("Received empty string after decoding")
            return {}
            
        return json.loads(decoded)
        
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON message: {e}, raw: {message.decode('utf-8', errors='ignore')[:100]}")
        return {"error": "invalid_json"}
    except UnicodeDecodeError as e:
        logger.warning(f"Failed to decode message: {e}")
        return {"error": "decode_error"}
    except Exception as e:
        logger.warning(f"Unexpected error deserializing message: {e}")
        return {"error": "deserialization_failed"}

class IPoWDMAgent:
    def __init__(self):
        self.detected_transceivers = self.detect_transceivers()
        self.active_connections = {}
        logger.info(f"Detected transceivers: {self.detected_transceivers}")
        
    def detect_transceivers(self):
        """Automatically detect which interfaces have transceivers installed"""
        try:
            # Run show interfaces transceiver to detect installed transceivers
            cmd = ["show", "interfaces", "transceiver"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            transceivers = []
            if result.returncode == 0:
                # Parse output to find interfaces with transceivers
                lines = result.stdout.split('\n')
                for line in lines:
                    # Look for Ethernet interfaces in the output
                    if 'Ethernet' in line and not '----' in line:
                        # Extract interface name (e.g., "Ethernet48", "Ethernet49")
                        match = re.search(r'(Ethernet\d+)', line)
                        if match:
                            interface = match.group(1)
                            transceivers.append(interface)
            
            # If no transceivers found via that command, try alternative method
            if not transceivers:
                transceivers = self.detect_transceivers_alternative()
                
            logger.info(f"Auto-detected {len(transceivers)} transceivers: {transceivers}")
            return transceivers
            
        except Exception as e:
            logger.error(f"Error detecting transceivers: {e}")
            return self.detect_transceivers_alternative()
    
    def detect_transceivers_alternative(self):
        """Alternative method to detect transceivers"""
        try:
            # Try show interfaces status and look for interfaces with transceivers
            cmd = ["show", "interfaces", "status"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            transceivers = []
            if result.returncode == 0:
                lines = result.stdout.split('\n')
                for line in lines:
                    if 'Ethernet' in line:
                        match = re.search(r'(Ethernet\d+)', line)
                        if match:
                            interface = match.group(1)
                            # Verify it has a transceiver by checking transceiver info
                            if self.has_transceiver(interface):
                                transceivers.append(interface)
            
            return transceivers
            
        except Exception as e:
            logger.error(f"Error in alternative transceiver detection: {e}")
            return []
    
    def has_transceiver(self, interface):
        """Check if a specific interface has a transceiver installed"""
        try:
            cmd = ["show", "interfaces", "transceiver", "eeprom", interface]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return result.returncode == 0 and "Not applicable" not in result.stdout
        except:
            return False
    
    def get_all_interfaces(self):
        """Get all Ethernet interfaces on the switch"""
        try:
            cmd = ["show", "interfaces", "status"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            interfaces = []
            if result.returncode == 0:
                lines = result.stdout.split('\n')
                for line in lines:
                    match = re.search(r'(Ethernet\d+)', line)
                    if match:
                        interfaces.append(match.group(1))
            
            return list(set(interfaces))  # Remove duplicates
            
        except Exception as e:
            logger.error(f"Error getting all interfaces: {e}")
            return []

    def execute_command(self, action, parameters):
        logger.info(f"Executing: {action}")
        
        if action == "getAvailablePluggables":
            return self.get_available_pluggables(parameters)
        elif action == "getSystemInfo":
            return self.get_system_info(parameters)
        elif action == "pingTest":
            return self.ping_test(parameters)
        elif action == "setupE2EConnection":
            return self.setup_e2e_connection(parameters)
        elif action == "adaptConnectionQoT":
            return self.adapt_connection_qot(parameters)
        elif action == "configureTransceiver":
            return self.configure_transceiver(parameters)
        elif action == "getQoTMetrics":
            return self.get_qot_metrics(parameters)
        elif action == "interfaceControl":
            return self.control_interface(parameters)
        elif action == "getInterfaceStatus":
            return self.get_interface_status(parameters)
        elif action == "scanTransceivers":
            return self.scan_transceivers(parameters)
        elif action == "getAllInterfaces":
            return self.get_all_interfaces_status(parameters)
        elif action == "getTransceiverDetails":
            return self.get_transceiver_details(parameters)
        elif action == "help":
            return self.get_help(parameters)
        else:
            return {
                "success": False,
                "error": f"Unknown action: {action}",
                "available_actions": self.get_available_actions()
            }

    def get_available_actions(self):
        """Return list of all available commands"""
        return [
            "getAvailablePluggables", "getSystemInfo", "pingTest", 
            "setupE2EConnection", "adaptConnectionQoT", "configureTransceiver",
            "getQoTMetrics", "interfaceControl", "getInterfaceStatus", 
            "scanTransceivers", "getAllInterfaces", "getTransceiverDetails", "help"
        ]

    def get_help(self, parameters):
        """Return help information about all available commands"""
        return {
            "success": True,
            "available_commands": {
                "getAvailablePluggables": "Get automatically detected transceivers",
                "getSystemInfo": "Get system information",
                "pingTest": "Perform ping test",
                "setupE2EConnection": "Setup end-to-end connection (Paper Study Case 2)",
                "adaptConnectionQoT": "Adapt connection QoT (Paper Study Case 3)",
                "configureTransceiver": "Configure optical transceiver parameters",
                "getQoTMetrics": "Get QoT metrics for optical interface",
                "interfaceControl": "Control interface admin state (up/down)",
                "getInterfaceStatus": "Get interface status information",
                "scanTransceivers": "Rescan for transceivers",
                "getAllInterfaces": "Get status of all interfaces",
                "getTransceiverDetails": "Get detailed transceiver information",
                "help": "Show this help message"
            },
            "targeting": "Use target_pop, target_router, target_vop to target specific switches"
        }

    def get_available_pluggables(self, parameters):
        """Get automatically detected transceivers"""
        return {
            "success": True,
            "detected_transceivers": self.detected_transceivers,
            "all_interfaces": self.get_all_interfaces(),
            "pop_id": POP_ID,
            "router_id": ROUTER_ID,
            "virtual_operator": VIRTUAL_OPERATOR,
            "message": f"Auto-detected {len(self.detected_transceivers)} transceivers"
        }

    def scan_transceivers(self, parameters):
        """Rescan and update detected transceivers"""
        self.detected_transceivers = self.detect_transceivers()
        return {
            "success": True,
            "detected_transceivers": self.detected_transceivers,
            "message": f"Rescanned and found {len(self.detected_transceivers)} transceivers"
        }

    def get_all_interfaces_status(self, parameters):
        """Get status of all interfaces"""
        try:
            cmd = ["show", "interface", "status"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return {
                    "success": True,
                    "status_output": result.stdout.strip(),
                    "message": "All interfaces status retrieved"
                }
            else:
                return {
                    "success": False,
                    "error": f"Command failed with return code {result.returncode}",
                    "stderr": result.stderr.strip()
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_transceiver_details(self, parameters):
        """Get detailed transceiver information"""
        try:
            interface = parameters.get('interface')
            if interface:
                cmd = ["show", "interfaces", "transceiver", "details", interface]
            else:
                cmd = ["show", "interfaces", "transceiver", "details"]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return {
                    "success": True,
                    "interface": interface or "all",
                    "details_output": result.stdout.strip(),
                    "message": "Transceiver details retrieved"
                }
            else:
                return {
                    "success": False,
                    "error": f"Command failed with return code {result.returncode}",
                    "stderr": result.stderr.strip()
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def control_interface(self, parameters):
        """Control interface admin state (up/down)"""
        try:
            interface = parameters.get('interface')
            admin_state = parameters.get('admin_state')  # 'up' or 'down'
            
            if not interface:
                return {"success": False, "error": "Interface parameter required"}
                
            if admin_state not in ['up', 'down']:
                return {"success": False, "error": "admin_state must be 'up' or 'down'"}
            
            # Execute SONiC config command
            if admin_state == 'down':
                cmd = ["sudo", "config", "interface", "shutdown", interface]
                action_desc = "shutdown"
            else:  # 'up'
                cmd = ["sudo", "config", "interface", "startup", interface]
                action_desc = "startup"
            
            logger.info(f"Executing: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return {
                    "success": True,
                    "interface": interface,
                    "admin_state": admin_state,
                    "message": f"Interface {interface} {action_desc} successful",
                    "stdout": result.stdout.strip(),
                    "stderr": result.stderr.strip()
                }
            else:
                return {
                    "success": False,
                    "interface": interface,
                    "admin_state": admin_state,
                    "error": f"Command failed with return code {result.returncode}",
                    "stdout": result.stdout.strip(),
                    "stderr": result.stderr.strip()
                }
                
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Command timed out after 30 seconds"}
        except Exception as e:
            logger.error(f"Error controlling interface: {e}")
            return {"success": False, "error": str(e)}

    def get_interface_status(self, parameters):
        """Get interface status information"""
        try:
            interface = parameters.get('interface')
            
            if interface:
                # Get specific interface status
                cmd = ["show", "interface", "status", interface]
            else:
                # Get all interface status
                cmd = ["show", "interface", "status"]
            
            logger.info(f"Executing: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return {
                    "success": True,
                    "interface": interface or "all",
                    "status_output": result.stdout.strip(),
                    "message": "Interface status retrieved successfully"
                }
            else:
                return {
                    "success": False,
                    "interface": interface or "all",
                    "error": f"Command failed with return code {result.returncode}",
                    "stderr": result.stderr.strip()
                }
                
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Command timed out after 30 seconds"}
        except Exception as e:
            logger.error(f"Error getting interface status: {e}")
            return {"success": False, "error": str(e)}

    def configure_transceiver(self, parameters):
        """Configure optical transceiver parameters"""
        try:
            interface = parameters.get('interface')
            frequency = parameters.get('frequency')
            tx_power = parameters.get('tx_power')
            app_code = parameters.get('app_code')
            
            if not interface:
                return {"success": False, "error": "Interface parameter required"}
            
            # Check if interface has a transceiver
            if interface not in self.detected_transceivers:
                return {
                    "success": False, 
                    "error": f"Interface {interface} does not have a transceiver installed. Available: {self.detected_transceivers}"
                }
            
            logger.info(f"Configuring {interface}: freq={frequency}Hz, tx_power={tx_power}dBm, app_code={app_code}")
            
            # Simulate configuration
            connection_id = f"conn-{interface}-{int(time.time())}"
            self.active_connections[connection_id] = {
                "interface": interface,
                "frequency": frequency,
                "tx_power": tx_power,
                "app_code": app_code,
                "start_time": time.time()
            }
            
            return {
                "success": True,
                "connection_id": connection_id,
                "interface": interface,
                "frequency": frequency,
                "tx_power": tx_power,
                "app_code": app_code,
                "message": f"Transceiver {interface} configured successfully"
            }
            
        except Exception as e:
            logger.error(f"Error configuring transceiver: {e}")
            return {"success": False, "error": str(e)}

    def get_qot_metrics(self, parameters):
        """Get QoT metrics for optical interface"""
        try:
            interface = parameters.get('interface')
            
            if not interface:
                return {"success": False, "error": "Interface parameter required"}
            
            # Check if interface has a transceiver
            if interface not in self.detected_transceivers:
                return {
                    "success": False, 
                    "error": f"Interface {interface} does not have a transceiver installed"
                }
            
            # Get transceiver status
            cmd = ["show", "interfaces", "transceiver", "status", interface]
            logger.info(f"Executing: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                output = result.stdout.strip()
                
                # Parse actual metrics from output
                metrics = self.parse_transceiver_metrics(output)
                metrics.update({
                    "timestamp": time.time(),
                    "interface": interface,
                    "raw_output": output
                })
                
                return {
                    "success": True,
                    "interface": interface,
                    "metrics": metrics,
                    "message": "QoT metrics retrieved successfully"
                }
            else:
                return {
                    "success": False,
                    "interface": interface,
                    "error": f"Command failed with return code {result.returncode}",
                    "stderr": result.stderr.strip()
                }
                
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Command timed out after 30 seconds"}
        except Exception as e:
            logger.error(f"Error getting QoT metrics: {e}")
            return {"success": False, "error": str(e)}

    def parse_transceiver_metrics(self, output):
        """Parse transceiver output to extract metrics"""
        metrics = {}
        try:
            # Extract RX power
            rx_match = re.search(r'RX Power\s*([-\d.]+)\s*dBm', output)
            if rx_match:
                metrics["rx_power_dbm"] = float(rx_match.group(1))
            
            # Extract TX power
            tx_match = re.search(r'TX Power\s*([-\d.]+)\s*dBm', output)
            if tx_match:
                metrics["tx_power_dbm"] = float(tx_match.group(1))
            
            # Extract temperature
            temp_match = re.search(r'Temperature\s*([-\d.]+)\s*C', output)
            if temp_match:
                metrics["temperature_c"] = float(temp_match.group(1))
            
            # Extract voltage
            volt_match = re.search(r'Voltage\s*([-\d.]+)\s*V', output)
            if volt_match:
                metrics["voltage_v"] = float(volt_match.group(1))
            
            # Extract bias current
            bias_match = re.search(r'Bias Current\s*([-\d.]+)\s*mA', output)
            if bias_match:
                metrics["bias_ma"] = float(bias_match.group(1))
                
        except Exception as e:
            logger.warning(f"Could not parse some metrics: {e}")
            
        return metrics

    def get_system_info(self, parameters):
        return {
            "success": True,
            "hostname": f"{POP_ID}-{ROUTER_ID}",
            "ip": SWITCH_IP,
            "timestamp": time.time(),
            "detected_transceivers": len(self.detected_transceivers),
            "virtual_operator": VIRTUAL_OPERATOR
        }

    def ping_test(self, parameters):
        target = parameters.get('target', '8.8.8.8')
        return {
            "success": True,
            "message": f"Ping test to {target} simulated",
            "target": target
        }

    def setup_e2e_connection(self, parameters):
        return {
            "success": True,
            "connection_id": parameters.get('connection_id', 'conn-1'),
            "message": f"E2E connection setup on {POP_ID}",
            "interface": parameters.get('endpoint_config', {}).get('port_id', 'Ethernet48')
        }

    def adapt_connection_qot(self, parameters):
        return {
            "success": True,
            "action": "qot_adapted",
            "interface": parameters.get('interface', 'Ethernet48'),
            "target_osnr": parameters.get('target_osnr', 25.0)
        }

def create_shared_topics():
    """Create ONLY shared Kafka topics"""
    try:
        admin = KafkaAdminClient(bootstrap_servers=[KAFKA_BROKER])
        
        shared_topics = [
            NewTopic(name=CONFIG_TOPIC, num_partitions=1, replication_factor=1),
            NewTopic(name=MONITORING_TOPIC, num_partitions=1, replication_factor=1),
        ]
        
        admin.create_topics(new_topics=shared_topics, validate_only=False)
        logger.info("Shared topics created")
        admin.close()
    except Exception as e:
        logger.info(f"Topics may exist: {e}")

def main():
    logger.info(f"Starting IPoWDM Agent: {AGENT_ID}")
    logger.info(f"Kafka: {KAFKA_BROKER}")
    logger.info(f"Config Topic: {CONFIG_TOPIC}")
    logger.info(f"Monitoring Topic: {MONITORING_TOPIC}")
    
    agent = IPoWDMAgent()
    create_shared_topics()
    
    # Kafka setup
    consumer = KafkaConsumer(
        CONFIG_TOPIC,
        bootstrap_servers=[KAFKA_BROKER],
        value_deserializer=deserialize_message,
        group_id=AGENT_ID,
        auto_offset_reset='latest'
    )
    
    producer = KafkaProducer(
        bootstrap_servers=[KAFKA_BROKER],
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )
    
    logger.info("Ready for commands...")
    
    try:
        for message in consumer:
            command = message.value
            if not command or command.get("error"):
                continue
                
            action = command.get('action')
            if not action:
                continue
            
            # Filter commands intended for this switch
            target_pop = command.get('target_pop')
            target_router = command.get('target_router')
            target_vop = command.get('target_vop')
            
            # Apply if command is for this switch OR broadcast to all
            if (target_pop is None and target_router is None and target_vop is None) or \
               (target_pop == POP_ID and target_router == ROUTER_ID and target_vop == VIRTUAL_OPERATOR):
                
                # Execute command
                result = agent.execute_command(action, command.get('parameters', {}))
                
                # Send response
                response = {
                    "timestamp": time.time(),
                    "agent_id": AGENT_ID,
                    "virtual_operator": VIRTUAL_OPERATOR,
                    "pop_id": POP_ID,
                    "router_id": ROUTER_ID,
                    "command_id": command.get('command_id', 'unknown'),
                    "action": action,
                    "result": result,
                    "status": "completed"
                }
                
                producer.send(MONITORING_TOPIC, response)
                logger.info(f"Command completed: {action}")
            
            else:
                logger.info(f"Observed config for other switch: {target_vop}.{target_pop}.{target_router}")
            
    except KeyboardInterrupt:
        logger.info("Stopping agent...")
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}")
    finally:
        try:
            consumer.close()
            producer.close()
        except:
            pass

if __name__ == "__main__":
    main()
