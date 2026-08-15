#!/usr/bin/env python3
"""Standalone native xAI OAuth login entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Allow execution as `python scripts/login_xai_oauth.py` from the repository.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import oauth_flow


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(oauth_flow.login_xai_oauth())
    except (KeyboardInterrupt, oauth_flow.OAuthLoginError) as exc:
        message = "Login cancelled." if isinstance(exc, KeyboardInterrupt) else str(exc)
        print(f"ERROR: {message}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
