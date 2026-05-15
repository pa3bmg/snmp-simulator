"""
Base SNMP agent.

Each simulated device runs one instance of SnmpAgent in its own thread.
The agent owns an asyncio event loop and binds a pysnmp
CommandResponder to the device's IP:port.

The OID table is a plain dict:  { oid_str -> callable_or_value }
  - If the value is callable: called with no args each GET, returns current value.
  - Otherwise: the value is returned directly.

Supported PDU types: GET, GET-NEXT, GET-BULK (v2c only).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Dict

from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.entity import engine, config as snmp_config
from pysnmp.entity.rfc3413 import cmdrsp
from pysnmp.entity.rfc3413.context import SnmpContext
from pysnmp.proto.api import v2c

log = logging.getLogger(__name__)


OidValue = Any  # int | str | bytes | callable


class SnmpAgent:
    """
    A single-device SNMPv2c agent that runs in a dedicated thread.

    Parameters
    ----------
    device_ip   : str   IP address to bind to (must already exist on the host)
    device_port : int   UDP port (161 for production, 1161+ for dev/non-root)
    community   : str   SNMPv2c community string
    oid_table   : dict  {oid_str: value_or_callable}
                        Updated at any time by the data engine (thread-safe via lock)
    """

    def __init__(
        self,
        device_ip: str,
        device_port: int,
        community: str,
        oid_table: Dict[str, OidValue],
        extra_oids: Dict[str, OidValue] | None = None,
    ):
        self.device_ip = device_ip
        self.device_port = device_port
        self.community = community
        # Merge extra_oids UNDER the profile table so profile values (callables)
        # always take precedence over static discovered values.
        merged = dict(extra_oids) if extra_oids else {}
        merged.update(oid_table)          # profile wins on collision
        self._oid_table: Dict[str, OidValue] = merged
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._async_stop: asyncio.Event | None = None   # created inside the loop
        self._snmp_engine: engine.SnmpEngine | None = None

    # ------------------------------------------------------------------ #
    #  OID table management (called from data engine thread)
    # ------------------------------------------------------------------ #
    def update_oid(self, oid: str, value: OidValue) -> None:
        with self._lock:
            self._oid_table[oid] = value

    def update_oids(self, updates: Dict[str, OidValue]) -> None:
        with self._lock:
            self._oid_table.update(updates)

    def delete_oids_by_prefix(self, prefix: str) -> None:
        """Remove all OID entries whose key starts with *prefix*."""
        with self._lock:
            for key in [k for k in self._oid_table if k.startswith(prefix)]:
                del self._oid_table[key]

    def get_oid_table_copy(self) -> Dict[str, OidValue]:
        with self._lock:
            return dict(self._oid_table)

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self._stop_event.clear()
        self._async_stop = None
        self._thread = threading.Thread(
            target=self._run, name=f"agent-{self.device_ip}:{self.device_port}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._loop and not self._loop.is_closed():
            # Signal the coroutine to exit cleanly — never call loop.stop()
            # directly as that raises RuntimeError in run_until_complete.
            def _signal():
                if self._async_stop is not None:
                    self._async_stop.set()
            self._loop.call_soon_threadsafe(_signal)
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------ #
    #  Internal — runs in dedicated thread
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as exc:
            log.error("Agent %s:%s crashed: %s", self.device_ip, self.device_port, exc)
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        snmp_engine_obj = engine.SnmpEngine()
        self._snmp_engine = snmp_engine_obj

        # Transport
        snmp_config.add_transport(
            snmp_engine_obj,
            udp.DOMAIN_NAME,
            udp.UdpTransport().open_server_mode((self.device_ip, self.device_port)),
        )

        # Community
        snmp_config.add_v1_system(snmp_engine_obj, "read-area", self.community)
        snmp_config.add_vacm_user(
            snmp_engine_obj,
            2,  # v2c
            "read-area",
            "noAuthNoPriv",
            (1, 3, 6),   # read subtree
        )
        snmp_config.add_context(snmp_engine_obj, "")

        # Build SNMP context with our custom MIB controller
        snmp_context = SnmpContext(snmp_engine_obj)
        snmp_context.context_names[b""] = _MibInstrumentController(self)

        # Register handler for all GET/GET-NEXT/GET-BULK
        cmdrsp.GetCommandResponder(snmp_engine_obj, snmp_context)
        cmdrsp.NextCommandResponder(snmp_engine_obj, snmp_context)
        cmdrsp.BulkCommandResponder(snmp_engine_obj, snmp_context)

        log.info("SNMP agent started on %s:%s", self.device_ip, self.device_port)

        # Wait for stop signal — using asyncio.Event avoids forcibly killing the loop.
        self._async_stop = asyncio.Event()
        await self._async_stop.wait()

        snmp_engine_obj.close_dispatcher()
        log.info("SNMP agent stopped on %s:%s", self.device_ip, self.device_port)

    # ------------------------------------------------------------------ #
    #  OID resolution helpers
    # ------------------------------------------------------------------ #
    def resolve(self, oid: str) -> Any:
        with self._lock:
            val = self._oid_table.get(oid)
        if callable(val):
            return val()
        return val

    def next_oid(self, oid: str) -> str | None:
        """Return the lexicographically next OID in the table."""
        with self._lock:
            keys = sorted(self._oid_table.keys(), key=_oid_key)
        k = _oid_key(oid)
        for candidate in keys:
            if _oid_key(candidate) > k:
                return candidate
        return None


# ------------------------------------------------------------------ #
#  MIB instrument controller — bridges pysnmp callbacks to OID table
# ------------------------------------------------------------------ #
from pysnmp.smi.instrum import AbstractMibInstrumController  # noqa: E402


class _MibInstrumentController(AbstractMibInstrumController):
    """Minimal MIB instrument: serves OIDs directly from the agent's table."""

    def __init__(self, agent: SnmpAgent):
        self._agent = agent

    # pysnmp 7.x uses *varBinds signature and **context kwargs
    def read_variables(self, *varBinds, **context):
        result = []
        for oid, _ in varBinds:
            oid_str = oid.prettyPrint()
            val = self._agent.resolve(oid_str)
            if val is None:
                result.append((oid, v2c.NoSuchObject()))
            else:
                result.append((oid, _to_snmp_value(val)))
        return result

    def read_next_variables(self, *varBinds, **context):
        result = []
        for oid, _ in varBinds:
            oid_str = oid.prettyPrint()
            next_oid_str = self._agent.next_oid(oid_str)
            if next_oid_str is None:
                result.append((oid, v2c.EndOfMibView()))
            else:
                from pysnmp.proto.rfc1902 import ObjectName
                next_oid = ObjectName(next_oid_str)
                val = self._agent.resolve(next_oid_str)
                result.append((next_oid, _to_snmp_value(val)))
        return result

    def write_variables(self, *varBinds, **context):
        return list(varBinds)


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #
def _oid_key(oid_str: str):
    """Convert OID string to tuple of ints for sorting."""
    return tuple(int(x) for x in oid_str.strip(".").split(".") if x)


def _to_snmp_value(val: Any):
    """Convert a Python value to the appropriate pysnmp type."""
    from pysnmp.proto.rfc1902 import (
        Integer, OctetString, ObjectIdentifier,
        Counter32, Counter64, Gauge32, TimeTicks,
    )
    if isinstance(val, TimeTicks):
        return val
    if isinstance(val, (Counter32, Counter64, Gauge32, Integer)):
        return val
    if isinstance(val, ObjectIdentifier):
        return val
    if isinstance(val, int):
        return Integer(val)
    if isinstance(val, str):
        return OctetString(val)
    if isinstance(val, bytes):
        return OctetString(val)
    return OctetString(str(val))
