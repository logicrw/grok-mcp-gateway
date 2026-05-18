#!/usr/bin/env bash
#
# install.sh - Grok OAuth Proxy installer
#
# Usage:
#   ./install.sh                          # Desktop mode
#   ./install.sh --headless               # Headless server mode (requires exported file)
#   ./install.sh --headless --enable-service
#
# In headless mode, expects an exported file at /tmp/xai-oauth.json
# (or path set via XAI_OAUTH_EXPORT_FILE)
#

set -euo pipefail

# -----------------------------
# Configuration
# -----------------------------
DEFAULT_EXPORT_FILE="/tmp/xai-oauth.json"
EXPORT_FILE="${XAI_OAUTH_EXPORT_FILE:-$DEFAULT_EXPORT_FILE}"
VENV_DIR=".venv"
PYTHON="${PYTHON:-python3}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="grok-oauth-proxy"
SYSTEMD_SERVICE_FILE="$REPO_ROOT/services/grok-oauth-proxy.service"

# -----------------------------
# Argument Parsing
# -----------------------------
HEADLESS=false
ENABLE_SERVICE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --headless)
            HEADLESS=true
            shift
            ;;
        --enable-service)
            ENABLE_SERVICE=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --headless           Install on a headless/remote server"
            echo "  --enable-service     Enable and start systemd service after install"
            echo ""
            echo "Examples:"
            echo "  ./install.sh"
            echo "  ./install.sh --headless"
            echo "  ./install.sh --headless --enable-service"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information."
            exit 1
            ;;
    esac
done

# -----------------------------
# Helper Functions
# -----------------------------
print_header() {
    echo "=========================================="
    echo "  Grok OAuth Proxy Installer"
    echo "=========================================="
}

print_step() {
    echo ""
    echo "▶ $1"
}

# -----------------------------
# Pre-flight Checks
# -----------------------------
print_header

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "ERROR: Python 3 is required but not found."
    exit 1
fi

# -----------------------------
# Headless Mode - Auth File Check
# -----------------------------
if $HEADLESS; then
    echo ""
    echo ">>> Running in HEADLESS mode"
    echo ""

    if [[ ! -f "$EXPORT_FILE" ]]; then
        echo "──────────────────────────────────────────"
        echo "  xAI OAuth credentials not found"
        echo "──────────────────────────────────────────"
        echo ""
        echo "This installation is running on a headless server."
        echo ""
        echo "Please run the following on your desktop machine first:"
        echo ""
        echo "  git clone https://github.com/yelixir-dev/grok-oauth-proxy.git"
        echo "  cd grok-oauth-proxy"
        echo "  python scripts/export_xai_oauth.py > ~/xai-oauth.json"
        echo ""
        echo "Then copy the file to this server:"
        echo ""
        echo "  scp ~/xai-oauth.json <user>@<server>:/tmp/xai-oauth.json"
        echo ""
        echo "After copying the file, run this command again:"
        echo ""
        echo "  ./install.sh --headless"
        echo ""
        echo "──────────────────────────────────────────"
        exit 1
    fi

    echo "Found exported credentials at: $EXPORT_FILE"
fi

# -----------------------------
# Installation Steps
# -----------------------------
print_step "Creating virtual environment..."
if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

print_step "Upgrading pip..."
pip install --quiet --upgrade pip

print_step "Installing dependencies..."
if [[ -f "requirements.txt" ]]; then
    pip install --quiet -r requirements.txt
else
    echo "WARNING: requirements.txt not found."
fi

# Import credentials in headless mode
if $HEADLESS && [[ -f "$EXPORT_FILE" ]]; then
    print_step "Importing xAI OAuth credentials..."
    "$VENV_DIR/bin/python" scripts/import_xai_oauth.py "$EXPORT_FILE"
fi

