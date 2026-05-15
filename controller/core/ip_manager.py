"""
IP alias manager — adds/removes IP aliases on the loopback/physical interface
so each simulated device can bind to a unique IP address.

Requires elevated privileges (root / Administrator / sudo).

Platform support:
  - macOS  : ifconfig lo0 alias <ip> / -alias <ip>
  - Linux  : ip addr add <ip>/32 dev lo  / ip addr del <ip>/32 dev lo
  - Windows: netsh interface ip add address "Loopback Pseudo-Interface 1" <ip> 255.255.255.255
             netsh interface ip delete address "Loopback Pseudo-Interface 1" <ip>

All operations are logged. Errors are surfaced as exceptions so the GUI
can report them to the user.
"""
from __future__ import annotations

import logging
import platform
import subprocess
import sys

log = logging.getLogger(__name__)

# Name of the loopback interface on each platform
_LOOPBACK = {
    "Darwin":  "lo0",
    "Linux":   "lo",
    "Windows": "Loopback Pseudo-Interface 1",
}


def _loopback() -> str:
    return _LOOPBACK.get(platform.system(), "lo")


def list_ips_on_interface(iface: str) -> list[str]:
    """Return all IP addresses (CIDR) currently assigned to *iface* (Linux only)."""
    result = subprocess.run(
        ["ip", "-4", "addr", "show", "dev", iface],
        capture_output=True, text=True,
    )
    addrs = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("inet "):
            # "inet 192.168.178.10/24 brd ... scope global enp7s0"
            parts = line.split()
            if len(parts) >= 2:
                addrs.append(parts[1])  # e.g. "192.168.178.10/24"
    return addrs


def add_ip_on_interface(ip_cidr: str, iface: str) -> None:
    """Add an IP address (CIDR notation, e.g. 192.168.1.10/24) to *iface*."""
    log.info("Adding %s to %s", ip_cidr, iface)
    _run(["ip", "addr", "add", ip_cidr, "dev", iface])


def remove_ip_on_interface(ip_cidr: str, iface: str) -> None:
    """Remove an IP address (CIDR notation) from *iface*."""
    log.info("Removing %s from %s", ip_cidr, iface)
    _run(["ip", "addr", "del", ip_cidr, "dev", iface])


def add_ip_alias(ip: str) -> None:
    """Add an IP alias to the loopback interface."""
    system = platform.system()
    log.info("Adding IP alias %s on %s", ip, system)

    if system == "Darwin":
        _run(["ifconfig", _loopback(), "alias", ip, "255.255.255.255"])
    elif system == "Linux":
        _run(["ip", "addr", "add", f"{ip}/32", "dev", _loopback()])
    elif system == "Windows":
        _run([
            "netsh", "interface", "ip", "add", "address",
            f'"{_loopback()}"', ip, "255.255.255.255",
        ])
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def remove_ip_alias(ip: str) -> None:
    """Remove an IP alias from the loopback interface."""
    system = platform.system()
    log.info("Removing IP alias %s on %s", ip, system)

    if system == "Darwin":
        _run(["ifconfig", _loopback(), "-alias", ip])
    elif system == "Linux":
        _run(["ip", "addr", "del", f"{ip}/32", "dev", _loopback()])
    elif system == "Windows":
        _run([
            "netsh", "interface", "ip", "delete", "address",
            f'"{_loopback()}"', ip,
        ])
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def is_elevated() -> bool:
    """Return True if the current process has elevated/root privileges."""
    if platform.system() == "Windows":
        import ctypes
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    else:
        import os
        return os.geteuid() == 0


def _run(cmd: list) -> None:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stderr: {result.stderr.strip()}"
        )
