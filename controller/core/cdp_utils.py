"""
CDP utilities — builds CISCO-CDP-MIB OID entries and persists links to JSON.

cdpCacheTable OID structure:
    1.3.6.1.4.1.9.9.23.1.2.1.1.<column>.<ifIndex>.<deviceIndex>

Columns used:
    1  cdpCacheIfIndex        INTEGER
    3  cdpCacheAddressType    INTEGER (1=ip)
    4  cdpCacheAddress        OctetString (4 bytes for IPv4)
    5  cdpCacheVersion        DisplayString
    6  cdpCacheDeviceId       DisplayString (neighbor sysName / hostname)
    7  cdpCacheDevicePort     DisplayString (neighbor interface name)
    8  cdpCachePlatform       DisplayString
    9  cdpCacheCapabilities   OctetString  (4 bytes bitfield)

Global CDP scalars:
    1.3.6.1.4.1.9.9.23.1.3.1.0  cdpGlobalRun            = 1 (true)
    1.3.6.1.4.1.9.9.23.1.3.2.0  cdpGlobalMessageInterval = 60
    1.3.6.1.4.1.9.9.23.1.3.3.0  cdpGlobalHoldTime       = 180
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, List, Tuple

from pysnmp.proto.rfc1902 import Integer, OctetString

if TYPE_CHECKING:
    from controller.models.device import Device
    from controller.models.link import Link

log = logging.getLogger(__name__)

_CDP_BASE  = "1.3.6.1.4.1.9.9.23"
_CDP_CACHE = f"{_CDP_BASE}.1.2.1.1"
CDP_PREFIX = _CDP_BASE  # used to identify / clear CDP OIDs

# Default config path
_DEFAULT_LINKS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "links.json",
)

# Capability bytes (4-byte OctetString per CISCO-CDP-MIB)
_CAPABILITIES = {
    "cisco_router":   b"\x00\x00\x00\x01",   # Router
    "cisco_switch":   b"\x00\x00\x00\x08",   # Switch
    "windows_server": b"\x00\x00\x00\x10",   # Host
}

_PLATFORM = {
    "cisco_router":   "Cisco 7206",
    "cisco_switch":   "Cisco WS-C3750",
    "windows_server": "Windows Server 2019",
}

_VERSION = {
    "cisco_router":   "Cisco IOS Software, Version 15.7(3)M5",
    "cisco_switch":   "Cisco IOS Software, Version 12.2(55)SE12",
    "windows_server": "Microsoft Windows Server 2019",
}


# --------------------------------------------------------------------------- #
#  CDP OID builder
# --------------------------------------------------------------------------- #
def build_cdp_oids(
    device: "Device",
    neighbors: List[Tuple[int, "Device", int]],
) -> dict:
    """
    Build a dict of CDP OIDs for *device* given its neighbors.

    neighbors: list of (local_ifc_index, neighbor_device, neighbor_ifc_index)
    Returns {} for non-Cisco device types (Windows servers don't run CDP).
    """
    if device.device_type not in ("cisco_router", "cisco_switch"):
        return {}

    oids: dict = {}

    # Global CDP scalars
    oids[f"{_CDP_BASE}.1.3.1.0"] = Integer(1)    # cdpGlobalRun
    oids[f"{_CDP_BASE}.1.3.2.0"] = Integer(60)   # cdpGlobalMessageInterval
    oids[f"{_CDP_BASE}.1.3.3.0"] = Integer(180)  # cdpGlobalHoldTime

    for local_ifc_idx, nbr, nbr_ifc_idx in neighbors:
        entry_idx = 1   # one CDP neighbor per interface port

        # Lookup neighbor interface name
        nbr_ifc = next((i for i in nbr.interfaces if i.index == nbr_ifc_idx), None)
        nbr_ifc_name = nbr_ifc.name if nbr_ifc else f"Interface{nbr_ifc_idx}"

        # Encode neighbor IP as 4 raw bytes
        try:
            ip_bytes = bytes(int(x) for x in nbr.ip.split("."))
        except Exception:
            ip_bytes = b"\x00\x00\x00\x00"

        caps    = _CAPABILITIES.get(nbr.device_type, b"\x00\x00\x00\x01")
        plat    = _PLATFORM.get(nbr.device_type, "Unknown")
        version = _VERSION.get(nbr.device_type, "Unknown")

        pfx = f"{_CDP_CACHE}.{{col}}.{local_ifc_idx}.{entry_idx}"
        oids[pfx.format(col=1)] = Integer(local_ifc_idx)   # cdpCacheIfIndex
        oids[pfx.format(col=3)] = Integer(1)               # cdpCacheAddressType (ip)
        oids[pfx.format(col=4)] = OctetString(ip_bytes)    # cdpCacheAddress
        oids[pfx.format(col=5)] = version                  # cdpCacheVersion
        oids[pfx.format(col=6)] = nbr.name                 # cdpCacheDeviceId
        oids[pfx.format(col=7)] = nbr_ifc_name             # cdpCacheDevicePort
        oids[pfx.format(col=8)] = plat                     # cdpCachePlatform
        oids[pfx.format(col=9)] = OctetString(caps)        # cdpCacheCapabilities

    return oids


# --------------------------------------------------------------------------- #
#  Link persistence
# --------------------------------------------------------------------------- #
def load_links(path: str = _DEFAULT_LINKS_PATH) -> List["Link"]:
    from controller.models.link import Link
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            data = json.load(fh)
        return [Link.from_dict(d) for d in data]
    except Exception as exc:
        log.warning("Could not load links from %s: %s", path, exc)
        return []


def save_links(links: List["Link"], path: str = _DEFAULT_LINKS_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump([lnk.to_dict() for lnk in links], fh, indent=2)
