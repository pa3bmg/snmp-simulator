#!/usr/bin/env bash
# =============================================================================
#  bundle_offline.sh — run this on an INTERNET-CONNECTED machine to download
#  all Python wheels so the installer can work fully offline.
#
#  Usage (on a Mac or Linux machine with internet):
#    bash bundle_offline.sh
#
#  Result:
#    offline_packages/   ← folder of .whl files — include in your install package
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/offline_packages"

# Python to use for downloading — must be 3.11 to match the target
PYTHON_BIN="$(command -v python3.11 2>/dev/null || command -v python3 2>/dev/null)"
[[ -z "$PYTHON_BIN" ]] && { echo "ERROR: python3 not found"; exit 1; }

echo "Using Python: $PYTHON_BIN ($($PYTHON_BIN --version))"
echo "Downloading wheels to: $OUT_DIR"

mkdir -p "$OUT_DIR"

"$PYTHON_BIN" -m pip download \
    --dest "$OUT_DIR" \
    --platform manylinux2014_x86_64 \
    --python-version 3.11 \
    --only-binary=:all: \
    -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null || \
"$PYTHON_BIN" -m pip download \
    --dest "$OUT_DIR" \
    -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "Done. $(ls "$OUT_DIR" | wc -l) packages downloaded to offline_packages/"
echo ""
echo "Include the offline_packages/ folder when copying to the target server."
echo "install.sh will detect it and use it automatically."
