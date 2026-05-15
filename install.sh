#!/usr/bin/env bash
# =============================================================================
#  SNMP Simulator — installer for Rocky Linux 8.10+
#  Must be run as root:  sudo bash install.sh
# =============================================================================
set -euo pipefail

# --------------------------------------------------------------------------- #
#  Config (override via env vars if needed)                                   #
# --------------------------------------------------------------------------- #
INSTALL_DIR="${INSTALL_DIR:-/opt/snmpsim}"
SERVICE_USER="${SERVICE_USER:-root}"          # root required for port 161 + ip addr
WEB_PORT="${WEB_PORT:-5000}"
SERVICE_NAME="snmpsim"

# --------------------------------------------------------------------------- #
#  Colour helpers                                                              #
# --------------------------------------------------------------------------- #
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# --------------------------------------------------------------------------- #
#  Root check                                                                  #
# --------------------------------------------------------------------------- #
[[ $EUID -eq 0 ]] || error "This installer must be run as root (sudo bash install.sh)"

# --------------------------------------------------------------------------- #
#  OS check                                                                    #
# --------------------------------------------------------------------------- #
if [[ -f /etc/os-release ]]; then
    source /etc/os-release
    if [[ "$ID" != "rocky" && "$ID" != "rhel" && "$ID" != "centos" ]]; then
        warn "OS is '$ID', not Rocky/RHEL. Continuing anyway — may need adjustment."
    fi
    MAJOR="${VERSION_ID%%.*}"
    if [[ "$MAJOR" -lt 8 ]]; then
        error "Requires Rocky Linux 8.10 or higher (detected $VERSION_ID)"
    fi
    info "OS: $PRETTY_NAME"
else
    warn "/etc/os-release not found — skipping OS check"
fi

# --------------------------------------------------------------------------- #
#  Source directory (where install.sh lives)                                  #
# --------------------------------------------------------------------------- #
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
info "Source directory: $SCRIPT_DIR"

# --------------------------------------------------------------------------- #
#  Install system packages                                                     #
# --------------------------------------------------------------------------- #
info "Installing system packages..."
dnf install -y \
    python3 \
    python3-pip \
    python3-devel \
    iproute \
    net-tools \
    gcc \
    2>/dev/null || error "dnf install failed"

# Python 3.11 is available on Rocky 9+; on Rocky 8 python3 is 3.6 — try to
# get a newer one via dnf module if available
PYTHON_BIN="$(command -v python3.11 2>/dev/null || command -v python3.9 2>/dev/null || command -v python3 2>/dev/null)"
info "Using Python: $PYTHON_BIN ($($PYTHON_BIN --version))"

# --------------------------------------------------------------------------- #
#  Create install directory and copy application files                        #
# --------------------------------------------------------------------------- #
info "Installing application to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"

# Copy all project files, preserving directory structure
rsync -a --delete \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='install.sh' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/"

chmod 750 "$INSTALL_DIR"

# --------------------------------------------------------------------------- #
#  Create Python virtual environment                                           #
# --------------------------------------------------------------------------- #
VENV="$INSTALL_DIR/.venv"
if [[ -d "$VENV" ]]; then
    info "Removing existing venv..."
    rm -rf "$VENV"
fi
info "Creating Python virtual environment..."
"$PYTHON_BIN" -m venv "$VENV"

info "Installing Python dependencies..."
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$INSTALL_DIR/requirements.txt" || \
    error "pip install failed — check requirements.txt and internet connectivity"

# --------------------------------------------------------------------------- #
#  Create config directory and default files if missing                       #
# --------------------------------------------------------------------------- #
mkdir -p "$INSTALL_DIR/config"
if [[ ! -f "$INSTALL_DIR/config/devices.json" ]]; then
    echo '[]' > "$INSTALL_DIR/config/devices.json"
fi
if [[ ! -f "$INSTALL_DIR/config/discovery_profiles.json" ]]; then
    echo '[]' > "$INSTALL_DIR/config/discovery_profiles.json"
fi# Secure the config directory (root-only read/write)
chown -R root:root "$INSTALL_DIR/config"
chmod 700 "$INSTALL_DIR/config"
chmod 600 "$INSTALL_DIR/config"/*.json 2>/dev/null || true
# --------------------------------------------------------------------------- #
#  Open firewall ports (firewalld)                                            #
# --------------------------------------------------------------------------- #
if command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld; then
    info "Opening firewall ports (161/udp SNMP, $WEB_PORT/tcp Web UI)..."
    firewall-cmd --permanent --add-port=161/udp      2>/dev/null || true
    firewall-cmd --permanent --add-port="${WEB_PORT}/tcp" 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
else
    warn "firewalld not active — skipping firewall configuration"
    warn "Make sure ports 161/udp and ${WEB_PORT}/tcp are accessible"
fi

# --------------------------------------------------------------------------- #
#  Write systemd service unit                                                  #
# --------------------------------------------------------------------------- #
info "Writing systemd service unit /etc/systemd/system/${SERVICE_NAME}.service ..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=SNMP Simulator Web Controller
After=network.target
Wants=network.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
Environment="SNMP_SIM_PORT=${WEB_PORT}"
ExecStart=${VENV}/bin/python -m web.app --host 0.0.0.0 --port ${WEB_PORT}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

# --------------------------------------------------------------------------- #
#  Enable and start service                                                    #
# --------------------------------------------------------------------------- #
info "Enabling and starting ${SERVICE_NAME} service..."
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

# Give it a moment to start
sleep 3

if systemctl is-active --quiet "${SERVICE_NAME}"; then
    info "Service is running."
else
    warn "Service did not start cleanly. Check logs with:"
    warn "  journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
fi

# --------------------------------------------------------------------------- #
#  Detect primary IP for the welcome message                                  #
# --------------------------------------------------------------------------- #
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

# --------------------------------------------------------------------------- #
#  Done                                                                        #
# --------------------------------------------------------------------------- #
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        SNMP Simulator — Installation Complete        ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Web UI  : ${GREEN}http://${LOCAL_IP}:${WEB_PORT}${NC}"
echo -e "  Install : ${INSTALL_DIR}"
echo -e "  Service : systemctl {start|stop|restart|status} ${SERVICE_NAME}"
echo -e "  Logs    : journalctl -u ${SERVICE_NAME} -f"
echo ""
echo -e "  To uninstall:"
echo -e "    systemctl disable --now ${SERVICE_NAME}"
echo -e "    rm -rf ${INSTALL_DIR} /etc/systemd/system/${SERVICE_NAME}.service"
echo -e "    systemctl daemon-reload"
echo ""
