# SNMP Simulator

## Quick Start

```bash
cd snmp-simulator

# Install dependencies (Python 3.11+)
pip install -r requirements.txt

# Run the controller GUI
python -m controller.main

# With elevated privileges (needed for port 161 and IP alias management)
sudo python -m controller.main
```

## What it does

- Simulates **Cisco Routers**, **Cisco Switches**, and **Windows Servers** as independent SNMP agents
- Each device binds to its own IP address and UDP port
- Dynamic OID values: CPU % fluctuates, interface counters increment
- SNMPv2c traps sent to CA Spectrum (or any NMS): `linkDown`, `linkUp`, `cpuHigh`
- GUI to define devices, start/stop agents, trigger interface outages, and view sent traps

## CA Spectrum integration

The `sysObjectID` for each device type is set to a real Cisco/Windows value so CA Spectrum can auto-model the device:

| Profile        | sysObjectID               | Model               |
| -------------- | ------------------------- | ------------------- |
| Cisco Router   | 1.3.6.1.4.1.9.1.1         | Cisco 7206          |
| Cisco Switch   | 1.3.6.1.4.1.9.1.516       | Cisco Catalyst 3750 |
| Windows Server | 1.3.6.1.4.1.311.1.1.3.1.2 | Windows Server 2019 |

## Running without root

Set device port to **1161** (or higher) and use **127.0.0.x** IPs (which already exist on the loopback).  
IP alias management is skipped automatically when running without elevated privileges.

## Testing

```bash
# Poll sysDescr from a running agent
snmpget -v2c -c public 127.0.0.1:1161 1.3.6.1.2.1.1.1.0

# Walk the interface table
snmpwalk -v2c -c public 127.0.0.1:1161 1.3.6.1.2.1.2.2.1

# Listen for traps
snmptrapd -f -Lo -c /dev/stdin <<< "authCommunity log,execute,net public" udp:1162
```

## Project structure

```
snmp-simulator/
├── controller/
│   ├── main.py               # Entry point
│   ├── gui/
│   │   ├── main_window.py    # Device table + toolbar
│   │   ├── device_dialog.py  # Add/edit device form
│   │   └── trap_log.py       # Live trap log panel
│   ├── core/
│   │   ├── agent_manager.py  # Start/stop agents
│   │   ├── config.py         # JSON persistence
│   │   └── ip_manager.py     # IP alias management
│   └── models/
│       └── device.py         # Device dataclass
├── agents/
│   ├── base_agent.py         # pysnmp agent core
│   ├── data_engine.py        # Dynamic OID updater
│   └── profiles/
│       ├── cisco_router.py
│       ├── cisco_switch.py
│       └── windows_server.py
├── traps/
│   └── trap_sender.py        # Raw UDP SNMPv2c trap builder
├── config/
│   └── devices.json          # Saved device definitions
└── requirements.txt
```
