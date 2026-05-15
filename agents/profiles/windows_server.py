"""
Windows Server profile — builds the initial OID table for a simulated Windows Server host.

sysObjectID  : 1.3.6.1.4.1.311.1.1.3.1.2   (Microsoft Windows Server 2019)
CPU OID      : hrProcessorLoad  (HOST-RESOURCES-MIB)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pysnmp.proto.rfc1902 import (
    Counter32, Gauge32, Integer, ObjectIdentifier, OctetString, TimeTicks
)

if TYPE_CHECKING:
    from controller.models.device import Device

# sysObjectID — Microsoft Windows
SYS_OBJECT_ID = "1.3.6.1.4.1.311.1.1.3.1.2"

_SYS           = "1.3.6.1.2.1.1"
_IF            = "1.3.6.1.2.1.2"
_IF_TABLE      = "1.3.6.1.2.1.2.2.1"
_HR_DEVICE     = "1.3.6.1.2.1.25.3"          # HOST-RESOURCES-MIB hrDevice group
_HR_PROC_TABLE = "1.3.6.1.2.1.25.3.3.1"      # hrProcessorTable
_HR_STORAGE    = "1.3.6.1.2.1.25.2.3.1"      # hrStorageTable (optional, nice to have)


def build_oid_table(device: "Device") -> dict:
    table: dict = {}

    table[f"{_SYS}.1.0"] = (
        "Hardware: Intel64 Family 6 Model 85 Stepping 7 AT/AT COMPATIBLE - "
        "Software: Windows Version 10.0 (Build 17763 Multiprocessor Free)"
    )
    table["1.3.6.1.2.1.1.2.0"] = ObjectIdentifier(
        tuple(int(x) for x in SYS_OBJECT_ID.split("."))
    )
    table[f"{_SYS}.3.0"] = lambda: TimeTicks(device.uptime_ticks)
    table[f"{_SYS}.4.0"] = "admin@example.com"
    table[f"{_SYS}.5.0"] = device.name
    table[f"{_SYS}.6.0"] = "Data Center"
    table[f"{_SYS}.7.0"] = Integer(76)  # sysServices (host)

    # Interfaces (standard IF-MIB)
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

    # HOST-RESOURCES-MIB — CPU  (hrProcessorLoad.1)
    # Simulating a single logical CPU entry
    table[f"{_HR_PROC_TABLE}.1.1"] = Integer(1)   # hrDeviceIndex
    table[f"{_HR_PROC_TABLE}.2.1"] = lambda: Gauge32(device.cpu_percent)  # hrProcessorLoad

    # hrStorage — simple example: one disk (C:) with 100 GB total, 60 GB used
    table[f"{_HR_STORAGE}.1.1"] = Integer(1)
    table[f"{_HR_STORAGE}.2.1"] = ObjectIdentifier((1, 3, 6, 1, 2, 1, 25, 2, 1, 4))  # hrStorageFixedDisk
    table[f"{_HR_STORAGE}.3.1"] = OctetString("C:\\")
    table[f"{_HR_STORAGE}.4.1"] = Integer(4096)                 # hrStorageAllocationUnits (4 KB)
    table[f"{_HR_STORAGE}.5.1"] = Gauge32(100 * 1024 * 256)    # hrStorageSize (100 GB in 4 KB units)
    table[f"{_HR_STORAGE}.6.1"] = Gauge32(60 * 1024 * 256)     # hrStorageUsed  (60 GB)

    return table
