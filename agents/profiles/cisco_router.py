"""
Cisco Router profile — builds the initial OID table for a simulated Cisco IOS router.

sysObjectID: 1.3.6.1.4.1.9.1.1   (Cisco 7206 — recognised by CA Spectrum)
CPU OID    : cpmCPUTotal5min  (CISCO-PROCESS-MIB)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pysnmp.proto.rfc1902 import (
    Counter32, Gauge32, Integer, ObjectIdentifier, OctetString, TimeTicks
)

if TYPE_CHECKING:
    from controller.models.device import Device

# sysObjectID — Cisco 7206 VXR (widely used; CA Spectrum models it correctly)
SYS_OBJECT_ID = "1.3.6.1.4.1.9.1.320"

# OID roots
_SYS      = "1.3.6.1.2.1.1"
_IF       = "1.3.6.1.2.1.2"
_IF_TABLE = "1.3.6.1.2.1.2.2.1"
_CPU      = "1.3.6.1.4.1.9.9.109.1.1.1.1"   # cpmCPUTotalEntry


def build_oid_table(device: "Device") -> dict:
    """
    Return a dict { oid_str -> value_or_callable } for a Cisco router.
    Callable entries are re-evaluated on every GET (dynamic values).
    """
    table: dict = {}

    # ------------------------------------------------------------------ #
    #  SNMPv2-MIB / system group
    # ------------------------------------------------------------------ #
    ios_version = "Cisco IOS Software, Version 15.7(3)M5, RELEASE SOFTWARE"
    table[f"{_SYS}.1.0"] = f"{ios_version}\r\nCopyright (c) Cisco Systems, Inc."
    table[f"{_SYS}.2.0"] = "42"                                    # sysObjectID (scalar)
    table[f"{_SYS}.3.0"] = lambda: TimeTicks(device.uptime_ticks)  # sysUpTime
    table[f"{_SYS}.4.0"] = "admin@example.com"                     # sysContact
    table[f"{_SYS}.5.0"] = device.name                             # sysName
    table[f"{_SYS}.6.0"] = "Server Room 1"                         # sysLocation
    table[f"{_SYS}.7.0"] = Integer(78)                              # sysServices (router=78)
    table["1.3.6.1.2.1.1.2.0"] = ObjectIdentifier(               # sysObjectID (proper)
        tuple(int(x) for x in SYS_OBJECT_ID.split("."))
    )

    # ------------------------------------------------------------------ #
    #  IF-MIB — interfaces group scalar
    # ------------------------------------------------------------------ #
    table[f"{_IF}.1.0"] = lambda: Integer(len(device.interfaces))  # ifNumber

    # ------------------------------------------------------------------ #
    #  IF-MIB — ifTable per interface
    # ------------------------------------------------------------------ #
    for ifc in device.interfaces:
        i = ifc.index
        table[f"{_IF_TABLE}.1.{i}"]  = Integer(i)                          # ifIndex
        table[f"{_IF_TABLE}.2.{i}"]  = ifc.name                            # ifDescr
        table[f"{_IF_TABLE}.3.{i}"]  = Integer(6)                          # ifType (ethernetCsmacd)
        table[f"{_IF_TABLE}.4.{i}"]  = Integer(1500)                       # ifMtu
        table[f"{_IF_TABLE}.5.{i}"]  = Gauge32(ifc.speed)                  # ifSpeed
        table[f"{_IF_TABLE}.6.{i}"]  = OctetString(b"\x00" * 6)            # ifPhysAddress (placeholder)
        table[f"{_IF_TABLE}.7.{i}"]  = lambda ifc=ifc: Integer(ifc.admin_status)   # ifAdminStatus
        table[f"{_IF_TABLE}.8.{i}"]  = lambda ifc=ifc: Integer(ifc.oper_status)    # ifOperStatus
        table[f"{_IF_TABLE}.10.{i}"] = lambda ifc=ifc: Counter32(ifc.in_octets % 2**32)   # ifInOctets
        table[f"{_IF_TABLE}.16.{i}"] = lambda ifc=ifc: Counter32(ifc.out_octets % 2**32)  # ifOutOctets
        table[f"{_IF_TABLE}.14.{i}"] = lambda ifc=ifc: Counter32(ifc.in_errors)            # ifInErrors
        table[f"{_IF_TABLE}.20.{i}"] = lambda ifc=ifc: Counter32(ifc.out_errors)           # ifOutErrors

    # ------------------------------------------------------------------ #
    #  CISCO-PROCESS-MIB — CPU
    #  cpmCPUTotalEntry.1 (cpmCPUTotalPhysicalIndex)  .1.1
    #  cpmCPUTotal5min    (1.3.6.1.4.1.9.9.109.1.1.1.1.8.1)
    # ------------------------------------------------------------------ #
    table[f"{_CPU}.1.1"] = Integer(1)                               # cpmCPUTotalPhysicalIndex
    table[f"{_CPU}.8.1"] = lambda: Gauge32(device.cpu_percent)     # cpmCPUTotal5min

    return table
