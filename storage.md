# SNMP Simulator — Storage Reference

All persistent data is stored as plain JSON files under the `config/` directory inside the installation folder.

Default installation path: `/opt/snmpsim/`

---

## Directory layout

```
/opt/snmpsim/
├── config/
│   ├── devices.json               ← Saved device definitions
│   └── discovery_profiles.json    ← Saved SNMP discovery scan results
├── agents/                        ← SNMP agent code (read-only at runtime)
├── controller/                    ← Controller logic (read-only at runtime)
├── traps/                         ← Trap sender (read-only at runtime)
├── web/                           ← Flask web UI (read-only at runtime)
└── .venv/                         ← Python virtual environment
```

---

## config/devices.json

**What it stores:** All simulated devices that have been added via the Web UI (Devices tab).

**When it is written:** Every time you add, edit, delete, start, or stop a device.

**Format:** JSON array — one object per device.

```json
[
  {
    "id": "a1b2c3d4-...",
    "device_type": "cisco_router",
    "name": "R1",
    "ip": "192.168.1.10",
    "port": 161,
    "community": "public",
    "num_interfaces": 4,
    "cpu_min": 5,
    "cpu_max": 75,
    "trap_destination": "192.168.1.1",
    "trap_port": 162,
    "trap_community": "public",
    "extra_oids": {},
    "interfaces": [
      {
        "index": 1,
        "name": "GigabitEthernet0/0",
        "oper_status": 1,
        "admin_status": 1,
        "in_octets": 0,
        "out_octets": 0,
        "in_errors": 0,
        "out_errors": 0,
        "speed": 1000000000
      }
    ]
  }
]
```

**Key fields:**

| Field | Description |
|---|---|
| `id` | Unique UUID — generated automatically |
| `device_type` | `cisco_router`, `cisco_switch`, or `windows_server` |
| `name` | Displayed name / sysName |
| `ip` | IP address the SNMP agent binds to |
| `port` | UDP port (normally 161) |
| `community` | SNMPv2c community string |
| `num_interfaces` | Number of simulated interfaces |
| `cpu_min` / `cpu_max` | CPU % fluctuation range |
| `trap_destination` | IP address to send traps to (empty = disabled) |
| `extra_oids` | OIDs imported from a discovery profile |
| `interfaces` | List of interface state objects |

**Note:** `cpu_percent`, `uptime_ticks` are runtime state — they reset to `0` after every restart and are not persisted.

---

## config/discovery_profiles.json

**What it stores:** Results of SNMP discovery scans saved from the Discovery tab.

**When it is written:** Every time you click **Save Profile** after a scan, and every time you delete a profile.

**Format:** JSON array — one object per saved scan.

```json
[
  {
    "id": "f9e8d7c6-...",
    "name": "Core Switch",
    "target_ip": "10.0.0.1",
    "timestamp": "2026-05-15T10:23:45",
    "device_type": "cisco_switch",
    "system_info": {
      "sysDescr": "Cisco IOS Software ...",
      "sysName": "SW-CORE",
      "sysLocation": "Server Room",
      "sysContact": "admin@example.com"
    },
    "interfaces": [
      {
        "index": 1,
        "name": "GigabitEthernet1/0/1",
        "oper_status": 1,
        "admin_status": 1,
        "speed": 1000000000
      }
    ],
    "cdp_neighbors": [
      {
        "local_interface": "GigabitEthernet1/0/1",
        "neighbor_ip": "10.0.0.2",
        "neighbor_port": "GigabitEthernet0/0"
      }
    ],
    "lldp_neighbors": [],
    "raw_oids": {
      "1.3.6.1.2.1.1.1.0": "Cisco IOS Software ...",
      "1.3.6.1.2.1.1.5.0": "SW-CORE"
    }
  }
]
```

**Key fields:**

| Field | Description |
|---|---|
| `id` | Unique UUID — generated automatically |
| `name` | Profile name you entered when saving |
| `target_ip` | IP address that was scanned |
| `timestamp` | ISO 8601 date/time of the scan |
| `device_type` | Inferred type from scan results |
| `system_info` | sysDescr, sysName, sysLocation, sysContact |
| `interfaces` | Interface list discovered via `IF-MIB` |
| `cdp_neighbors` | Neighbours from `CISCO-CDP-MIB` |
| `lldp_neighbors` | Neighbours from `LLDP-MIB` |
| `raw_oids` | All individual OID values collected during scan |

---

## Backup and restore

To back up all data:

```bash
tar czf snmpsim-config-backup-$(date +%Y%m%d).tar.gz /opt/snmpsim/config/
```

To restore:

```bash
tar xzf snmpsim-config-backup-20260515.tar.gz -C /
systemctl restart snmpsim
```

---

## Editing files manually

The JSON files can be edited with any text editor while the service is stopped:

```bash
systemctl stop snmpsim
nano /opt/snmpsim/config/devices.json
systemctl start snmpsim
```

> **Warning:** Do not edit the files while the service is running — changes will be overwritten when the next save occurs.

---

## File permissions

The `config/` directory and its files are owned by `root` and not world-readable:

```
drwx------ root root  config/
-rw------- root root  devices.json
-rw------- root root  discovery_profiles.json
```

The installer sets these permissions automatically. If you need to reset them:

```bash
chown -R root:root /opt/snmpsim/config
chmod 700 /opt/snmpsim/config
chmod 600 /opt/snmpsim/config/*.json
```
