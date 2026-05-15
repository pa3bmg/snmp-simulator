#!/usr/bin/env bash
# Start the SNMP Simulator Web Controller
#
# Usage:
#   ./start.sh                  # default: http://0.0.0.0:5000
#   SNMP_SIM_PORT=8080 ./start.sh
#   sudo ./start.sh             # enables port 161 + IP alias management

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/.venv"
HOST="${SNMP_SIM_HOST:-0.0.0.0}"
PORT="${SNMP_SIM_PORT:-5000}"

# Create venv if it doesn't exist
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV"
fi

# Install / upgrade dependencies
echo "Checking dependencies..."
"$VENV/bin/pip" install -q -r requirements.txt

# Resolve a local IP to display in the startup message
if [[ "$(uname)" == "Darwin" ]]; then
    LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || echo "localhost")
else
    LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
fi

echo ""
echo "  SNMP Simulator Web Controller"
echo "  ─────────────────────────────────────────"
echo "  Local  : http://localhost:${PORT}"
echo "  Network: http://${LOCAL_IP}:${PORT}"
echo "  ─────────────────────────────────────────"
echo "  Press Ctrl+C to stop"
echo ""

"$VENV/bin/python" -m web.app --host "$HOST" --port "$PORT"
