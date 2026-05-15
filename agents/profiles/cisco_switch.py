"""
Cisco Switch profile — builds the initial OID table for a simulated Cisco Catalyst switch.

sysObjectID: 1.3.6.1.4.1.9.1.516  (Cisco Catalyst 3750 — recognised by CA Spectrum)
CPU OID    : cpmCPUTotal5min  (CISCO-PROCESS-MIB)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pysnmp.proto.rfc1902 import (
    Counter32, Gauge32, Integer, ObjectIdentifier, OctetString, TimeTicks
)

if TYPE_CHECKING:
    from controller.models.device import Device

# sysObjectID — Cisco Catalyst 3750
SYS_OBJECT_ID = "1.3.6.1.4.1.9.1.616"

_SYS      = "1.3.6.1.2.1.1"
_IF       = "1.3.6.1.2.1.2"
_IF_TABLE = "1.3.6.1.2.1.2.2.1"
_CPU      = "1.3.6.1.4.1.9.9.109.1.1.1.1"


def build_oid_table(device: "Device") -> dict:
    table: dict = {}

    ios_version = "Cisco IOS Software, Catalyst L3 Switch Software (CAT3K_CAA-UNIVERSALK9-M), Version 15.2(4)E"
    table[f"{_SYS}.1.0"] = f"{ios_version}\r\nCopyright (c) Cisco Systems, Inc."
    table["1.3.6.1.2.1.1.2.0"] = ObjectIdentifier(
        tuple(int(x) for x in SYS_OBJECT_ID.split("."))
    )
    table[f"{_SYS}.3.0"] = lambda: TimeTicks(device.uptime_ticks)
    table[f"{_SYS}.4.0"] = "admin@example.com"
    table[f"{_SYS}.5.0"] = device.name
    table[f"{_SYS}.6.0"] = "Wiring Closet"
    table[f"{_SYS}.7.0"] = Integer(74)   # sysServices = switch (layer 2)

    table[f"{_IF}.1.0"] = lambda: Integer(len(device.interfaces))

    for ifc in device.interfaces:
        i = ifc.index
        table[f"{_IF_TABLE}.1.{i}"]  = Integer(i)
        table[f"{_IF_TABLE}.2.{i}"]  = ifc.name
        table[f"{_IF_TABLE}.3.{i}"]  = Integer(6)
        table[f"{_IF_TABLE}.4.{i}"]  = Integer(1500)
        table[f"{_IF_TABLE}.5.{i}"]  = Gauge32(ifc.speed)
        table[f"{_IF_TABLE}.6.{i}"]  = OctetString(b"\x00" * 6)
        table[f"{_IF_TABLE}.7.{i}"]  = lambda ifc=ifc: Integer(ifc.admin_status)
        table[f"{_IF_TABLE}.8.{i}"]  = lambda ifc=ifc: Integer(ifc.oper_status)
        table[f"{_IF_TABLE}.10.{i}"] = lambda ifc=ifc: Counter32(ifc.in_octets % 2**32)
        table[f"{_IF_TABLE}.16.{i}"] = lambda ifc=ifc: Counter32(ifc.out_octets % 2**32)
        table[f"{_IF_TABLE}.14.{i}"] = lambda ifc=ifc: Counter32(ifc.in_errors)
        table[f"{_IF_TABLE}.20.{i}"] = lambda ifc=ifc: Counter32(ifc.out_errors)

    # CPU
    table[f"{_CPU}.1.1"] = Integer(1)
    table[f"{_CPU}.8.1"] = lambda: Gauge32(device.cpu_percent)

    return table
