"""
Data engine — per-device background thread that:

  1. Increments sysUpTime every 100 ms (1 tick = 0.01 s)
  2. Fluctuates CPU % randomly within the device's configured range
  3. Increments interface counters (in/out octets) to simulate traffic
  4. Detects interface oper-status changes and fires SNMP traps
  5. Fires a cpuHigh trap when CPU crosses the high threshold (default 80 %)
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Optional

from controller.models.device import Device

log = logging.getLogger(__name__)

CPU_HIGH_THRESHOLD = 80        # % — trap sent when CPU exceeds this
CPU_TRAP_COOLDOWN  = 300       # seconds between repeated cpuHigh traps
TICK_INTERVAL      = 0.1       # seconds per main loop iteration (10 ticks/s)
CPU_UPDATE_EVERY   = 50        # iterations between CPU changes  (~5 s)
COUNTER_BYTES_MIN  = 1_000     # minimum bytes added per interval per interface
COUNTER_BYTES_MAX  = 1_000_000 # maximum bytes added per interval per interface


class DataEngine:
    """
    Drives the dynamic OID values of a single simulated device.

    Parameters
    ----------
    device          : Device   the device whose state is mutated
    trap_callback   : callable called when a trap should be sent
                      signature: trap_callback(device, trap_type, ifc_index=None)
                      trap_type: "linkDown" | "linkUp" | "cpuHigh"
    """

    def __init__(
        self,
        device: Device,
        trap_callback: Optional[Callable] = None,
    ):
        self.device = device
        self._trap_cb = trap_callback
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._cpu_high_last_sent: float = 0.0
        self._iter = 0

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"data-engine-{self.device.name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        device = self.device
        # Track previous oper_status to detect transitions
        prev_oper = {ifc.index: ifc.oper_status for ifc in device.interfaces}

        while not self._stop_event.is_set():
            self._iter += 1

            # sysUpTime: 10 ticks per second (1 tick = 10 ms)
            device.uptime_ticks += 1  # called at 10 Hz → matches 1 tick/0.1 s

            # Counter increments (every iteration)
            for ifc in device.interfaces:
                if ifc.oper_status == 1:  # only when interface is up
                    ifc.in_octets  += random.randint(COUNTER_BYTES_MIN, COUNTER_BYTES_MAX)
                    ifc.out_octets += random.randint(COUNTER_BYTES_MIN, COUNTER_BYTES_MAX)

            # CPU fluctuation (~every 5 s)
            if self._iter % CPU_UPDATE_EVERY == 0:
                new_cpu = random.randint(device.cpu_min, device.cpu_max)
                device.cpu_percent = new_cpu
                if new_cpu >= CPU_HIGH_THRESHOLD:
                    now = time.time()
                    if now - self._cpu_high_last_sent > CPU_TRAP_COOLDOWN:
                        self._cpu_high_last_sent = now
                        self._send_trap("cpuHigh")

            # Interface state-change detection
            for ifc in device.interfaces:
                current = ifc.oper_status
                previous = prev_oper.get(ifc.index, current)
                if current != previous:
                    trap_type = "linkDown" if current == 2 else "linkUp"
                    self._send_trap(trap_type, ifc_index=ifc.index)
                prev_oper[ifc.index] = current

            time.sleep(TICK_INTERVAL)

    def _send_trap(self, trap_type: str, ifc_index: int | None = None) -> None:
        if self._trap_cb:
            try:
                self._trap_cb(self.device, trap_type, ifc_index)
            except Exception as exc:
                log.error("Trap callback error: %s", exc)
