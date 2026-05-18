#!/usr/bin/env bash
#
# uninstall.sh - Grok OAuth Proxy uninstaller
#
# This script removes the virtual environment and optionally
# the local token state. It does NOT remove Hermes auth.
#

set -euo pipefail

VENV_DIR=".venv"
STATE_DIR="$HOME/.local/state/grok-oauth-proxy"

echo "=========================================="
echo "  Grok OAuth Proxy Uninstaller"
echo "=========================================="
echo ""

read -p "Remove virtual environment (.venv)? [Y/n] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
    if [[ -d "$VENV_DIR" ]]; then
        rm -rf "$VENV_DIR"
        echo "✓ Virtual environment removed."
    else
        echo "  No virtual environment found."
    fi
fi

read -p "Remove local token state (~/.local/state/grok-oauth-proxy)? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [[ -d "$STATE_DIR" ]]; then
        rm -rf "$STATE_DIR"
        echo "✓ Local token state removed."
    else
        echo "  No local token state found."
    fi
fi

echo ""
echo "Uninstall completed."
echo "Note: Hermes auth.json was not touched."
echo ""