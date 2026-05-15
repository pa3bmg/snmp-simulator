"""
Trap sender — sends SNMPv2c notifications (traps) to a target manager.

Supported trap types:
  - linkDown  (OID 1.3.6.1.6.3.1.1.5.3)
  - linkUp    (OID 1.3.6.1.6.3.1.1.5.4)
  - cpuHigh   (enterprise trap, OID 1.3.6.1.4.1.9.9.109.0.1)

Each trap includes:
  - sysUpTime
  - snmpTrapOID
  - device-specific varbinds (ifIndex for link traps, cpuLoad for cpuHigh)
"""
from __future__ import annotations

import logging
import socket
import struct
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from controller.models.device import Device

log = logging.getLogger(__name__)

# Well-known OIDs
_SNMP_TRAP_OID        = "1.3.6.1.6.3.1.1.4.1.0"
_SYS_UPTIME_OID       = "1.3.6.1.2.1.1.3.0"
_IF_INDEX_OID_PREFIX  = "1.3.6.1.2.1.2.2.1.1"   # ifIndex.N
_IF_OPER_OID_PREFIX   = "1.3.6.1.2.1.2.2.1.8"   # ifOperStatus.N
_CPU_LOAD_OID         = "1.3.6.1.4.1.9.9.109.1.1.1.1.8.1"  # cpmCPUTotal5min.1

TRAP_OIDS = {
    "linkDown": "1.3.6.1.6.3.1.1.5.3",
    "linkUp":   "1.3.6.1.6.3.1.1.5.4",
    "cpuHigh":  "1.3.6.1.4.1.9.9.109.0.1",
}


def send_trap(device: "Device", trap_type: str, ifc_index: int | None = None) -> None:
    """
    Build and send a SNMPv2c TRAP PDU via raw UDP.

    Uses a minimal hand-crafted BER encoder so there is no dependency on
    an asyncio event loop in the calling thread.
    """
    if not device.trap_destination:
        return
    if trap_type not in TRAP_OIDS:
        log.warning("Unknown trap type: %s", trap_type)
        return

    trap_oid = TRAP_OIDS[trap_type]
    uptime   = device.uptime_ticks

    varbinds = [
        (_SYS_UPTIME_OID,  "timeticks", uptime),
        (_SNMP_TRAP_OID,   "oid",       trap_oid),
    ]

    if trap_type in ("linkDown", "linkUp") and ifc_index is not None:
        oper = 2 if trap_type == "linkDown" else 1
        varbinds.append((f"{_IF_INDEX_OID_PREFIX}.{ifc_index}", "integer", ifc_index))
        varbinds.append((f"{_IF_OPER_OID_PREFIX}.{ifc_index}", "integer", oper))
    elif trap_type == "cpuHigh":
        varbinds.append((_CPU_LOAD_OID, "integer", device.cpu_percent))

    try:
        pdu = _build_v2c_trap(device.community, varbinds)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(pdu, (device.trap_destination, device.trap_port))
        sock.close()
        log.info(
            "Trap %s sent from %s to %s:%s",
            trap_type, device.name, device.trap_destination, device.trap_port
        )
    except OSError as exc:
        log.error("Failed to send trap %s: %s", trap_type, exc)


# ------------------------------------------------------------------ #
#  Minimal BER / SNMPv2c PDU builder
# ------------------------------------------------------------------ #

def _tlv(tag: int, value: bytes) -> bytes:
    length = len(value)
    if length < 0x80:
        return bytes([tag]) + bytes([length]) + value
    elif length < 0x100:
        return bytes([tag, 0x81, length]) + value
    else:
        return bytes([tag, 0x82]) + struct.pack(">H", length) + value


def _encode_int(value: int) -> bytes:
    if value == 0:
        return b"\x00"
    result = b""
    n = value
    while n:
        result = bytes([n & 0xFF]) + result
        n >>= 8
    # ensure sign bit is 0 for positive ints
    if result[0] & 0x80:
        result = b"\x00" + result
    return result


def _encode_oid(oid_str: str) -> bytes:
    parts = [int(x) for x in oid_str.strip(".").split(".")]
    # first two components are combined: first*40 + second
    encoded = [parts[0] * 40 + parts[1]]
    for part in parts[2:]:
        if part < 0x80:
            encoded.append(part)
        else:
            # multi-byte base-128 encoding
            octets = []
            while part:
                octets.insert(0, part & 0x7F)
                part >>= 7
            for j, o in enumerate(octets):
                if j < len(octets) - 1:
                    encoded.append(o | 0x80)
                else:
                    encoded.append(o)
    return bytes(encoded)


def _encode_varbind(oid_str: str, vtype: str, value) -> bytes:
    oid_bytes = _tlv(0x06, _encode_oid(oid_str))
    if vtype == "integer":
        val_bytes = _tlv(0x02, _encode_int(int(value)))
    elif vtype == "timeticks":
        val_bytes = _tlv(0x43, _encode_int(int(value)))
    elif vtype == "oid":
        val_bytes = _tlv(0x06, _encode_oid(str(value)))
    elif vtype == "string":
        val_bytes = _tlv(0x04, str(value).encode())
    else:
        val_bytes = _tlv(0x05, b"")  # Null fallback
    return _tlv(0x30, oid_bytes + val_bytes)


def _build_v2c_trap(community: str, varbinds: list) -> bytes:
    # Encode varbind list
    vb_list = b"".join(_encode_varbind(*vb) for vb in varbinds)
    vb_sequence = _tlv(0x30, vb_list)

    # SNMPv2c TRAP PDU (tag 0xA7)
    # request-id, error-status, error-index = 0
    pdu_body = (
        _tlv(0x02, _encode_int(1))   # request-id
        + _tlv(0x02, b"\x00")        # error-status
        + _tlv(0x02, b"\x00")        # error-index
        + vb_sequence
    )
    trap_pdu = _tlv(0xA7, pdu_body)

    # Message: version (1=v2c), community, PDU
    message = (
        _tlv(0x02, b"\x01")                         # version 1 = SNMPv2c
        + _tlv(0x04, community.encode("ascii"))       # community
        + trap_pdu
    )
    return _tlv(0x30, message)
