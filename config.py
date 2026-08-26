"""Lightweight configuration from environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


# A LaunchAgent can inherit credentials intended for unrelated developer tools.
# The gateway only needs this explicit configuration surface; drop everything
# else before any HTTP/OAuth modules are imported.  Keeping this in config.py
# covers both the HTTP service and the stdio MCP entrypoint.
_RUNTIME_ENV_ALLOWLIST = frozenset(
    {
        "HOME",
        "PATH",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PYTHONUNBUFFERED",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "LOG_LEVEL",
        "PROXY_HOST",
        "PROXY_PORT",
        "PROXY_API_KEY",
        "HERMES_AUTH_PATH",
        "TOKEN_REFRESH_WINDOW",
        "UPSTREAM_RETRY_ATTEMPTS",
        "UPSTREAM_RETRY_DELAY",
        "GROK_GATEWAY_PORT_AUTOSCAN",
        "GROK_GATEWAY_MCP_TOOL_ALLOWLIST",
        "GROK_GATEWAY_DEBUG_UPSTREAM_ERRORS",
        "GROK_PROXY_ALLOWED_ORIGINS",
        "GROK_PROXY_AUTH_STATE",
        "GROK_PROXY_AUTO_X_SEARCH",
        "GROK_PROXY_X_SEARCH_ALLOWED_HANDLES",
        "GROK_PROXY_X_SEARCH_IMAGE_UNDERSTANDING",
        "GROK_PROXY_X_SEARCH_VIDEO_UNDERSTANDING",
        "GROK_PROXY_X_SEARCH_INTERNAL_TOOL_NAMES",
        "GROK_PROXY_RETRIEVE_MODEL",
        "GROK_PROXY_MCP_MODEL",
        "GROK_PROXY_FAST_MODEL",
        "GROK_PROXY_RETRIEVE_RAW_MODEL",
        "GROK_PROXY_MCP_RAW_MODEL",
        "GROK_PROXY_ENABLE_AUTO_TIERING",
        "GROK_PROXY_RETRIEVE_CONCURRENCY",
        "GROK_PROXY_MCP_X_SEARCH_CONCURRENCY",
        "GROK_PROXY_RETRIEVE_QUEUE_TIMEOUT_SECONDS",
        "GROK_PROXY_RETRIEVE_TOTAL_TIMEOUT_SECONDS",
        "GROK_PROXY_RETRIEVE_STAGE_TIMEOUT_SECONDS",
        "GROK_PROXY_RETRIEVE_MAX_TARGETS",
        "GROK_PROXY_RETRIEVE_OEMBED_CONCURRENCY",
        "GROK_PROXY_FAST_STAGE_TIMEOUT_SECONDS",
        "GROK_PROXY_SMART_STAGE_TIMEOUT_SECONDS",
        "GROK_PROXY_SMART_REASONING_EFFORT",
        "GROK_PROXY_RAW_STAGE_TIMEOUT_SECONDS",
        "GROK_PROXY_SMART_ESCALATION_MIN_REMAINING_SECONDS",
        "GROK_PROXY_FALLBACK_RESERVE_SECONDS",
        "GROK_PROXY_FAST_MAX_TURNS",
        "GROK_PROXY_SMART_MAX_TURNS",
        "GROK_PROXY_STORE_RESPONSES",
        "GROK_PROXY_RETRIEVE_CACHE",
        "GROK_PROXY_RETRIEVE_CACHE_PATH",
        "GROK_PROXY_RETRIEVE_CACHE_MAX_ENTRIES",
        "GROK_PROXY_RETRIEVE_CACHE_EXACT_TTL_SECONDS",
        "GROK_PROXY_RETRIEVE_CACHE_LATEST_TTL_SECONDS",
    }
)


def _sanitize_runtime_environment() -> int:
    retained = {name: value for name, value in os.environ.items() if name in _RUNTIME_ENV_ALLOWLIST}
    discarded_count = len(os.environ) - len(retained)
    os.environ.clear()
    os.environ.update(retained)
    return discarded_count


RUNTIME_ENV_DISCARDED_VARIABLE_COUNT = _sanitize_runtime_environment()


def _env_int(
    name: str,
    default: int,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
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
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_float(
    name: str,
    default: float,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
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
    if maximum is not None:
        value = min(maximum, value)
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

# Browser origins allowed to call a loopback-bound proxy (exact origin strings,
# e.g. "http://localhost:3000"). Any other Origin header is rejected to block
# DNS-rebinding and cross-site browser requests; non-browser local clients that
# send no Origin are unaffected.
GROK_PROXY_ALLOWED_ORIGINS: list[str] = _env_csv("GROK_PROXY_ALLOWED_ORIGINS")

# Seconds before token expiry to trigger a background prewarm refresh
TOKEN_REFRESH_WINDOW: int = _env_int("TOKEN_REFRESH_WINDOW", 300, minimum=30)

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

# Resident MCP clients can share one proxy process. Keep retrieve calls bounded
# so several local agents cannot stampede the upstream account at once.
GROK_GATEWAY_MCP_TOOL_ALLOWLIST: list[str] = [
    item.lower() for item in _env_csv("GROK_GATEWAY_MCP_TOOL_ALLOWLIST", ["x_retrieve"])
]
# Canonical name is GROK_PROXY_RETRIEVE_CONCURRENCY; the old x_search-era name
# is still honored when the new variable is unset.
GROK_PROXY_RETRIEVE_CONCURRENCY: int = _env_int(
    "GROK_PROXY_RETRIEVE_CONCURRENCY",
    _env_int("GROK_PROXY_MCP_X_SEARCH_CONCURRENCY", 3, minimum=1),
    minimum=1,
)
# Waiting longer than this for the retrieve admission semaphore is treated as
# overload, not as a model-quality failure, and never triggers tier escalation.
GROK_PROXY_RETRIEVE_QUEUE_TIMEOUT_SECONDS: float = _env_float(
    "GROK_PROXY_RETRIEVE_QUEUE_TIMEOUT_SECONDS", 30.0, minimum=1.0, maximum=300.0
)
# xAI surfaces the injected x_search server-side tool under these internal
# names; only these artifacts are stripped from auto-x_search responses.
GROK_PROXY_X_SEARCH_INTERNAL_TOOL_NAMES: list[str] = _env_csv(
    "GROK_PROXY_X_SEARCH_INTERNAL_TOOL_NAMES", ["x_keyword_search"]
)
GROK_GATEWAY_DEBUG_UPSTREAM_ERRORS: bool = _env_bool("GROK_GATEWAY_DEBUG_UPSTREAM_ERRORS", False)

GROK_PROXY_RETRIEVE_TOTAL_TIMEOUT_SECONDS: float = _env_float(
    "GROK_PROXY_RETRIEVE_TOTAL_TIMEOUT_SECONDS", 120.0, minimum=10.0, maximum=300.0
)
GROK_PROXY_RETRIEVE_STAGE_TIMEOUT_SECONDS: float = min(
    GROK_PROXY_RETRIEVE_TOTAL_TIMEOUT_SECONDS,
    _env_float("GROK_PROXY_RETRIEVE_STAGE_TIMEOUT_SECONDS", 80.0, minimum=5.0, maximum=120.0),
)
GROK_PROXY_RETRIEVE_MAX_TARGETS: int = _env_int(
    "GROK_PROXY_RETRIEVE_MAX_TARGETS", 5, minimum=1, maximum=10
)
GROK_PROXY_RETRIEVE_OEMBED_CONCURRENCY: int = _env_int(
    "GROK_PROXY_RETRIEVE_OEMBED_CONCURRENCY", 3, minimum=1, maximum=10
)

# v0.2.1 Adaptive routing and execution lane configuration
GROK_PROXY_RETRIEVE_MODEL: str = (
    os.getenv("GROK_PROXY_RETRIEVE_MODEL")
    or os.getenv("GROK_PROXY_MCP_MODEL")
    or "grok-4.6"
).strip() or "grok-4.6"
GROK_PROXY_RETRIEVE_FAST_MODEL: str = (
    os.getenv("GROK_PROXY_RETRIEVE_FAST_MODEL")
    or os.getenv("GROK_PROXY_MCP_FAST_MODEL")
    or os.getenv("GROK_PROXY_FAST_MODEL")
    or "grok-4.20-0309-non-reasoning"
).strip() or "grok-4.20-0309-non-reasoning"
GROK_PROXY_FAST_MODEL: str = GROK_PROXY_RETRIEVE_FAST_MODEL
GROK_PROXY_RETRIEVE_RAW_MODEL: str = (
    os.getenv("GROK_PROXY_RETRIEVE_RAW_MODEL")
    or os.getenv("GROK_PROXY_MCP_RAW_MODEL")
    or "grok-composer-2.5-fast"
).strip() or "grok-composer-2.5-fast"
GROK_PROXY_ENABLE_AUTO_TIERING: bool = _env_bool("GROK_PROXY_ENABLE_AUTO_TIERING", True)
GROK_PROXY_FAST_STAGE_TIMEOUT_SECONDS: float = _env_float(
    "GROK_PROXY_FAST_STAGE_TIMEOUT_SECONDS", 10.0, minimum=5.0, maximum=60.0
)
GROK_PROXY_SMART_STAGE_TIMEOUT_SECONDS: float = _env_float(
    "GROK_PROXY_SMART_STAGE_TIMEOUT_SECONDS", 80.0, minimum=5.0, maximum=120.0
)
GROK_PROXY_SMART_REASONING_EFFORT: str = (
    os.getenv("GROK_PROXY_SMART_REASONING_EFFORT", "").strip().lower()
)

# Raw expansion is a best-effort recovery step.  It must never turn an
# otherwise bounded MCP call into a second long-running model invocation.
GROK_PROXY_RAW_STAGE_TIMEOUT_SECONDS: float = _env_float(
    "GROK_PROXY_RAW_STAGE_TIMEOUT_SECONDS", 30.0, minimum=2.0, maximum=60.0
)

GROK_PROXY_SMART_ESCALATION_MIN_REMAINING_SECONDS: float = _env_float(
    "GROK_PROXY_SMART_ESCALATION_MIN_REMAINING_SECONDS", 35.0, minimum=10.0, maximum=120.0
)
GROK_PROXY_FALLBACK_RESERVE_SECONDS: float = _env_float(
    "GROK_PROXY_FALLBACK_RESERVE_SECONDS", 8.0, minimum=2.0, maximum=30.0
)
GROK_PROXY_FAST_MAX_TURNS: int = _env_int("GROK_PROXY_FAST_MAX_TURNS", 2, minimum=1, maximum=5)
GROK_PROXY_SMART_MAX_TURNS: int = _env_int("GROK_PROXY_SMART_MAX_TURNS", 3, minimum=1, maximum=10)
GROK_PROXY_STORE_RESPONSES: bool = _env_bool("GROK_PROXY_STORE_RESPONSES", False)

# v0.3.0 response cache: identical deterministic queries (exact status targets,
# latest-by-handle) are served from a local SQLite store instead of re-billing
# upstream xAI calls. Semantic research stays uncached by design.
GROK_PROXY_RETRIEVE_CACHE: bool = _env_bool("GROK_PROXY_RETRIEVE_CACHE", True)
GROK_PROXY_RETRIEVE_CACHE_PATH: Optional[str] = (os.getenv("GROK_PROXY_RETRIEVE_CACHE_PATH") or "").strip() or None
GROK_PROXY_RETRIEVE_CACHE_MAX_ENTRIES: int = _env_int(
    "GROK_PROXY_RETRIEVE_CACHE_MAX_ENTRIES", 5000, minimum=10, maximum=100_000
)
# Immutable status posts keep a long TTL; live feeds rotate quickly.
GROK_PROXY_RETRIEVE_CACHE_EXACT_TTL_SECONDS: int = _env_int(
    "GROK_PROXY_RETRIEVE_CACHE_EXACT_TTL_SECONDS", 86_400, minimum=60, maximum=2_592_000
)
GROK_PROXY_RETRIEVE_CACHE_LATEST_TTL_SECONDS: int = _env_int(
    "GROK_PROXY_RETRIEVE_CACHE_LATEST_TTL_SECONDS", 480, minimum=30, maximum=86_400
)
