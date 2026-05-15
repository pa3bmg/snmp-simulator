"""
Agent manager — starts, stops, and tracks all simulated device agents.

For each device it:
  1. Selects the correct profile module (cisco_router / cisco_switch / windows_server)
  2. Builds the initial OID table
  3. Starts an SnmpAgent bound to the device's IP:port
  4. Starts a DataEngine to drive dynamic values and send traps
"""
from __future__ import annotations

import importlib
import logging
from typing import Callable, Dict, List, Optional, Tuple

from agents.base_agent import SnmpAgent
from agents.data_engine import DataEngine
from controller.models.device import Device
from traps.trap_sender import send_trap

log = logging.getLogger(__name__)

_PROFILE_MAP = {
    "cisco_router":   "agents.profiles.cisco_router",
    "cisco_switch":   "agents.profiles.cisco_switch",
    "windows_server": "agents.profiles.windows_server",
}


class AgentManager:
    """
    Manages the full lifecycle of all simulated SNMP agents.

    Signals (set externally, called on the main thread via Qt signals or direct call):
        on_trap   : Callable[[Device, str, Optional[int]], None]
                    called when a trap is sent: (device, trap_type, ifc_index)
        on_status : Callable[[str, str], None]
                    called when a device status changes: (device_id, new_status)
                    new_status: "running" | "stopped" | "error"
    """

    def __init__(
        self,
        on_trap: Optional[Callable] = None,
        on_status: Optional[Callable] = None,
    ):
        self.on_trap   = on_trap
        self.on_status = on_status

        # device_id -> (SnmpAgent, DataEngine)
        self._agents: Dict[str, Tuple[SnmpAgent, DataEngine]] = {}

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #
    def start_device(self, device: Device) -> bool:
        """Start SNMP agent + data engine for a device. Returns True on success."""
        if device.id in self._agents:
            agent, eng = self._agents[device.id]
            if agent.is_running:
                log.warning("Device %s already running", device.name)
                return False
            # Thread died (crashed) — clean up stale entry before restarting
            eng.stop()
            del self._agents[device.id]
            self._notify_status(device.id, "stopped")

        profile_module_name = _PROFILE_MAP.get(device.device_type)
        if not profile_module_name:
            log.error("Unknown device type: %s", device.device_type)
            return False

        try:
            profile = importlib.import_module(profile_module_name)
            oid_table = profile.build_oid_table(device)
        except Exception as exc:
            log.error("Failed to build OID table for %s: %s", device.name, exc)
            self._notify_status(device.id, "error")
            return False

        agent = SnmpAgent(
            device.ip, device.port, device.community, oid_table,
            extra_oids=device.extra_oids if device.extra_oids else None,
        )
        engine = DataEngine(device, trap_callback=self._trap_callback)

        try:
            agent.start()
            engine.start()
        except Exception as exc:
            log.error("Failed to start agent for %s: %s", device.name, exc)
            self._notify_status(device.id, "error")
            return False

        self._agents[device.id] = (agent, engine)
        self._notify_status(device.id, "running")
        log.info("Started agent for %s at %s:%s", device.name, device.ip, device.port)
        return True

    def stop_device(self, device_id: str) -> None:
        pair = self._agents.pop(device_id, None)
        if pair is None:
            return
        agent, eng = pair
        eng.stop()
        agent.stop()
        self._notify_status(device_id, "stopped")
        log.info("Stopped agent %s", device_id)

    def start_all(self, devices: List[Device]) -> None:
        for device in devices:
            self.start_device(device)

    def stop_all(self) -> None:
        for device_id in list(self._agents.keys()):
            self.stop_device(device_id)

    def is_running(self, device_id: str) -> bool:
        pair = self._agents.get(device_id)
        if pair is None:
            return False
        agent, _ = pair
        return agent.is_running

    def running_ids(self) -> List[str]:
        return list(self._agents.keys())

    def update_cdp_for_device(self, device_id: str, cdp_oids: dict) -> None:
        """Replace all CDP OIDs for a running agent with *cdp_oids*."""
        from controller.core.cdp_utils import CDP_PREFIX
        pair = self._agents.get(device_id)
        if pair is None:
            return
        agent, _ = pair
        agent.delete_oids_by_prefix(CDP_PREFIX)
        if cdp_oids:
            agent.update_oids(cdp_oids)

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #
    def _trap_callback(self, device: Device, trap_type: str, ifc_index: Optional[int] = None) -> None:
        send_trap(device, trap_type, ifc_index)
        if self.on_trap:
            try:
                self.on_trap(device, trap_type, ifc_index)
            except Exception as exc:
                log.error("on_trap callback error: %s", exc)

    def _notify_status(self, device_id: str, status: str) -> None:
        if self.on_status:
            try:
                self.on_status(device_id, status)
            except Exception as exc:
                log.error("on_status callback error: %s", exc)
