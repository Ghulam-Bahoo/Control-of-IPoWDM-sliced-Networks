# sonic-agent/run_agent.py
"""
Entry point for SONiC Agent container.

Fixes:
- AgentOrchestrator requires (kafka_manager, cmis_driver)
- agent KafkaManager requires (broker, config_topic, monitoring_topic)
- CMISDriver requires interface_mappings (IFNAME->PORTNUM)
"""

import json
import logging
import os
import signal
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Dict, Any

from config.settings import settings
from core.kafka_manager import KafkaManager
from core.cmis_driver import CMISDriver
from core.agent_orchestrator import AgentOrchestrator


def setup_logging() -> None:
    level_name = str(getattr(settings, "LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    log_file = getattr(settings, "LOG_FILE", "")
    if log_file:
        try:
            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)

            fh = RotatingFileHandler(
                log_file,
                maxBytes=int(getattr(settings, "LOG_MAX_SIZE_MB", 10)) * 1024 * 1024,
                backupCount=int(getattr(settings, "LOG_BACKUP_COUNT", 5)),
            )
            fh.setLevel(level)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception:
            root.exception("File logging disabled (cannot write LOG_FILE).")


logger = logging.getLogger(__name__)


def _print_banner() -> None:
    logger.info("=" * 60)
    logger.info("Starting SONiC Agent: %s", settings.AGENT_ID)
    logger.info("POP: %s, Router: %s", settings.POP_ID, settings.ROUTER_ID)
    logger.info("Virtual Operator: %s", settings.VIRTUAL_OPERATOR)
    logger.info("Kafka Broker: %s", settings.KAFKA_BROKER)
    logger.info("Config Topic: %s", settings.CONFIG_TOPIC)
    logger.info("Monitoring Topic: %s", settings.MONITORING_TOPIC)
    logger.info("Assigned Interfaces: %s", settings.ASSIGNED_TRANSCEIVERS)
    logger.info("=" * 60)


def _load_interface_mappings() -> Dict[str, int]:
    """
    Load IFNAME_TO_PORTNUM_JSON from settings and validate it is a dict[str,int].
    """
    raw = getattr(settings, "IFNAME_TO_PORTNUM_JSON", "{}")

    # It may already be a dict if your settings parses it; handle both.
    if isinstance(raw, dict):
        mappings = raw
    else:
        try:
            mappings = json.loads(str(raw))
        except Exception as e:
            raise ValueError(f"Invalid IFNAME_TO_PORTNUM_JSON: {e}. Value={raw!r}") from e

    if not isinstance(mappings, dict):
        raise ValueError(f"IFNAME_TO_PORTNUM_JSON must decode to dict, got {type(mappings)}")

    # Ensure int values (and normalize keys)
    out: Dict[str, int] = {}
    for k, v in mappings.items():
        if not isinstance(k, str):
            k = str(k)
        try:
            out[k] = int(v)
        except Exception as e:
            raise ValueError(f"Invalid mapping value for {k}: {v!r} (must be int)") from e

    if not out:
        logger.warning("IFNAME_TO_PORTNUM_JSON is empty. CMIS reads may fail.")
    return out


def main() -> int:
    setup_logging()
    _print_banner()

    orchestrator = None
    kafka = None

    def handle_signal(sig, _frame):
        nonlocal orchestrator, kafka
        logger.info("Received signal %s, shutting down...", sig)

        try:
            if orchestrator:
                orchestrator.stop()
        except Exception:
            logger.exception("Error stopping orchestrator")

        try:
            if kafka:
                kafka.close()
        except Exception:
            logger.exception("Error closing Kafka manager")

        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        interface_mappings = _load_interface_mappings()
        logger.info("Loaded %d interface mappings for CMISDriver", len(interface_mappings))

        kafka = KafkaManager(
            broker=settings.KAFKA_BROKER,
            config_topic=settings.CONFIG_TOPIC,
            monitoring_topic=settings.MONITORING_TOPIC,
        )

        cmis_driver = CMISDriver(interface_mappings=interface_mappings)

        orchestrator = AgentOrchestrator(
            kafka_manager=kafka,
            cmis_driver=cmis_driver,
        )

        orchestrator.start()

        while True:
            time.sleep(1)

    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        try:
            if kafka:
                kafka.close()
        except Exception:
            logger.exception("Error closing Kafka after fatal error")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

