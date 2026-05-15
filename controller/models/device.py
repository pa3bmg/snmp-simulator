"""
Device dataclass — represents one simulated SNMP device.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List, Optional


DEVICE_TYPES = ["cisco_router", "cisco_switch", "windows_server"]


@dataclass
class Interface:
    """Represents a single simulated network interface."""
    index: int                  # ifIndex (1-based)
    name: str                   # e.g. "GigabitEthernet0/0"
    oper_status: int = 1        # 1=up, 2=down
    admin_status: int = 1       # 1=up, 2=down
    in_octets: int = 0
    out_octets: int = 0
    in_errors: int = 0
    out_errors: int = 0
    speed: int = 1_000_000_000  # bits/s (default 1 Gbps)

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Interface":
        return cls(**d)


@dataclass
class Device:
    """One simulated network device (router, switch, or server)."""
    device_type: str                    # cisco_router | cisco_switch | windows_server
    name: str                           # sysName
    ip: str                             # IP the agent binds to
    port: int = 161                     # UDP port (use 1161+ for non-root dev)
    community: str = "public"           # SNMPv2c community string
    num_interfaces: int = 4             # Number of simulated interfaces
    cpu_min: int = 5                    # Minimum CPU % (for fluctuation)
    cpu_max: int = 75                   # Maximum CPU %
    trap_destination: str = ""          # IP to send SNMPv2c traps to
    trap_port: int = 162                # Trap destination UDP port
    trap_community: str = "public"      # Community for outgoing traps

    # Extra OIDs imported from a discovery profile.  Merged into the agent's
    # OID table after the device profile sets up its own OIDs, so any OID
    # observed on a real device is faithfully replayed.
    extra_oids: dict = field(default_factory=dict)

    # Runtime state (not persisted between runs, but reset on load)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    interfaces: List[Interface] = field(default_factory=list)
    cpu_percent: int = 0
    uptime_ticks: int = 0              # sysUpTime in hundredths of a second

    def __post_init__(self):
        if not self.interfaces:
            self.interfaces = self._default_interfaces()

    def _default_interfaces(self) -> List[Interface]:
        names = self._interface_names()
        return [
            Interface(index=i + 1, name=names[i], speed=self._default_speed())
            for i in range(self.num_interfaces)
        ]

    def _interface_names(self) -> List[str]:
        if self.device_type == "cisco_router":
            bases = [f"GigabitEthernet0/{i}" for i in range(self.num_interfaces)]
        elif self.device_type == "cisco_switch":
            bases = [f"FastEthernet0/{i}" for i in range(self.num_interfaces)]
        else:
            bases = [f"Ethernet{i}" for i in range(self.num_interfaces)]
        return bases

    def _default_speed(self) -> int:
        if self.device_type == "windows_server":
            return 1_000_000_000
        return 1_000_000_000

    # ------------------------------------------------------------------ #
    #  Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "device_type": self.device_type,
            "name": self.name,
            "ip": self.ip,
            "port": self.port,
            "community": self.community,
            "num_interfaces": self.num_interfaces,
            "cpu_min": self.cpu_min,
            "cpu_max": self.cpu_max,
            "trap_destination": self.trap_destination,
            "trap_port": self.trap_port,
            "trap_community": self.trap_community,
            "extra_oids": self.extra_oids,
            # Persist interface config (names, admin status) but reset counters
            "interfaces": [
                {
                    "index": ifc.index,
                    "name": ifc.name,
                    "admin_status": ifc.admin_status,
                    "speed": ifc.speed,
                }
                for ifc in self.interfaces
            ],
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Device":
        d = dict(d)  # avoid mutating caller's dict
        ifc_data = d.pop("interfaces", [])
        d.setdefault("id", str(uuid.uuid4()))
        d.setdefault("extra_oids", {})
        obj = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        if ifc_data:
            obj.interfaces = [
                Interface(
                    index=i["index"],
                    name=i["name"],
                    admin_status=i.get("admin_status", 1),
                    oper_status=i.get("admin_status", 1),  # start oper = admin
                    speed=i.get("speed", 1_000_000_000),
                )
                for i in ifc_data
            ]
        return obj
