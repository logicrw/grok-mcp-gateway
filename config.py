"""Lightweight configuration from environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _env_int(name: str, default: int, *, minimum: Optional[int] = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise RuntimeError(f"{name} must be an integer") from exc
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_float(name: str, default: float, *, minimum: Optional[float] = None) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        value = default
    else:
        try:
            value = float(raw)
        except ValueError as exc:
            raise RuntimeError(f"{name} must be a number") from exc
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: Optional[list[str]] = None) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


HOST: str = os.getenv("PROXY_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT: int = _env_int("PROXY_PORT", 9996, minimum=1)
GROK_GATEWAY_PORT_AUTOSCAN: bool = _env_bool("GROK_GATEWAY_PORT_AUTOSCAN", False)
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
HERMES_AUTH_PATH: Path = Path(
    os.getenv("HERMES_AUTH_PATH", str(Path.home() / ".hermes" / "auth.json"))
).expanduser()

# Optional local proxy authentication. Required automatically when binding to a
# non-loopback address, because incoming clients otherwise get raw xAI OAuth use.
PROXY_API_KEY: Optional[str] = (os.getenv("PROXY_API_KEY") or "").strip() or None

# Seconds before token expiry to trigger a background prewarm refresh
TOKEN_REFRESH_WINDOW: int = _env_int("TOKEN_REFRESH_WINDOW", 300, minimum=30)
# How often to poll Hermes auth.json for changes (seconds)
HERMES_POLL_INTERVAL: int = _env_int("HERMES_POLL_INTERVAL", 60, minimum=5)

# Total upstream attempts, including the first try. Clamp to one so retry config
# mistakes do not make every request fail without contacting upstream.
UPSTREAM_RETRY_ATTEMPTS: int = _env_int("UPSTREAM_RETRY_ATTEMPTS", 2, minimum=1)
UPSTREAM_RETRY_DELAY: float = _env_float("UPSTREAM_RETRY_DELAY", 1.0, minimum=0.0)

# Optional compatibility shim for clients such as Alma that can call the
# Responses API but cannot attach xAI server-side tools in their custom
# provider UI. Disabled by default to avoid surprise tool latency/cost.
GROK_PROXY_AUTO_X_SEARCH: bool = _env_bool("GROK_PROXY_AUTO_X_SEARCH", False)
GROK_PROXY_X_SEARCH_ALLOWED_HANDLES: list[str] = _env_csv("GROK_PROXY_X_SEARCH_ALLOWED_HANDLES")[:10]
GROK_PROXY_X_SEARCH_IMAGE_UNDERSTANDING: bool = _env_bool("GROK_PROXY_X_SEARCH_IMAGE_UNDERSTANDING", False)
GROK_PROXY_X_SEARCH_VIDEO_UNDERSTANDING: bool = _env_bool("GROK_PROXY_X_SEARCH_VIDEO_UNDERSTANDING", False)

# Resident MCP clients can share one proxy process. Keep x_search calls bounded
# so several local agents cannot stampede the upstream account at once.
GROK_GATEWAY_MCP_TOOL_ALLOWLIST: list[str] = [
    item.lower() for item in _env_csv("GROK_GATEWAY_MCP_TOOL_ALLOWLIST", ["x_retrieve"])
]
GROK_PROXY_MCP_X_SEARCH_CONCURRENCY: int = _env_int("GROK_PROXY_MCP_X_SEARCH_CONCURRENCY", 3, minimum=1)
GROK_GATEWAY_DEBUG_UPSTREAM_ERRORS: bool = _env_bool("GROK_GATEWAY_DEBUG_UPSTREAM_ERRORS", False)
