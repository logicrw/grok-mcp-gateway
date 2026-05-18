#!/usr/bin/env python3
"""
Export only the xAI Grok OAuth credentials from Hermes auth.json.

This is the recommended way to move xAI OAuth tokens to a headless server
without copying your entire Hermes credential store.

Usage:
    python scripts/export_xai_oauth.py > xai-oauth.json
    scp xai-oauth.json user@server:/tmp/

On the server:
    python scripts/import_xai_oauth.py /tmp/xai-oauth.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def hermes_auth_path() -> Path:
    """Return the Hermes auth path, allowing tests/headless scripts to override it."""
    return Path(os.getenv("HERMES_AUTH_PATH", str(Path.home() / ".hermes" / "auth.json"))).expanduser()


OUTPUT_WARNING = (
    "WARNING: exported xAI OAuth credentials contain refresh tokens. "
    "Treat the output like a password, do not commit it, and delete it after import."
)


def find_xai_oauth(data: dict) -> dict | None:
    """Return xAI OAuth credentials from either supported Hermes auth shape."""
    provider = (data.get("providers") or {}).get("xai-oauth")
    if provider:
        return provider

    pool_entries = (data.get("credential_pool") or {}).get("xai-oauth") or []
    if not pool_entries:
        return None
    entry = next(
        (
            candidate
            for candidate in pool_entries
            if str(candidate.get("last_status") or "").lower() in {"", "ok"}
        ),
        pool_entries[0],
    )
    tokens = {
        "access_token": str(entry.get("access_token") or "").strip(),
        "refresh_token": str(entry.get("refresh_token") or "").strip(),
        "token_type": str(entry.get("token_type") or "Bearer").strip() or "Bearer",
    }
    for optional_key in ("id_token", "expires_in"):
        if entry.get(optional_key) is not None:
            tokens[optional_key] = entry[optional_key]
    return {
        "tokens": tokens,
        "discovery": {"token_endpoint": "https://auth.x.ai/oauth2/token"},
        "client_id": str(entry.get("client_id") or "").strip(),
        "last_refresh": entry.get("last_refresh"),
    }


def main() -> None:
    auth_path = hermes_auth_path()
    if not auth_path.exists():
        print(f"ERROR: Hermes auth file not found: {auth_path}", file=sys.stderr)
        sys.exit(1)

    with auth_path.open(encoding="utf-8") as f:
        data = json.load(f)

    xai_oauth = find_xai_oauth(data)

    if not xai_oauth:
        print("ERROR: No 'xai-oauth' credentials found in Hermes auth.json", file=sys.stderr)
        print("Please run 'hermes model' and complete xAI Grok OAuth first.", file=sys.stderr)
        sys.exit(1)

    export_data = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "hermes",
        "xai-oauth": xai_oauth,
    }

    # Keep stdout machine-readable JSON so `> xai-oauth.json` can be imported
    # directly. Put the human warning on stderr instead.
    print(OUTPUT_WARNING, file=sys.stderr)
    print(json.dumps(export_data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
