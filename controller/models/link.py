"""
Link dataclass — represents a physical cable between two device interfaces.
Used to populate CISCO-CDP-MIB (cdpCacheTable) on both ends.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class Link:
    """A point-to-point link between one interface on device A and one on device B."""
    device_a_id: str
    ifc_a_index: int          # ifIndex on device A
    device_b_id: str
    ifc_b_index: int          # ifIndex on device B
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "device_a_id": self.device_a_id,
            "ifc_a_index": self.ifc_a_index,
            "device_b_id": self.device_b_id,
            "ifc_b_index": self.ifc_b_index,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Link":
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            device_a_id=d["device_a_id"],
            ifc_a_index=int(d["ifc_a_index"]),
            device_b_id=d["device_b_id"],
            ifc_b_index=int(d["ifc_b_index"]),
        )
