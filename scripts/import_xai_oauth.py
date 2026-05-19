#!/usr/bin/env python3
"""
Import xAI Grok OAuth credentials exported by export_xai_oauth.py
into the local Hermes auth.json on a headless server.

Usage:
    python scripts/import_xai_oauth.py /tmp/xai-oauth.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


XAI_TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"


def hermes_auth_path() -> Path:
    """Return the Hermes auth path, allowing tests/headless scripts to override it."""
    return Path(os.getenv("HERMES_AUTH_PATH", str(Path.home() / ".hermes" / "auth.json"))).expanduser()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def proxy_auth_state_path() -> Path:
    """Return the proxy's local OAuth state path.

    This mirrors token_manager.LOCAL_AUTH_PATH without importing the app module,
    so the helper can run before dependencies are installed or outside the venv.
    """
    state_home = Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))).expanduser()
    return Path(
        os.getenv("GROK_PROXY_AUTH_STATE", str(state_home / "grok-oauth-proxy" / "auth_state.json"))
    ).expanduser()


def validate_token_endpoint(endpoint: str) -> str:
    cleaned = (endpoint or XAI_TOKEN_ENDPOINT).strip()
    parsed = urlparse(cleaned)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "auth.x.ai"
        or parsed.path != "/oauth2/token"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        print("ERROR: Refusing untrusted xAI token_endpoint in export file.", file=sys.stderr)
        sys.exit(1)
    return cleaned


def atomic_write_json(path: Path, data: dict) -> None:
    tmp_path = None
    fd = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        tmp_path = Path(tmp_name)
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import xAI Grok OAuth credentials into Hermes auth.json."
    )
    parser.add_argument("export_file", help="Path to xai-oauth JSON exported by export_xai_oauth.py")
    parser.add_argument(
        "--no-reset-proxy-state",
        action="store_true",
        help=(
            "Do not remove Grok MCP Gateway's local auth_state.json after import. "
            "By default the state is removed so the next proxy restart rehydrates "
            "from the newly imported Hermes credentials."
        ),
    )
    return parser.parse_args()


def reset_proxy_state() -> None:
    state_path = proxy_auth_state_path()
    try:
        if state_path.exists():
            state_path.unlink()
            print(f"Removed stale proxy token state: {state_path}")
    except OSError as exc:
        print(f"WARNING: Could not remove proxy token state {state_path}: {exc}", file=sys.stderr)


def main() -> None:
    args = parse_args()

    export_file = Path(args.export_file).expanduser()
    if not export_file.exists():
        print(f"ERROR: File not found: {export_file}", file=sys.stderr)
        sys.exit(1)

    with export_file.open(encoding="utf-8") as f:
        export_data = json.load(f)

    if "xai-oauth" not in export_data:
        print("ERROR: Invalid export file. 'xai-oauth' key not found.", file=sys.stderr)
        sys.exit(1)
    xai_oauth = export_data["xai-oauth"]
    if not isinstance(xai_oauth, dict):
        print("ERROR: Invalid export file. 'xai-oauth' must be an object.", file=sys.stderr)
        sys.exit(1)
    discovery = xai_oauth.setdefault("discovery", {})
    if not isinstance(discovery, dict):
        print("ERROR: Invalid export file. 'xai-oauth.discovery' must be an object.", file=sys.stderr)
        sys.exit(1)
    discovery["token_endpoint"] = validate_token_endpoint(str(discovery.get("token_endpoint") or XAI_TOKEN_ENDPOINT))

    auth_path = hermes_auth_path()
    hermes_dir = auth_path.parent

    # Ensure Hermes directory exists with strict permissions.
    hermes_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(hermes_dir, 0o700)

    # Load or create Hermes auth.json.
    if auth_path.exists():
        with auth_path.open(encoding="utf-8") as f:
            hermes_data = json.load(f)
    else:
        hermes_data = {
            "version": 1,
            "providers": {},
            "active_provider": None,
            "updated_at": utc_now(),
        }

    # Merge xai-oauth without touching other providers.
    hermes_data.setdefault("providers", {})
    hermes_data["providers"]["xai-oauth"] = xai_oauth
    hermes_data["updated_at"] = utc_now()

    # Backup existing file.
    if auth_path.exists():
        backup = auth_path.with_suffix(".json.bak")
        shutil.copy2(auth_path, backup)
        os.chmod(backup, 0o600)
        print(f"Backed up existing auth to {backup}")

    atomic_write_json(auth_path, hermes_data)

    if not args.no_reset_proxy_state:
        reset_proxy_state()

    print("Successfully imported xai-oauth into Hermes.")
    print(f"Hermes auth: {auth_path}")
    if args.no_reset_proxy_state:
        print("Proxy token state was left unchanged.")
    else:
        print("Proxy token state will be rehydrated from Hermes on next proxy restart.")
    print("You can now restart grok-mcp-gateway.")


if __name__ == "__main__":
    main()
