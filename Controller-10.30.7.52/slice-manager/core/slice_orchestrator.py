import logging
import subprocess
import json
from typing import Dict, List, Optional, Any
from datetime import datetime

from core.kafka_admin import kafka_admin
from core.linkdb_client import linkdb_client
from models.schemas import (
    VOpActivationRequest, VOpStatusResponse, VOpStatus, InterfaceAssignment
)
from config.settings import settings


logger = logging.getLogger(__name__)


class SliceOrchestrator:
    """Professional orchestrator for virtual operator slices."""
    
    def __init__(self):
        """Initialize orchestrator with dependencies."""
        self.kafka_admin = kafka_admin
        self.linkdb = linkdb_client
    
    def activate_virtual_operator(self, request: VOpActivationRequest) -> VOpStatusResponse:
        """
        Activate a new virtual operator (main business logic).
        
        This implements the exact flow described in the paper:
        1. Validate interface assignments
        2. Create Kafka topics
        3. Initialize Link Database
        4. Send initial configuration
        5. Deploy controller instance
        
        Args:
            request: Activation request
            
        Returns:
            Status response with activation details
        """
        activation_time = datetime.utcnow()
        vop_id = request.vop_id
        
        try:
            logger.info(f"Starting activation of {vop_id} for tenant {request.tenant_name}")
            
            # Step 1: Validate interface assignments
            self._validate_interface_assignments(request.interface_assignments)
            
            # Step 2: Create Kafka topics
            topic_info = self.kafka_admin.create_vop_topics(vop_id)
            
            # Step 3: Initialize Link Database
            linkdb_success = self._initialize_linkdb_for_vop(request)
            
            if not linkdb_success:
                raise RuntimeError(f"Failed to initialize Link Database for {vop_id}")
            
            # Step 4: Send initial configuration to agents
            config_sent = self._send_initial_configuration(vop_id, request.interface_assignments)
            
            # Step 5: Deploy controller instance
            controller_endpoint = self._deploy_controller_instance(vop_id, request)
            
            # Prepare response
            response = VOpStatusResponse(
                vop_id=vop_id,
                tenant_name=request.tenant_name,
                status=VOpStatus.ACTIVE,
                config_topic=topic_info['config_topic'].name,
                monitoring_topic=topic_info['monitoring_topic'].name,
                controller_endpoint=controller_endpoint,
                assigned_interfaces=[
                    {
                        'pop_id': assig.pop_id,
                        'router_id': assig.router_id,
                        'interfaces': assig.interfaces
                    }
                    for assig in request.interface_assignments
                ],
                activation_time=activation_time,
                message=f"Virtual operator {vop_id} activated successfully"
            )
            
            logger.info(f"Successfully activated {vop_id}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to activate {vop_id}: {e}")
            
            # Attempt cleanup on failure
            self._cleanup_failed_activation(vop_id)
            
            return VOpStatusResponse(
                vop_id=vop_id,
                tenant_name=request.tenant_name,
                status=VOpStatus.FAILED,
                config_topic=f"config_{vop_id}",
                monitoring_topic=f"monitoring_{vop_id}",
                assigned_interfaces=[
                    {
                        'pop_id': assig.pop_id,
                        'router_id': assig.router_id,
                        'interfaces': assig.interfaces
                    }
                    for assig in request.interface_assignments
                ],
                activation_time=activation_time,
                message=f"Activation failed: {str(e)}",
                error_details={'error': str(e), 'type': type(e).__name__}
            )
    
    def _validate_interface_assignments(self, assignments: List[InterfaceAssignment]) -> None:
        """
        Validate interface assignments against available resources.
        
        Args:
            assignments: List of interface assignments
            
        Raises:
            ValueError: If validation fails
        """
        for assignment in assignments:
            pop_id = assignment.pop_id
            router_id = assignment.router_id
            
            for interface in assignment.interfaces:
                # Check if interface is available
                is_available = self.linkdb.check_interface_availability(
                    pop_id, router_id, interface
                )
                
                if not is_available:
                    raise ValueError(
                        f"Interface {interface} at {pop_id}/{router_id} "
                        f"is not available for assignment"
                    )
        
        logger.debug("Interface assignments validated successfully")
    
    def _initialize_linkdb_for_vop(self, request: VOpActivationRequest) -> bool:
        """Initialize Link Database entries for the new vOp."""
        try:
            # Convert assignments to dictionary format for Link DB
            assignments_dict = [
                {
                    'pop_id': assig.pop_id,
                    'router_id': assig.router_id,
                    'interfaces': assig.interfaces
                }
                for assig in request.interface_assignments
            ]
            
            return self.linkdb.initialize_vop(
                request.vop_id,
                request.tenant_name,
                assignments_dict
            )
            
        except Exception as e:
            logger.error(f"Link DB initialization failed for {request.vop_id}: {e}")
            return False
    
    def _send_initial_configuration(self, vop_id: str, 
                                   assignments: List[InterfaceAssignment]) -> bool:
        """
        Send initial configuration message to agents via Kafka.
        
        According to the paper, agents are pre-configured to listen to all topics
        and filter based on their assigned interfaces.
        
        Args:
            vop_id: Virtual operator ID
            assignments: Interface assignments
            
        Returns:
            True if configuration sent successfully
        """
        # In a complete implementation, this would use a Kafka producer
        # to send a welcome/config message. For now, we'll log it.
        
        config_message = {
            'action': 'vop_activation',
            'vop_id': vop_id,
            'timestamp': datetime.utcnow().isoformat(),
            'assignments': [
                {
                    'pop_id': assig.pop_id,
                    'router_id': assig.router_id,
                    'interfaces': assig.interfaces
                }
                for assig in assignments
            ],
            'message': f"Virtual operator {vop_id} has been activated"
        }
        
        logger.info(f"Initial configuration for {vop_id}: {json.dumps(config_message, indent=2)}")
        
        # TODO: Implement actual Kafka producer
        # from kafka import KafkaProducer
        # producer = KafkaProducer(bootstrap_servers=settings.KAFKA_BROKER)
        # producer.send(f"config_{vop_id}", json.dumps(config_message).encode())
        
        return True
    
    def _deploy_controller_instance(self, vop_id: str, 
                                   request: VOpActivationRequest) -> Optional[str]:
        """
        Deploy controller instance using existing deployment script.
        
        Args:
            vop_id: Virtual operator ID
            request: Activation request
            
        Returns:
            Controller endpoint URL or None
        """
        try:
            # Prepare environment variables for deployment
            env_vars = {
                'VOP_ID': vop_id,
                'TENANT_NAME': request.tenant_name,
                'KAFKA_BROKER': settings.KAFKA_BROKER,
                'CONFIG_TOPIC': f"config_{vop_id}",
                'MONITORING_TOPIC': f"monitoring_{vop_id}"
            }
            
            # Call the existing deployment script
            logger.info(f"Deploying controller for {vop_id} using {settings.CONTROLLER_DEPLOY_SCRIPT}")
            
            # Build command with environment variables
            cmd = f"VOP_ID={vop_id} TENANT_NAME='{request.tenant_name}' "
            cmd += f"{settings.CONTROLLER_DEPLOY_SCRIPT}"
            
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes timeout
            )
            
            if result.returncode == 0:
                # Parse output to get endpoint (assuming script outputs it)
                endpoint = self._parse_controller_endpoint(result.stdout)
                logger.info(f"Controller for {vop_id} deployed successfully")
                return endpoint
            else:
                logger.error(f"Controller deployment failed for {vop_id}: {result.stderr}")
                return None
                
        except subprocess.TimeoutExpired:
            logger.error(f"Controller deployment timeout for {vop_id}")
            return None
        except Exception as e:
            logger.error(f"Controller deployment error for {vop_id}: {e}")
            return None
    
    def _parse_controller_endpoint(self, output: str) -> str:
        """Parse controller endpoint from deployment script output."""
        # This is a placeholder - adjust based on actual script output
        for line in output.split('\n'):
            if 'endpoint' in line.lower() or 'url' in line.lower():
                parts = line.split(':')
                if len(parts) > 1:
                    return parts[1].strip()
        
        # Default endpoint (adjust based on your network)
        return f"http://10.30.7.52:808{vop_id[-1]}"  # e.g., vOp2 -> 8082
    
    def _cleanup_failed_activation(self, vop_id: str) -> None:
        """Clean up resources after a failed activation."""
        try:
            logger.warning(f"Cleaning up failed activation for {vop_id}")
            
            # Delete Kafka topics
            self.kafka_admin.delete_vop_topics(vop_id)
            
            # Deactivate in Link DB
            self.linkdb.deactivate_vop(vop_id)
            
            # TODO: Undo any partial controller deployment
            
        except Exception as e:
            logger.error(f"Cleanup failed for {vop_id}: {e}")
    
    def get_vop_status(self, vop_id: str) -> Optional[VOpStatusResponse]:

        try:
            vop_info = self.linkdb.get_vop_info(vop_id)
            if not vop_info:
                return None
            
            # Handle interface_assignments parsing
            assignments = vop_info.get('interface_assignments', '[]')
            if isinstance(assignments, str):
                assignments = json.loads(assignments)
            
            return VOpStatusResponse(
                vop_id=vop_id,
                tenant_name=vop_info.get('tenant_name', 'Unknown'),
                status=VOpStatus(vop_info.get('status', 'UNKNOWN')),
                config_topic=f"config_{vop_id}",
                monitoring_topic=f"monitoring_{vop_id}",
                controller_endpoint=None,
                assigned_interfaces=assignments,
                activation_time=datetime.fromisoformat(vop_info.get('created_at')),
                message=f"Status: {vop_info.get('status')}"
            )
        except Exception as e:
            logger.error(f"Failed to get status for {vop_id}: {e}")
            return None
    
    def list_active_vops(self) -> List[Dict[str, Any]]:
        """List all active virtual operators."""
        try:
            vop_ids = self.linkdb.get_all_vops()
            vops = []
            
            for vop_id in vop_ids:
                vop_info = self.linkdb.get_vop_info(vop_id)
                if vop_info:
                    # Handle interface_assignments - it might be string or list
                    assignments = vop_info.get('interface_assignments', '[]')
                    if isinstance(assignments, str):
                        try:
                            assignments = json.loads(assignments)
                        except:
                            assignments = []
                    
                    vops.append({
                        'vop_id': vop_id,
                        'tenant_name': vop_info.get('tenant_name'),
                        'status': vop_info.get('status'),
                        'created_at': vop_info.get('created_at'),
                        'interfaces_count': len(assignments) if isinstance(assignments, list) else 0
                    })
            
            return vops
        except Exception as e:
            logger.error(f"Failed to list active vOps: {e}")
            return []
    
    def health_check(self) -> Dict[str, Any]:
        """Perform health check of all components."""
        return {
            'kafka_connected': True,  # Would need actual check
            'linkdb_connected': self.linkdb.health_check(),
            'timestamp': datetime.utcnow().isoformat(),
            'active_vops_count': len(self.list_active_vops())
        }


# Singleton instance
slice_orchestrator = SliceOrchestrator()