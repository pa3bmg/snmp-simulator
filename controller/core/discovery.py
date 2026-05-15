"""
SNMP Discovery Engine.

Walks a real network device over SNMPv1 or v2c and collects:
  - System info (sysDescr, sysName, sysObjectID, …)
  - Interface table (ifTable + ifXTable)
  - CDP neighbor cache (Cisco devices)
  - LLDP remote table
  - Transparent bridge scalars (optional)
  - Full raw OID snapshot

Results are returned as a DiscoveryResult dataclass and can be saved as
named profiles (see controller/core/config.py).

The DiscoveryWorker(QThread) wraps the async scan so it can be driven from
the Qt GUI without blocking the main thread.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pysnmp.hlapi.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    walk_cmd,
    get_cmd,
)

# Qt is only available in the desktop GUI environment; make it optional so this
# module can also be imported on headless servers running the Flask web UI.
try:
    from PySide6.QtCore import QThread, Signal as _Signal
    _HAS_QT = True
except ImportError:
    _HAS_QT = False
    QThread = object           # fallback base class (DiscoveryWorker unusable but importable)
    def _Signal(*args, **kwargs):  # noqa: N802
        return None

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Known OIDs used by the parser
# ---------------------------------------------------------------------------
_SYS_PREFIX      = "1.3.6.1.2.1.1"
_IFTABLE_PREFIX  = "1.3.6.1.2.1.2.2.1"
_IFXTABLE_PREFIX = "1.3.6.1.2.1.31.1.1.1"
_CDP_PREFIX      = "1.3.6.1.4.1.9.9.23.1.2.1.1"   # cdpCacheEntry (not the table)
_LLDP_REM_PREFIX = "1.0.8802.1.1.2.1.4.1"
_BRIDGE_PREFIX   = "1.3.6.1.2.1.17"

_SYS_SCALARS = {
    "1.3.6.1.2.1.1.1.0": "sysDescr",
    "1.3.6.1.2.1.1.2.0": "sysObjectID",
    "1.3.6.1.2.1.1.3.0": "sysUpTime",
    "1.3.6.1.2.1.1.4.0": "sysContact",
    "1.3.6.1.2.1.1.5.0": "sysName",
    "1.3.6.1.2.1.1.6.0": "sysLocation",
    "1.3.6.1.2.1.1.7.0": "sysServices",
}

# ifTable column sub-ids
_IF_COL = {
    "1": "ifIndex",
    "2": "ifDescr",
    "3": "ifType",
    "4": "ifMtu",
    "5": "ifSpeed",
    "6": "ifPhysAddress",
    "7": "ifAdminStatus",
    "8": "ifOperStatus",
}

# ifXTable column sub-ids
_IFX_COL = {
    "1":  "ifName",
    "18": "ifAlias",
}

# CDP cache column sub-ids
_CDP_COL = {
    "4": "cdpCacheAddress",
    "5": "cdpCacheVersion",
    "6": "cdpCacheDeviceId",
    "7": "cdpCacheDevicePort",
    "8": "cdpCachePlatform",
    "11": "cdpCacheCapabilities",
}

# LLDP remote system column sub-ids (within 1.0.8802.1.1.2.1.4.1.1)
_LLDP_COL = {
    "5": "lldpRemChassisIdSubtype",
    "6": "lldpRemChassisId",
    "7": "lldpRemPortIdSubtype",
    "8": "lldpRemPortId",
    "9": "lldpRemPortDesc",
    "10": "lldpRemSysName",
    "11": "lldpRemSysDesc",
}


# ---------------------------------------------------------------------------
#  Data containers
# ---------------------------------------------------------------------------
@dataclass
class DiscoveryResult:
    target_ip:      str
    community:      str
    snmp_version:   str                       # "v1" or "v2c"
    timestamp:      str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    system:         Dict[str, str] = field(default_factory=dict)
    interfaces:     List[Dict[str, Any]] = field(default_factory=list)
    cdp_neighbors:  List[Dict[str, str]] = field(default_factory=list)
    lldp_neighbors: List[Dict[str, str]] = field(default_factory=list)
    bridge:         Dict[str, str] = field(default_factory=dict)
    raw_oids:       Dict[str, str] = field(default_factory=dict)

    def infer_device_type(self) -> str:
        """Best-guess device type from sysObjectID / sysDescr."""
        obj_id = self.system.get("sysObjectID", "")
        descr  = self.system.get("sysDescr", "").lower()
        if obj_id.startswith("1.3.6.1.4.1.9"):          # Cisco enterprise OID
            if "switch" in descr or "catalyst" in descr or "nexus" in descr:
                return "cisco_switch"
            return "cisco_router"
        if "windows" in descr:
            return "windows_server"
        # Fallback: check sysObjectID for Windows (Microsoft = 1.3.6.1.4.1.311)
        if obj_id.startswith("1.3.6.1.4.1.311"):
            return "windows_server"
        return "cisco_router"

    def to_dict(self) -> dict:
        return {
            "target_ip":      self.target_ip,
            "community":      self.community,
            "snmp_version":   self.snmp_version,
            "timestamp":      self.timestamp,
            "system":         self.system,
            "interfaces":     self.interfaces,
            "cdp_neighbors":  self.cdp_neighbors,
            "lldp_neighbors": self.lldp_neighbors,
            "bridge":         self.bridge,
            "raw_oids":       self.raw_oids,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DiscoveryResult":
        return cls(
            target_ip      = d.get("target_ip", ""),
            community      = d.get("community", "public"),
            snmp_version   = d.get("snmp_version", "v2c"),
            timestamp      = d.get("timestamp", ""),
            system         = d.get("system", {}),
            interfaces     = d.get("interfaces", []),
            cdp_neighbors  = d.get("cdp_neighbors", []),
            lldp_neighbors = d.get("lldp_neighbors", []),
            bridge         = d.get("bridge", {}),
            raw_oids       = d.get("raw_oids", {}),
        )


# ---------------------------------------------------------------------------
#  Discovery Engine
# ---------------------------------------------------------------------------
class DiscoveryEngine:
    """
    Async SNMP scanner.

    Usage (from a non-async context):
        result = asyncio.run(DiscoveryEngine().scan(...))
    """

    async def scan(
        self,
        ip:             str,
        community:      str  = "public",
        port:           int  = 161,
        version:        str  = "v2c",       # "v1" or "v2c"
        include_bridge: bool = False,
        timeout:        int  = 5,
        retries:        int  = 2,
        progress_cb=None,                   # optional callable(str)
    ) -> DiscoveryResult:
        """Walk the device and return a DiscoveryResult."""

        def _progress(msg: str):
            log.debug("Discovery [%s]: %s", ip, msg)
            if progress_cb:
                progress_cb(msg)

        mp_model = 0 if version == "v1" else 1   # 0=v1, 1=v2c

        snmp_engine = SnmpEngine()
        auth        = CommunityData(community, mpModel=mp_model)
        transport   = await UdpTransportTarget.create(
            (ip, port), timeout=timeout, retries=retries
        )
        ctx = ContextData()

        result = DiscoveryResult(
            target_ip=ip, community=community, snmp_version=version
        )

        # ---- System group -----------------------------------------------
        _progress("Scanning system group…")
        raw = await self._walk_subtree(
            snmp_engine, auth, transport, ctx, _SYS_PREFIX
        )
        result.raw_oids.update(raw)
        for oid, val in raw.items():
            label = _SYS_SCALARS.get(oid)
            if label:
                result.system[label] = val

        # ---- ifTable ----------------------------------------------------
        _progress("Scanning interface table…")
        raw = await self._walk_subtree(
            snmp_engine, auth, transport, ctx, _IFTABLE_PREFIX
        )
        result.raw_oids.update(raw)
        ifc_data: Dict[str, Dict[str, str]] = {}   # ifIndex -> fields
        for oid, val in raw.items():
            parts = oid[len(_IFTABLE_PREFIX):].lstrip(".").split(".")
            if len(parts) >= 2:
                col_id, idx = parts[0], parts[1]
                col_name = _IF_COL.get(col_id)
                if col_name:
                    ifc_data.setdefault(idx, {})
                    ifc_data[idx][col_name] = val

        # ---- ifXTable ---------------------------------------------------
        _progress("Scanning extended interface table (ifXTable)…")
        raw = await self._walk_subtree(
            snmp_engine, auth, transport, ctx, _IFXTABLE_PREFIX
        )
        result.raw_oids.update(raw)
        for oid, val in raw.items():
            parts = oid[len(_IFXTABLE_PREFIX):].lstrip(".").split(".")
            if len(parts) >= 2:
                col_id, idx = parts[0], parts[1]
                col_name = _IFX_COL.get(col_id)
                if col_name:
                    ifc_data.setdefault(idx, {})
                    ifc_data[idx][col_name] = val

        # Build sorted interface list
        for idx in sorted(ifc_data.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            entry = ifc_data[idx]
            entry["ifIndex"] = entry.get("ifIndex", idx)
            result.interfaces.append(entry)

        # ---- CDP cache --------------------------------------------------
        _progress("Scanning CDP neighbor cache…")
        raw = await self._walk_subtree(
            snmp_engine, auth, transport, ctx, _CDP_PREFIX
        )
        result.raw_oids.update(raw)
        cdp_rows: Dict[str, Dict[str, str]] = {}   # "localIfIndex.remoteIndex"
        for oid, val in raw.items():
            if not oid.startswith(_CDP_PREFIX):
                continue
            suffix = oid[len(_CDP_PREFIX):].lstrip(".")
            parts  = suffix.split(".")
            if len(parts) >= 3:
                col_id = parts[0]
                row_key = ".".join(parts[1:3])  # localIfIndex.remoteIndex
                col_name = _CDP_COL.get(col_id)
                if col_name:
                    cdp_rows.setdefault(row_key, {})
                    cdp_rows[row_key][col_name] = val
        for row in cdp_rows.values():
            # Decode cdpCacheAddress: pysnmp returns raw bytes as hex string
            addr = row.get("cdpCacheAddress", "")
            if addr:
                row["cdpCacheAddress"] = _decode_cdp_address(addr)
            result.cdp_neighbors.append(row)

        # ---- LLDP remote table ------------------------------------------
        _progress("Scanning LLDP remote table…")
        lldp_prefix_full = _LLDP_REM_PREFIX + ".1"
        raw = await self._walk_subtree(
            snmp_engine, auth, transport, ctx, lldp_prefix_full
        )
        result.raw_oids.update(raw)
        lldp_rows: Dict[str, Dict[str, str]] = {}
        for oid, val in raw.items():
            suffix = oid[len(lldp_prefix_full):].lstrip(".")
            parts  = suffix.split(".")
            if len(parts) >= 3:
                col_id  = parts[0]
                row_key = ".".join(parts[1:])
                col_name = _LLDP_COL.get(col_id)
                if col_name:
                    lldp_rows.setdefault(row_key, {})
                    lldp_rows[row_key][col_name] = val
        for row in lldp_rows.values():
            result.lldp_neighbors.append(row)

        # ---- Bridge MIB (optional) --------------------------------------
        if include_bridge:
            _progress("Scanning BRIDGE-MIB…")
            raw = await self._walk_subtree(
                snmp_engine, auth, transport, ctx, _BRIDGE_PREFIX
            )
            result.raw_oids.update(raw)
            bridge_scalars = {
                "1.3.6.1.2.1.17.1.1.0": "dot1dBaseBridgeAddress",
                "1.3.6.1.2.1.17.1.2.0": "dot1dBaseNumPorts",
                "1.3.6.1.2.1.17.1.3.0": "dot1dBaseType",
            }
            for oid, label in bridge_scalars.items():
                if oid in raw:
                    result.bridge[label] = raw[oid]

        snmp_engine.transport_dispatcher.closeDispatcher()
        _progress("Discovery complete.")
        return result

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #
    async def _walk_subtree(
        self,
        snmp_engine,
        auth,
        transport,
        ctx,
        base_oid: str,
    ) -> Dict[str, str]:
        """Walk a single OID subtree; return {oid_str: value_str} dict."""
        collected: Dict[str, str] = {}
        try:
            async for err_indication, err_status, _err_idx, var_binds in walk_cmd(
                snmp_engine,
                auth,
                transport,
                ctx,
                ObjectType(ObjectIdentity(base_oid)),
                lexicographicMode=False,
            ):
                if err_indication:
                    log.debug("Walk %s: %s", base_oid, err_indication)
                    break
                if err_status:
                    log.debug("Walk %s error status: %s", base_oid, err_status)
                    break
                for var_bind in var_binds:
                    oid_obj, val_obj = var_bind
                    oid_str = str(oid_obj)
                    val_str = _format_value(val_obj)
                    collected[oid_str] = val_str
        except Exception as exc:
            log.debug("Walk %s exception: %s", base_oid, exc)
        return collected


def _decode_cdp_address(raw: str) -> str:
    """
    cdpCacheAddress is a binary OCTET STRING (4 bytes for IPv4).
    pysnmp prettyPrint() returns it as hex like '0x0a000001'.
    Convert to dotted-decimal.  Non-IPv4 values are returned as-is.
    """
    try:
        cleaned = raw.strip().lower()
        if cleaned.startswith("0x"):
            cleaned = cleaned[2:]
        # Remove spaces/colons (some representations use them)
        cleaned = cleaned.replace(" ", "").replace(":", "")
        if len(cleaned) == 8:          # 4 bytes → IPv4
            b = bytes.fromhex(cleaned)
            return ".".join(str(x) for x in b)
    except Exception:
        pass
    return raw


def _format_value(val) -> str:
    """Convert pysnmp value object to a plain string."""
    try:
        # OID values
        if hasattr(val, "prettyPrint"):
            return val.prettyPrint()
        return str(val)
    except Exception:
        return str(val)


# ---------------------------------------------------------------------------
#  Qt Worker Thread
# ---------------------------------------------------------------------------
class DiscoveryWorker(QThread):
    """
    Runs DiscoveryEngine.scan() in a background thread and emits Qt signals.

    Signals
    -------
    progress(str)          – status text update
    result(object)         – DiscoveryResult on success
    error(str)             – human-readable error message on failure
    """

    if _HAS_QT:
        progress = _Signal(str)
        result   = _Signal(object)   # DiscoveryResult
        error    = _Signal(str)

    def __init__(
        self,
        ip:             str,
        community:      str  = "public",
        port:           int  = 161,
        version:        str  = "v2c",
        include_bridge: bool = False,
        timeout:        int  = 5,
        retries:        int  = 2,
        parent=None,
    ):
        super().__init__(parent)
        self._ip             = ip
        self._community      = community
        self._port           = port
        self._version        = version
        self._include_bridge = include_bridge
        self._timeout        = timeout
        self._retries        = retries

    def run(self) -> None:
        try:
            engine = DiscoveryEngine()
            res = asyncio.run(
                engine.scan(
                    ip=self._ip,
                    community=self._community,
                    port=self._port,
                    version=self._version,
                    include_bridge=self._include_bridge,
                    timeout=self._timeout,
                    retries=self._retries,
                    progress_cb=lambda msg: self.progress.emit(msg),
                )
            )
            self.result.emit(res)
        except Exception as exc:
            log.error("DiscoveryWorker failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))
