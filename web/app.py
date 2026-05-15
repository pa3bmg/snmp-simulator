"""
SNMP Simulator — Web Controller
Flask + Flask-SocketIO

Run:
    python -m web.app [--host 0.0.0.0] [--port 5000]

With elevated privileges for port 161 + IP alias management:
    sudo python -m web.app
"""
from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import re
import sys
import threading
import time
from typing import Optional

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO

# Ensure project root is on sys.path when running as a script directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.core.agent_manager import AgentManager
from controller.core.config import load_devices, save_devices, load_discovery_profiles, save_discovery_profiles
from controller.core.discovery import DiscoveryEngine, DiscoveryResult
from controller.core.ip_manager import (
    add_ip_alias, is_elevated, remove_ip_alias,
    list_ips_on_interface, add_ip_on_interface, remove_ip_on_interface,
)
from controller.core.cdp_utils import build_cdp_oids, load_links, save_links
from controller.models.device import Device, DEVICE_TYPES
from controller.models.link import Link

log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(24)
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

_lock = threading.Lock()
_devices: list[Device] = []
_links: list[Link] = []

_TYPE_LABELS = {
    "cisco_router":   "Cisco Router",
    "cisco_switch":   "Cisco Switch",
    "windows_server": "Windows Server",
}


# ------------------------------------------------------------------ #
#  Callbacks from agent threads → emit SocketIO events to browser
# ------------------------------------------------------------------ #

def _on_trap(device: Device, trap_type: str, ifc_index: Optional[int]) -> None:
    socketio.emit("trap", {
        "device": device.name,
        "type":   trap_type,
        "ifc":    ifc_index,
    })


def _on_status(device_id: str, status: str) -> None:
    socketio.emit("status_change", {"id": device_id, "status": status})


manager = AgentManager(on_trap=_on_trap, on_status=_on_status)


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _find_device(device_id: str) -> Optional[Device]:
    with _lock:
        return next((d for d in _devices if d.id == device_id), None)


def _device_to_dict(d: Device) -> dict:
    return {
        "id":                d.id,
        "name":              d.name,
        "ip":                d.ip,
        "port":              d.port,
        "device_type":       d.device_type,
        "device_type_label": _TYPE_LABELS.get(d.device_type, d.device_type),
        "community":         d.community,
        "num_interfaces":    d.num_interfaces,
        "cpu_min":           d.cpu_min,
        "cpu_max":           d.cpu_max,
        "cpu_percent":       d.cpu_percent,
        "trap_destination":  d.trap_destination,
        "trap_port":         d.trap_port,
        "trap_community":    d.trap_community,
        "running":           manager.is_running(d.id),
        "ifc_up":            sum(1 for i in d.interfaces if i.oper_status == 1),
        "ifc_dn":            sum(1 for i in d.interfaces if i.oper_status == 2),
        "interfaces": [
            {"index": i.index, "name": i.name, "oper_status": i.oper_status}
            for i in d.interfaces
        ],
    }


def _persist() -> None:
    with _lock:
        save_devices(_devices)


def _persist_links() -> None:
    with _lock:
        save_links(_links)


def _rebuild_cdp(device_id: str) -> None:
    """Recompute CDP neighbour OIDs for *device_id* and push to its running agent."""
    d = _find_device(device_id)
    if d is None:
        return
    with _lock:
        links_snapshot = list(_links)
    neighbors = []
    for lnk in links_snapshot:
        if lnk.device_a_id == device_id:
            nbr = _find_device(lnk.device_b_id)
            if nbr:
                neighbors.append((lnk.ifc_a_index, nbr, lnk.ifc_b_index))
        elif lnk.device_b_id == device_id:
            nbr = _find_device(lnk.device_a_id)
            if nbr:
                neighbors.append((lnk.ifc_b_index, nbr, lnk.ifc_a_index))
    cdp_oids = build_cdp_oids(d, neighbors)
    manager.update_cdp_for_device(device_id, cdp_oids)


def _try_add_alias(ip: str) -> None:
    try:
        add_ip_alias(ip)
    except Exception as exc:
        log.warning("Could not add IP alias %s: %s", ip, exc)