# -----------------------------
# Optional: Enable systemd service
# -----------------------------
if $ENABLE_SERVICE; then
    print_step "Setting up systemd service..."

    if ! command -v systemctl >/dev/null 2>&1; then
        echo "ERROR: --enable-service requires systemd/systemctl."
        echo "On macOS, use the LaunchAgent example in services/README.md instead."
        exit 1
    fi

    if [[ ! -f "$SYSTEMD_SERVICE_FILE" ]]; then
        echo "ERROR: systemd service file not found at $SYSTEMD_SERVICE_FILE"
        echo "Please create it first or skip --enable-service."
        exit 1
    fi

    SERVICE_DEST="/etc/systemd/system/${SERVICE_NAME}.service"
    SERVICE_USER="$(id -un)"
    SERVICE_HOME="$(eval echo ~"$SERVICE_USER")"
    SERVICE_WORKDIR="$REPO_ROOT"
    SERVICE_VENV_BIN="$REPO_ROOT/$VENV_DIR/bin"
    SERVICE_PYTHON_BIN="$SERVICE_VENV_BIN/python"
    SERVICE_HERMES_AUTH_PATH="$SERVICE_HOME/.hermes/auth.json"
    SERVICE_PATH="$SERVICE_VENV_BIN:$SERVICE_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
    TMP_SERVICE="$(mktemp)"
    trap 'rm -f "$TMP_SERVICE"' EXIT

    SERVICE_USER="$SERVICE_USER" \
    SERVICE_HOME="$SERVICE_HOME" \
    SERVICE_WORKDIR="$SERVICE_WORKDIR" \
    SERVICE_VENV_BIN="$SERVICE_VENV_BIN" \
    SERVICE_PYTHON_BIN="$SERVICE_PYTHON_BIN" \
    SERVICE_HERMES_AUTH_PATH="$SERVICE_HERMES_AUTH_PATH" \
    SERVICE_PATH="$SERVICE_PATH" \
    "$VENV_DIR/bin/python" - "$SYSTEMD_SERVICE_FILE" "$TMP_SERVICE" <<'PY'
import os
import sys
from pathlib import Path

source = Path(sys.argv[1])
dest = Path(sys.argv[2])
text = source.read_text(encoding="utf-8")
replacements = {
    "__SERVICE_USER__": os.environ["SERVICE_USER"],
    "__SERVICE_HOME__": os.environ["SERVICE_HOME"],
    "__SERVICE_WORKDIR__": os.environ["SERVICE_WORKDIR"],
    "__SERVICE_HERMES_AUTH_PATH__": os.environ["SERVICE_HERMES_AUTH_PATH"],
    "__SERVICE_PATH__": os.environ["SERVICE_PATH"],
    "__SERVICE_PYTHON_BIN__": os.environ["SERVICE_PYTHON_BIN"],
}
for placeholder, value in replacements.items():
    text = text.replace(placeholder, value)

missing = [placeholder for placeholder in replacements if placeholder in text]
if missing:
    raise SystemExit(f"unrendered placeholders remain in service template: {missing}")
dest.write_text(text, encoding="utf-8")
PY

    sudo cp "$TMP_SERVICE" "$SERVICE_DEST"
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl restart "$SERVICE_NAME"

    echo "Systemd service enabled and restarted."
fi

# -----------------------------
# Final Output
# -----------------------------
echo ""
echo "=========================================="
echo "  Installation completed successfully!"
echo "=========================================="
echo ""

if $HEADLESS; then
    echo "To start manually:"
    echo "  source $VENV_DIR/bin/activate"
    echo "  python main.py"
    echo ""
    if $ENABLE_SERVICE; then
        echo "Service status:"
        echo "  sudo systemctl status $SERVICE_NAME"
    else
        echo "To enable systemd service later:"
        echo "  ./install.sh --enable-service"
    fi
else
    echo "To start:"
    echo "  source $VENV_DIR/bin/activate"
    echo "  python main.py"
fi

echo ""
echo "See README.md or README.zh-CN.md for more details."
echo ""
