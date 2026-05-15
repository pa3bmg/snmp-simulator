"""
Config persistence — load/save device definitions and discovery profiles to/from JSON.
"""
from __future__ import annotations

import json
import os
from typing import List

from controller.models.device import Device

_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "config",
)

_DEFAULT_PATH          = os.path.join(_CONFIG_DIR, "devices.json")
_PROFILES_PATH         = os.path.join(_CONFIG_DIR, "discovery_profiles.json")


def config_path() -> str:
    return _DEFAULT_PATH


def load_devices(path: str = _DEFAULT_PATH) -> List[Device]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    devices = []
    for d in data:
        try:
            devices.append(Device.from_dict(d))
        except Exception:
            pass  # skip corrupted entries
    return devices


def save_devices(devices: List[Device], path: str = _DEFAULT_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([d.to_dict() for d in devices], fh, indent=2)


# ---------------------------------------------------------------------------
#  Discovery profile persistence
# ---------------------------------------------------------------------------

def load_discovery_profiles(path: str = _PROFILES_PATH) -> List[dict]:
    """Return list of saved discovery profile dicts."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except Exception:
            return []


def save_discovery_profiles(profiles: List[dict], path: str = _PROFILES_PATH) -> None:
    """Persist the list of discovery profiles to disk."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(profiles, fh, indent=2)