def _try_remove_alias(ip: str) -> None:
    try:
        remove_ip_alias(ip)
    except Exception as exc:
        log.warning("Could not remove IP alias %s: %s", ip, exc)


# ------------------------------------------------------------------ #
#  Background task — push device states to browser every 3 s
# ------------------------------------------------------------------ #

def _background_updater() -> None:
    while True:
        time.sleep(3)
        with _lock:
            updates = [_device_to_dict(d) for d in _devices]
        socketio.emit("device_updates", updates)


# ------------------------------------------------------------------ #
#  Routes — page
# ------------------------------------------------------------------ #

@app.route("/")
def index():
    return render_template("index.html", elevated=is_elevated())


# ------------------------------------------------------------------ #
#  Routes — JSON API
# ------------------------------------------------------------------ #

@app.route("/api/devices", methods=["GET"])
def api_list():
    with _lock:
        data = [_device_to_dict(d) for d in _devices]
    return jsonify(data)


@app.route("/api/devices", methods=["POST"])
def api_add():
    body = request.get_json(force=True)
    try:
        device = _device_from_body(body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    with _lock:
        _devices.append(device)
    _persist()
    return jsonify(_device_to_dict(device)), 201


@app.route("/api/devices/<device_id>", methods=["GET"])
def api_get(device_id):
    d = _find_device(device_id)
    if not d:
        return jsonify({"error": "Not found"}), 404
    return jsonify(_device_to_dict(d))


@app.route("/api/devices/<device_id>", methods=["PUT"])
def api_update(device_id):
    d = _find_device(device_id)
    if not d:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(force=True)
    was_running = manager.is_running(device_id)
    if was_running:
        manager.stop_device(device_id)
    try:
        _apply_body(d, body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if was_running:
        manager.start_device(d)
    _persist()
    return jsonify(_device_to_dict(d))


@app.route("/api/devices/<device_id>", methods=["DELETE"])
def api_delete(device_id):
    global _devices
    d = _find_device(device_id)
    if not d:
        return jsonify({"error": "Not found"}), 404
    manager.stop_device(device_id)
    _try_remove_alias(d.ip)
    with _lock:
        _devices = [x for x in _devices if x.id != device_id]
    _persist()
    return jsonify({"ok": True})


@app.route("/api/devices/<device_id>/start", methods=["POST"])
def api_start(device_id):
    d = _find_device(device_id)
    if not d:
        return jsonify({"error": "Not found"}), 404
    if is_elevated():
        _try_add_alias(d.ip)
    ok = manager.start_device(d)
    if ok:
        _rebuild_cdp(device_id)
    return jsonify({"ok": ok, **_device_to_dict(d)})


@app.route("/api/devices/<device_id>/stop", methods=["POST"])
def api_stop(device_id):
    d = _find_device(device_id)
    if not d:
        return jsonify({"error": "Not found"}), 404
    manager.stop_device(device_id)
    _try_remove_alias(d.ip)
    return jsonify({"ok": True, **_device_to_dict(d)})


@app.route("/api/devices/start_all", methods=["POST"])
def api_start_all():
    with _lock:
        devs = list(_devices)
    for d in devs:
        if not manager.is_running(d.id):
            if is_elevated():
                _try_add_alias(d.ip)
            if manager.start_device(d):
                _rebuild_cdp(d.id)
    return jsonify({"ok": True})


@app.route("/api/devices/stop_all", methods=["POST"])
def api_stop_all():
    manager.stop_all()
    return jsonify({"ok": True})


@app.route("/api/devices/<device_id>/toggle_ifc", methods=["POST"])
def api_toggle_ifc(device_id):
    d = _find_device(device_id)
    if not d:
        return jsonify({"error": "Not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    ifc_idx = body.get("ifc_index")
    if ifc_idx is not None:
        target = next((i for i in d.interfaces if i.index == ifc_idx), None)
    else:
        # toggle first up interface, or bring first interface up if all down
        target = next((i for i in d.interfaces if i.oper_status == 1), None)
        if target is None and d.interfaces:
            target = d.interfaces[0]
    if target:
        target.oper_status = 2 if target.oper_status == 1 else 1
    return jsonify(_device_to_dict(d))


# ------------------------------------------------------------------ #
#  Profiles — named saved device sets
# ------------------------------------------------------------------ #

_PROFILES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "profiles",
)
_PROFILE_NAME_RE = re.compile(r'^[\w\- ]{1,64}$')


def _profile_path(name: str) -> str:
    safe = name.replace(" ", "_")
    return os.path.join(_PROFILES_DIR, f"{safe}.json")


@app.route("/api/profiles", methods=["GET"])
def api_profiles_list():
    """Return list of saved profile names with device counts."""
    os.makedirs(_PROFILES_DIR, exist_ok=True)
    profiles = []
    for fname in sorted(os.listdir(_PROFILES_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(_PROFILES_DIR, fname)
        try:
            with open(path) as fh:
                data = json.load(fh)
            profiles.append({
                "name":  data.get("name", fname[:-5]),
                "count": len(data.get("devices", [])),
                "saved": data.get("saved", ""),
            })
        except Exception:
            pass
    return jsonify(profiles)


@app.route("/api/profiles", methods=["POST"])
def api_profiles_save():
    """Save current device list under a given name."""
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name or not _PROFILE_NAME_RE.match(name):
        return jsonify({"error": "Profile name must be 1–64 alphanumeric/dash/space characters."}), 400
    os.makedirs(_PROFILES_DIR, exist_ok=True)
    with _lock:
        devices_data = [d.to_dict() for d in _devices]
    payload = {
        "name":    name,
        "saved":   time.strftime("%Y-%m-%d %H:%M:%S"),
        "devices": devices_data,
    }
    with open(_profile_path(name), "w") as fh:
        json.dump(payload, fh, indent=2)
    log.info("Saved profile '%s' with %d device(s)", name, len(devices_data))
    return jsonify({"ok": True, "name": name, "count": len(devices_data)}), 201


@app.route("/api/profiles/<path:name>", methods=["GET"])
def api_profiles_load(name):
    """Load a saved profile — replaces the current device list."""
    global _devices
    path = _profile_path(name)
    if not os.path.exists(path):
        return jsonify({"error": "Profile not found"}), 404
    try:
        with open(path) as fh:
            data = json.load(fh)
    except Exception as exc:
        return jsonify({"error": f"Could not read profile: {exc}"}), 500

    # Stop all running agents first
    manager.stop_all()

    loaded = []
    for d_dict in data.get("devices", []):
        try:
            loaded.append(Device.from_dict(d_dict))
        except Exception:
            pass

    with _lock:
        _devices = loaded
    _persist()
    log.info("Loaded profile '%s' with %d device(s)", name, len(loaded))
    return jsonify({"ok": True, "name": name, "count": len(loaded),
                    "devices": [_device_to_dict(d) for d in loaded]})


@app.route("/api/profiles/<path:name>", methods=["DELETE"])
def api_profiles_delete(name):
    """Delete a saved profile."""
    path = _profile_path(name)
    if not os.path.exists(path):
        return jsonify({"error": "Profile not found"}), 404
    os.remove(path)
    log.info("Deleted profile '%s'", name)
    return jsonify({"ok": True})


# ------------------------------------------------------------------ #
#  Links — topology connections between device interfaces
# ------------------------------------------------------------------ #

def _link_to_dict(lnk: Link) -> dict:
    """Enrich link with device/interface labels for the UI."""
    da = _find_device(lnk.device_a_id)
    db = _find_device(lnk.device_b_id)

    def _ifc_name(dev, ifc_idx):
        if dev is None:
            return f"ifc{ifc_idx}"
        ifc = next((i for i in dev.interfaces if i.index == ifc_idx), None)
        return ifc.name if ifc else f"ifc{ifc_idx}"

    return {
        "id": lnk.id,
        "device_a_id":   lnk.device_a_id,
        "device_a_name": da.name if da else lnk.device_a_id,
        "ifc_a_index":   lnk.ifc_a_index,
        "ifc_a_name":    _ifc_name(da, lnk.ifc_a_index),
        "device_b_id":   lnk.device_b_id,
        "device_b_name": db.name if db else lnk.device_b_id,
        "ifc_b_index":   lnk.ifc_b_index,
        "ifc_b_name":    _ifc_name(db, lnk.ifc_b_index),
    }


@app.route("/api/links", methods=["GET"])
def api_links_list():
    with _lock:
        links_snapshot = list(_links)
    return jsonify([_link_to_dict(lnk) for lnk in links_snapshot])


@app.route("/api/links", methods=["POST"])
def api_links_create():
    global _links
    body = request.get_json(force=True) or {}
    try:
        lnk = Link(
            device_a_id=body["device_a_id"],
            ifc_a_index=int(body["ifc_a_index"]),
            device_b_id=body["device_b_id"],
            ifc_b_index=int(body["ifc_b_index"]),
        )
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    # Validate both devices exist
    if not _find_device(lnk.device_a_id) or not _find_device(lnk.device_b_id):
        return jsonify({"error": "One or both devices not found"}), 404

    with _lock:
        _links.append(lnk)
    _persist_links()

    # Push CDP updates to both running agents
    _rebuild_cdp(lnk.device_a_id)
    _rebuild_cdp(lnk.device_b_id)

    return jsonify(_link_to_dict(lnk)), 201


@app.route("/api/links/<link_id>", methods=["DELETE"])
def api_links_delete(link_id):
    global _links
    with _lock:
        target = next((lnk for lnk in _links if lnk.id == link_id), None)
        if target is None:
            return jsonify({"error": "Not found"}), 404
        _links = [lnk for lnk in _links if lnk.id != link_id]
    _persist_links()

    # Push CDP updates (neighbor removed from both devices)
    _rebuild_cdp(target.device_a_id)
    _rebuild_cdp(target.device_b_id)

    return jsonify({"ok": True})


# ------------------------------------------------------------------ #
#  Canvas layout — node positions for the visual planner
# ------------------------------------------------------------------ #

_CANVAS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "canvas.json",
)


@app.route("/api/canvas/layout", methods=["GET"])
def api_canvas_layout_get():
    """Return saved canvas node positions {device_id: {x, y}}."""
    if not os.path.exists(_CANVAS_PATH):
        return jsonify({})
    try:
        with open(_CANVAS_PATH) as fh:
            return jsonify(json.load(fh))
    except Exception:
        return jsonify({})


@app.route("/api/canvas/layout", methods=["POST"])
def api_canvas_layout_save():
    """Persist canvas node positions {device_id: {x, y}}."""
    body = request.get_json(force=True) or {}
    os.makedirs(os.path.dirname(_CANVAS_PATH), exist_ok=True)
    with open(_CANVAS_PATH, "w") as fh:
        json.dump(body, fh)
    return jsonify({"ok": True})


# ------------------------------------------------------------------ #
#  Server network interface management
# ------------------------------------------------------------------ #

_SIM_IFACE = "enp7s0"


@app.route("/api/server/interfaces", methods=["GET"])
def api_server_interfaces_list():
    """List all network interfaces on the server (excluding loopback)."""
    import os
    try:
        ifaces = [
            name for name in os.listdir("/sys/class/net")
            if name != "lo"
        ]
        ifaces.sort()
        return jsonify({"interfaces": ifaces, "active": _SIM_IFACE})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/server/iface", methods=["PUT"])
def api_server_iface_set():
    """Switch the active sim interface. Body: {iface: "ethX"}"""
    global _SIM_IFACE
    import os
    body = request.get_json(force=True) or {}
    iface = body.get("iface", "").strip()
    if not iface:
        return jsonify({"error": "iface is required"}), 400
    if not os.path.exists(f"/sys/class/net/{iface}"):
        return jsonify({"error": f"Interface '{iface}' not found"}), 404
    _SIM_IFACE = iface
    return jsonify({"ok": True, "iface": _SIM_IFACE})


@app.route("/api/server/ips", methods=["GET"])
def api_server_ips_list():
    """List all IPs currently on the sim interface."""
    try:
        addrs = list_ips_on_interface(_SIM_IFACE)
        return jsonify({"iface": _SIM_IFACE, "addresses": addrs})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/server/ips", methods=["POST"])
def api_server_ips_add():
    """Add an IP address to the sim interface. Body: {ip_cidr: "x.x.x.x/24"}"""
    body = request.get_json(force=True) or {}
    ip_cidr = body.get("ip_cidr", "").strip()
    if not ip_cidr:
        return jsonify({"error": "ip_cidr is required (e.g. 192.168.1.10/24)"}), 400
    # Basic CIDR validation
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$", ip_cidr):
        return jsonify({"error": "Invalid CIDR format. Use x.x.x.x/prefix"}), 400
    try:
        add_ip_on_interface(ip_cidr, _SIM_IFACE)
        return jsonify({"ok": True, "ip_cidr": ip_cidr})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.route("/api/server/ips/bulk", methods=["POST"])
def api_server_ips_bulk():
    """
    Add a range of IPs to the sim interface.
    Body: {start_ip: "x.x.x.x", end_ip: "x.x.x.x", prefix: 24}
    Already-existing addresses are silently skipped.
    """
    import ipaddress
    body = request.get_json(force=True) or {}
    start_ip = body.get("start_ip", "").strip()
    end_ip   = body.get("end_ip",   "").strip()
    prefix   = int(body.get("prefix", 24))
    if not start_ip or not end_ip:
        return jsonify({"error": "start_ip and end_ip are required"}), 400
    try:
        start = int(ipaddress.IPv4Address(start_ip))
        end   = int(ipaddress.IPv4Address(end_ip))
    except Exception:
        return jsonify({"error": "Invalid IP address in range"}), 400
    if start > end:
        return jsonify({"error": "start_ip must be <= end_ip"}), 400
    if (end - start) > 254:
        return jsonify({"error": "Range too large (max 255 addresses)"}), 400
    added = []
    skipped = []
    errors = []
    for i in range(start, end + 1):
        ip_str  = str(ipaddress.IPv4Address(i))
        ip_cidr = f"{ip_str}/{prefix}"
        try:
            add_ip_on_interface(ip_cidr, _SIM_IFACE)
            added.append(ip_cidr)
        except Exception as exc:
            msg = str(exc).lower()
            if "exists" in msg or "eexist" in msg or "rtnetlink answers: file exists" in msg:
                skipped.append(ip_cidr)
            else:
                errors.append({"ip": ip_cidr, "error": str(exc)})
    return jsonify({"ok": True, "added": len(added), "skipped": len(skipped), "errors": errors})

@app.route("/api/server/ips", methods=["DELETE"])
def api_server_ips_remove():
    """Remove an IP address from the sim interface. Body: {ip_cidr: "x.x.x.x/24"}"""
    body = request.get_json(force=True) or {}
    ip_cidr = body.get("ip_cidr", "").strip()
    if not ip_cidr:
        return jsonify({"error": "ip_cidr is required"}), 400
    try:
        remove_ip_on_interface(ip_cidr, _SIM_IFACE)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ------------------------------------------------------------------ #
#  Discovery — scan real devices and manage discovery profiles
# ------------------------------------------------------------------ #

@app.route("/api/discovery/scan", methods=["POST"])
def api_discovery_scan():
    """
    Scan a real device via SNMP and return a DiscoveryResult dict.
    Body: {ip, community, port, version, include_bridge, timeout, retries}
    This runs synchronously (blocking) in the request thread.
    """
    import asyncio
    body = request.get_json(force=True) or {}
    ip = body.get("ip", "").strip()
    if not ip:
        return jsonify({"error": "ip is required"}), 400

    community      = body.get("community", "public")
    port           = int(body.get("port", 161))
    version        = body.get("version", "v2c")
    include_bridge = bool(body.get("include_bridge", False))
    timeout        = int(body.get("timeout", 5))
    retries        = int(body.get("retries", 2))

    try:
        engine = DiscoveryEngine()
        result: DiscoveryResult = asyncio.run(
            engine.scan(
                ip=ip,
                community=community,
                port=port,
                version=version,
                include_bridge=include_bridge,
                timeout=timeout,
                retries=retries,
            )
        )
        data = result.to_dict()
        data["inferred_device_type"] = result.infer_device_type()
        return jsonify(data)
    except Exception as exc:
        log.error("Discovery scan failed for %s: %s", ip, exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/discovery/profiles", methods=["GET"])
def api_discovery_profiles_list():
    """Return all saved discovery profiles."""
    return jsonify(load_discovery_profiles())


@app.route("/api/discovery/profiles", methods=["POST"])
def api_discovery_profiles_save():
    """Save a discovery profile. Body: {name, ...DiscoveryResult fields}"""
    import uuid as _uuid
    body = request.get_json(force=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    profiles = load_discovery_profiles()
    # Replace existing profile with same name
    profiles = [p for p in profiles if p.get("name") != name]
    body["id"]   = body.get("id") or str(_uuid.uuid4())
    body["name"] = name
    profiles.append(body)
    save_discovery_profiles(profiles)
    return jsonify({"ok": True, "id": body["id"]}), 201


@app.route("/api/discovery/profiles/<profile_id>", methods=["DELETE"])
def api_discovery_profiles_delete(profile_id):
    """Delete a discovery profile by id."""
    profiles = load_discovery_profiles()
    new_profiles = [p for p in profiles if p.get("id") != profile_id]
    if len(new_profiles) == len(profiles):
        return jsonify({"error": "Profile not found"}), 404
    save_discovery_profiles(new_profiles)
    return jsonify({"ok": True})


# ------------------------------------------------------------------ #
#  Device construction from request body
# ------------------------------------------------------------------ #

def _device_from_body(body: dict) -> Device:
    name = body.get("name", "").strip()
    ip   = body.get("ip", "").strip()
    if not name:
        raise ValueError("name is required")
    if not ip:
        raise ValueError("ip is required")
    device = Device(
        device_type      = body.get("device_type", "cisco_router"),
        name             = name,
        ip               = ip,
        port             = int(body.get("port", 161)),
        community        = body.get("community", "public"),
        num_interfaces   = int(body.get("num_interfaces", 4)),
        cpu_min          = int(body.get("cpu_min", 5)),
        cpu_max          = int(body.get("cpu_max", 75)),
        trap_destination = body.get("trap_destination", ""),
        trap_port        = int(body.get("trap_port", 162)),
        trap_community   = body.get("trap_community", "public"),
        extra_oids       = body.get("extra_oids") or {},
    )
    # Apply discovered interface names if supplied
    ifc_names = body.get("ifc_names") or []
    if ifc_names:
        for ifc, name in zip(device.interfaces, ifc_names):
            ifc.name = name
    return device


def _apply_body(d: Device, body: dict) -> None:
    if "name" in body:
        d.name = body["name"].strip()
    if "device_type" in body:
        d.device_type = body["device_type"]
    if "ip" in body:
        d.ip = body["ip"].strip()
    if "port" in body:
        d.port = int(body["port"])
    if "community" in body:
        d.community = body["community"]
    old_num = d.num_interfaces
    if "num_interfaces" in body:
        d.num_interfaces = int(body["num_interfaces"])
    if "cpu_min" in body:
        d.cpu_min = int(body["cpu_min"])
    if "cpu_max" in body:
        d.cpu_max = int(body["cpu_max"])
    if "trap_destination" in body:
        d.trap_destination = body["trap_destination"].strip()
    if "trap_port" in body:
        d.trap_port = int(body["trap_port"])
    if "trap_community" in body:
        d.trap_community = body["trap_community"]
    if d.num_interfaces != old_num:
        d.interfaces = d._default_interfaces()


# ------------------------------------------------------------------ #
#  Main
# ------------------------------------------------------------------ #

def main() -> None:
    global _devices

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="SNMP Simulator Web Controller")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="HTTP port (default: 5000)")
    args = parser.parse_args()

    _devices = load_devices()
    log.info("Loaded %d device(s) from config", len(_devices))

    global _links
    _links = load_links()
    log.info("Loaded %d link(s) from config", len(_links))

    if not is_elevated():
        log.warning("Not running as root/admin — IP alias management disabled")

    atexit.register(manager.stop_all)

    # Start background state broadcaster
    threading.Thread(target=_background_updater, daemon=True, name="bg-updater").start()

    log.info("Web controller available at http://%s:%s", args.host, args.port)
    socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
