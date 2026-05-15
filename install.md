# SNMP Simulator — Installation Manual

## Requirements

| Item    | Minimum                                                |
| ------- | ------------------------------------------------------ |
| OS      | Rocky Linux 8.10 or higher (RHEL 8/9 compatible)       |
| CPU     | 1 vCPU                                                 |
| RAM     | 512 MB                                                 |
| Disk    | 500 MB free                                            |
| Network | Static IP recommended                                  |
| User    | **root** (required for UDP port 161 and `ip addr add`) |

---

## Offline installation (no internet on target server)

Use this when the target server has no internet access.

### Step 1 — pre-download Python wheels (on an internet-connected machine)

Run this once on your Mac or any machine with internet and Python 3.11:

```bash
cd snmp-simulator
bash bundle_offline.sh
```

This creates an `offline_packages/` folder containing all 16 required `.whl` files.

### Step 2 — ensure system packages are available

The installer uses `dnf` to install `python3.11`, `gcc`, `rsync`, and `iproute`. On the offline server one of these must be true:

- **Already-configured local repo** — if your server already has a local Rocky/RHEL repo configured in `/etc/yum.repos.d/`, nothing extra is needed. `dnf` will use it automatically.
- **Rocky ISO auto-mount** — place a `Rocky*.iso` file in the same folder as `install.sh`. The installer will mount it and configure a temporary `dnf` repo from it automatically.

### Step 3 — copy the folder to the offline server

```bash
scp -r snmp-simulator/ root@<offline-server>:/tmp/snmp-simulator
```

The `offline_packages/` folder must be included in the copy.

### Step 4 — run the installer

```bash
bash /tmp/snmp-simulator/install.sh
```

The installer detects `offline_packages/` and uses `pip install --no-index` — no internet required for Python dependencies.

---

## Online installation



From your workstation, copy the project folder to the target server. Replace `<server-ip>` with the actual IP address.

```bash
scp -r snmp-simulator/ root@<server-ip>:/tmp/snmp-simulator
```

Or use a USB stick / shared folder — any method that gets the folder onto the server.

---

## 2. Log in to the server as root

```bash
ssh root@<server-ip>
```

---

## 3. Run the installer

```bash
cd /tmp/snmp-simulator
bash install.sh
```

The installer will:

1. Verify you are running Rocky Linux 8.10 or higher
2. Install required system packages via `dnf`:
   - `python3`, `python3-pip`, `python3-devel`
   - `iproute` (for `ip addr add` / `ip addr del`)
   - `gcc` (needed to compile some Python packages)
3. Copy the application to `/opt/snmpsim/`
4. Create a Python virtual environment in `/opt/snmpsim/.venv`
5. Install Python dependencies from `requirements.txt`
6. Open firewall ports **161/udp** (SNMP) and **5000/tcp** (Web UI) via `firewalld`
7. Install and enable a `systemd` service named **`snmpsim`** that starts automatically on boot
8. Start the service immediately

When finished, the installer prints the URL of the Web UI.

---

## 4. Verify the installation

Check that the service is running:

```bash
systemctl status snmpsim
```

View live log output:

```bash
journalctl -u snmpsim -f
```

Open the Web UI in a browser:

```
http://<server-ip>:5000
```

---

## 5. First-time configuration

1. Open the Web UI
2. Go to the **Server Network** tab
3. Select the network interface that should carry the simulated device IP addresses (e.g. `enp7s0`)
4. Add the IP addresses you want to simulate (single address or bulk range)
5. Go to the **Devices** tab and add your simulated devices

---

## Installer options

You can override defaults with environment variables before running the installer:

| Variable      | Default        | Description                        |
| ------------- | -------------- | ---------------------------------- |
| `INSTALL_DIR` | `/opt/snmpsim` | Where the application is installed |
| `WEB_PORT`    | `5000`         | TCP port for the Web UI            |

Example — install to a different directory and use port 8080:

```bash
INSTALL_DIR=/opt/mysnmp WEB_PORT=8080 bash install.sh
```

---

## Service management

| Action            | Command                     |
| ----------------- | --------------------------- |
| Start             | `systemctl start snmpsim`   |
| Stop              | `systemctl stop snmpsim`    |
| Restart           | `systemctl restart snmpsim` |
| Status            | `systemctl status snmpsim`  |
| View logs         | `journalctl -u snmpsim -f`  |
| Disable autostart | `systemctl disable snmpsim` |

---

## Upgrading

Copy the new version to the server and run the installer again. It replaces the application files and restarts the service. Existing configuration files in `/opt/snmpsim/config/` are preserved.

```bash
scp -r snmp-simulator/ root@<server-ip>:/tmp/snmp-simulator
ssh root@<server-ip> "cd /tmp/snmp-simulator && bash install.sh"
```

---

## Uninstalling

```bash
systemctl disable --now snmpsim
rm -rf /opt/snmpsim /etc/systemd/system/snmpsim.service
systemctl daemon-reload
```

To also remove the firewall rules:

```bash
firewall-cmd --permanent --remove-port=161/udp
firewall-cmd --permanent --remove-port=5000/tcp
firewall-cmd --reload
```

---

## Troubleshooting

**Service fails to start**

```bash
journalctl -u snmpsim -n 50 --no-pager
```

Common causes:

- Port 5000 already in use → set `WEB_PORT` to a free port and re-run the installer
- Python dependency failed to install → check internet connectivity and re-run

**Cannot reach the Web UI**

- Verify the service is running: `systemctl status snmpsim`
- Check `firewalld` allowed the port: `firewall-cmd --list-ports`
- Check SELinux is not blocking: `ausearch -m avc -ts recent`

**`ip addr add` fails with "permission denied"**

The service must run as `root`. Verify the service unit file:

```bash
grep User /etc/systemd/system/snmpsim.service
# Should show: User=root
```

**SNMP not responding on port 161**

- Confirm the IP address is assigned to the interface: `ip addr show enp7s0`
- Confirm the simulator is bound to that IP in the Devices tab (status should be **Running**)
- Test with: `snmpget -v2c -c public <device-ip> 1.3.6.1.2.1.1.1.0`
