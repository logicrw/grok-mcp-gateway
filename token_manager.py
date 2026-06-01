"""Manages xAI OAuth tokens copied from Hermes auth.json.

Runs independently from Hermes to avoid token-refresh races.
All blocking I/O is wrapped with asyncio.to_thread so this module
is safe to call from async FastAPI handlers.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

import config
from error_sanitizer import sanitize_text

HERMES_AUTH_PATH = config.HERMES_AUTH_PATH
_STATE_HOME = Path(os.getenv("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))).expanduser()
LOCAL_AUTH_PATH = Path(
    os.getenv("GROK_PROXY_AUTH_STATE", str(_STATE_HOME / "grok-oauth-proxy" / "auth_state.json"))
).expanduser()
LEGACY_LOCAL_AUTH_PATH = Path(__file__).with_name("auth_state.json")

XAI_TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"
XAI_API_BASE = "https://api.x.ai"

REFRESH_SKEW_SECONDS = 120

# Prevent concurrent token refreshes from racing each other.
_refresh_lock = asyncio.Lock()


def _count_from_state(state: Dict[str, Any], key: str) -> int:
    value = state.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def get_refresh_diagnostics(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return sanitized OAuth refresh diagnostics safe for health/metrics."""
    return {
        "last_refresh_at": state.get("last_refresh_at") or state.get("last_refresh"),
        "last_refresh_status": state.get("last_refresh_status"),
        "last_refresh_error_class": state.get("last_refresh_error_class"),
        "refresh_token_rotated": bool(state.get("refresh_token_rotated")),
        "refresh_success_count": _count_from_state(state, "refresh_success_count"),
        "refresh_failure_count": _count_from_state(state, "refresh_failure_count"),
        "credential_source": state.get("credential_source") or "hermes_xai_oauth",
        "reauth_required": bool(state.get("reauth_required")),
    }


def _should_accept_hermes_state(hermes_state: Dict[str, Any], previous_state: Dict[str, Any]) -> bool:
    hermes_access = str(hermes_state.get("access_token") or "")
    previous_access = str(previous_state.get("access_token") or "")
    if not hermes_access:
        return False
    if not previous_access:
        return True

    hermes_exp = get_token_expiry(hermes_access) or 0
    previous_exp = get_token_expiry(previous_access) or 0
    now = time.time()
    if previous_exp <= now and hermes_exp > now:
        return True
    if hermes_exp and previous_exp and hermes_exp <= previous_exp:
        return False

    same_access = hermes_state.get("access_token") == previous_state.get("access_token")
    same_refresh = hermes_state.get("refresh_token") == previous_state.get("refresh_token")
    return not (same_access and same_refresh)


def _decode_jwt_payload(token: str) -> Dict[str, Any]:
    """Return an unverified JWT payload, or an empty dict for non-JWT values."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        pad_len = (4 - (len(payload_b64) % 4)) % 4
        payload_b64 += "=" * pad_len
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _decode_jwt_exp(token: str) -> Optional[float]:
    """Return the 'exp' claim from an unverified JWT, or None."""
    payload = _decode_jwt_payload(token)
    exp = payload.get("exp")
    if isinstance(exp, (int, float)):
        return float(exp)
    return None


def _extract_oauth_client_id(tokens: Dict[str, Any], explicit_client_id: str = "") -> str:
    """Extract the OAuth public client_id from Hermes token claims.

    Hermes stores the xAI public client identifier in JWT claims (`client_id`
    and/or `aud`) rather than as a standalone auth.json field. Importing it from
    the user's authenticated Hermes state avoids shipping a distribution-specific
    client_id constant in this proxy.
    """
    candidates: list[Any] = [explicit_client_id]
    for token_name in ("access_token", "id_token"):
        claims = _decode_jwt_payload(str(tokens.get(token_name) or ""))
        candidates.append(claims.get("client_id"))
        candidates.append(claims.get("aud"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _coerce_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return 0.0


def _select_hermes_pool_entry(pool_entries: list[Dict[str, Any]]) -> Dict[str, Any]:
    usable = [
        entry
        for entry in pool_entries
        if str(entry.get("last_status") or "").lower() not in {"disabled", "error", "exhausted", "failed"}
    ]
    candidates = usable or pool_entries

    def score(entry: Dict[str, Any]) -> tuple[float, float, float]:
        expiry = _decode_jwt_exp(str(entry.get("access_token") or "")) or 0.0
        refreshed_at = _coerce_timestamp(entry.get("last_refresh") or entry.get("last_status_at"))
        priority = entry.get("priority")
        priority_score = float(priority) if isinstance(priority, (int, float)) else 0.0
        return (expiry, refreshed_at, priority_score)

    return max(candidates, key=score)


def _is_expiring(access_token: str, skew_seconds: int = REFRESH_SKEW_SECONDS) -> bool:
    exp = _decode_jwt_exp(access_token)
    if exp is None:
        return False
    return exp <= (time.time() + max(0, skew_seconds))


def _load_json_sync(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    _restrict_existing_file(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read token state file: %s", exc.__class__.__name__)
        return None


def _ensure_private_state_dir(path: Path) -> None:
    """Create and lock down the directory that stores live OAuth state."""
    if path.parent.exists():
        parent_stat = path.parent.lstat()
        if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
            raise RuntimeError("Refusing to use unsafe token state directory.")
        parent_mode = stat.S_IMODE(parent_stat.st_mode)
        current_uid = os.getuid() if hasattr(os, "getuid") else parent_stat.st_uid
        if path.parent.name == "grok-oauth-proxy" and parent_stat.st_uid == current_uid:
            os.chmod(path.parent, 0o700)
        elif parent_mode & 0o022:
            raise RuntimeError("Token state directory is group/world-writable; refusing to store OAuth tokens there.")
        return
    path.parent.mkdir(parents=True, mode=0o700)
    os.chmod(path.parent, 0o700)


def _restrict_existing_file(path: Path) -> None:
    """Best-effort chmod for existing token state files before reading."""
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(stat_result.st_mode):
        raise RuntimeError("Refusing to read symlinked token state file.")
    if not stat.S_ISREG(stat_result.st_mode):
        raise RuntimeError("Refusing to read non-regular token state file.")
    mode = stat.S_IMODE(stat_result.st_mode)
    if mode & 0o077:
        try:
            os.chmod(path, 0o600)
        except PermissionError as exc:
            raise RuntimeError("Token state file permissions are too open and could not be repaired.") from exc


def _save_json_sync(path: Path, data: Dict[str, Any]) -> None:
    _ensure_private_state_dir(path)
    tmp: Optional[Path] = None
    fd: Optional[int] = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        tmp = Path(tmp_name)
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = None
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        if fd is not None:
            os.close(fd)
        if tmp is not None and tmp.exists():
            tmp.unlink()
        raise


async def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(_load_json_sync, path)
    except RuntimeError:
        return _load_json_sync(path)


async def _save_json(path: Path, data: Dict[str, Any]) -> None:
    try:
        return await asyncio.to_thread(_save_json_sync, path, data)
    except RuntimeError:
        return _save_json_sync(path, data)


def _validate_token_endpoint(token_endpoint: str) -> str:
    endpoint = (token_endpoint or XAI_TOKEN_ENDPOINT).strip()
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "auth.x.ai"
        or parsed.path != "/oauth2/token"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("Refusing untrusted xAI token_endpoint in OAuth state.")
    return endpoint


async def load_from_hermes(auth_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Read xai-oauth tokens from Hermes auth.json and return a local state dict."""
    path = auth_path or HERMES_AUTH_PATH
    data = await _load_json(path)
    if not data:
        return None
    providers = data.get("providers") or {}
    xai_state = providers.get("xai-oauth")
    if xai_state:
        tokens = xai_state.get("tokens") or {}
        discovery = xai_state.get("discovery") or {}
        client_id = _extract_oauth_client_id(tokens, str(xai_state.get("client_id") or ""))
        return {
            "access_token": str(tokens.get("access_token") or "").strip(),
            "refresh_token": str(tokens.get("refresh_token") or "").strip(),
            "client_id": client_id,
            "token_type": str(tokens.get("token_type") or "Bearer").strip() or "Bearer",
            "expires_in": tokens.get("expires_in"),
            "token_endpoint": _validate_token_endpoint(str(discovery.get("token_endpoint") or XAI_TOKEN_ENDPOINT)),
            "last_refresh": xai_state.get("last_refresh"),
        }

    pool_entries = (data.get("credential_pool") or {}).get("xai-oauth") or []
    if not pool_entries:
        return None
    pool_entry = _select_hermes_pool_entry(pool_entries)
    client_id = _extract_oauth_client_id(pool_entry, str(pool_entry.get("client_id") or ""))
    return {
        "access_token": str(pool_entry.get("access_token") or "").strip(),
        "refresh_token": str(pool_entry.get("refresh_token") or "").strip(),
        "client_id": client_id,
        "token_type": "Bearer",
        "expires_in": pool_entry.get("expires_in"),
        "token_endpoint": XAI_TOKEN_ENDPOINT,
        "last_refresh": pool_entry.get("last_refresh"),
    }


async def init_local_state() -> Dict[str, Any]:
    """Bootstrap local auth_state.json from Hermes (if present)."""
    if not HERMES_AUTH_PATH.exists():
        if not shutil.which("hermes"):
            raise RuntimeError(
                "Hermes auth.json not found and Hermes Agent CLI is unavailable. "
                "Install Hermes and complete xAI Grok OAuth, or import xAI OAuth credentials with "
                "scripts/import_xai_oauth.py on this host."
            )
        raise RuntimeError(
            "Hermes auth.json not found. Install and configure Hermes before starting this proxy."
        )
    state = await load_from_hermes()
    if not state or not state.get("access_token"):
        raise RuntimeError(
            "No xai-oauth credentials found in Hermes auth.json. "
            "Run 'hermes model' and select xAI Grok OAuth first."
        )
    if not state.get("client_id"):
        raise RuntimeError("Hermes xai-oauth credentials do not include an OAuth client_id claim.")
    await _save_json(LOCAL_AUTH_PATH, state)
    logger.info("Initialized local token state from Hermes.")
    return state


async def read_local_state() -> Dict[str, Any]:
    """Read local auth_state.json, bootstrapping from Hermes if missing."""
    data = await _load_json(LOCAL_AUTH_PATH)
    if not data and LEGACY_LOCAL_AUTH_PATH.exists():
        data = await _load_json(LEGACY_LOCAL_AUTH_PATH)
        if data and data.get("access_token"):
            await _save_json(LOCAL_AUTH_PATH, data)
            logger.info("Migrated token state to %s", LOCAL_AUTH_PATH)
            try:
                LEGACY_LOCAL_AUTH_PATH.unlink()
                logger.info("Removed legacy source-tree token state after migration.")
            except OSError:
                logger.warning("Migrated token state but could not remove legacy source-tree token file.")
    if not data or not data.get("access_token"):
        return await init_local_state()
    if not data.get("client_id"):
        hermes_state = await load_from_hermes()
        if hermes_state and hermes_state.get("client_id"):
            data["client_id"] = hermes_state["client_id"]
            await _save_json(LOCAL_AUTH_PATH, data)
        else:
            raise RuntimeError("Local token state is missing OAuth client_id. Re-authenticate xAI Grok OAuth in Hermes.")
    return data


def _refresh_sync(refresh_token: str, token_endpoint: str, client_id: str) -> Dict[str, Any]:
    """Synchronous token refresh (runs in thread pool)."""
    try:
        resp = httpx.post(
            token_endpoint,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            },
            timeout=20.0,
        )
    except Exception as exc:
        raise RuntimeError(f"Token refresh request failed: {sanitize_text(exc)}") from exc

    if resp.status_code != 200:
        logger.debug("Token refresh failed upstream body: %s", sanitize_text(resp.text))
        raise RuntimeError(f"Token refresh failed ({resp.status_code})")

    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"Token refresh returned invalid JSON: {exc}") from exc

    new_access = str(payload.get("access_token") or "").strip()
    if not new_access:
        raise RuntimeError("Token refresh response missing access_token.")

    return {
        "access_token": new_access,
        "refresh_token": str(payload.get("refresh_token") or refresh_token).strip(),
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
        "expires_in": payload.get("expires_in"),
    }


async def refresh_access_token(state: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh xAI OAuth tokens using the local refresh_token."""
    refresh_token = str(state.get("refresh_token") or "").strip()
    client_id = str(state.get("client_id") or "").strip()
    token_endpoint = _validate_token_endpoint(str(state.get("token_endpoint") or XAI_TOKEN_ENDPOINT))

    if not refresh_token:
        raise RuntimeError("No refresh_token available. Re-authenticate with Hermes.")
    if not client_id:
        raise RuntimeError("No OAuth client_id available. Re-authenticate xAI Grok OAuth in Hermes.")

    logger.info("Refreshing xAI OAuth token...")
    try:
        refreshed = await asyncio.to_thread(_refresh_sync, refresh_token, token_endpoint, client_id)
    except Exception as exc:
        failed = dict(state)
        failed["last_refresh_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        failed["last_refresh_status"] = "failure"
        failed["last_refresh_error_class"] = exc.__class__.__name__
        failed["refresh_failure_count"] = _count_from_state(state, "refresh_failure_count") + 1
        failed["refresh_success_count"] = _count_from_state(state, "refresh_success_count")
        failed["reauth_required"] = True
        await _save_json(LOCAL_AUTH_PATH, failed)
        rehydrated = await rehydrate_from_hermes(failed)
        if rehydrated:
            logger.info("Recovered xAI OAuth token state from Hermes after refresh failure.")
            return rehydrated
        raise

    updated = dict(state)
    updated["access_token"] = refreshed["access_token"]
    updated["refresh_token"] = refreshed["refresh_token"]
    updated["client_id"] = client_id
    updated["token_type"] = refreshed["token_type"]
    updated["expires_in"] = refreshed["expires_in"]
    updated["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    updated["last_refresh_at"] = updated["last_refresh"]
    updated["last_refresh_status"] = "success"
    updated["last_refresh_error_class"] = None
    updated["refresh_token_rotated"] = refreshed["refresh_token"] != refresh_token
    updated["refresh_success_count"] = _count_from_state(state, "refresh_success_count") + 1
    updated["refresh_failure_count"] = _count_from_state(state, "refresh_failure_count")
    updated["credential_source"] = "xai-oauth"
    updated["reauth_required"] = False
    await _save_json(LOCAL_AUTH_PATH, updated)
    logger.info("Token refreshed successfully.")
    return updated


async def rehydrate_from_hermes(previous_state: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Reload a newer Hermes xai-oauth credential into local state, if available."""
    if previous_state is None:
        previous_state = await _load_json(LOCAL_AUTH_PATH) or {}
    hermes_state = await load_from_hermes()
    if not hermes_state or not hermes_state.get("access_token") or not hermes_state.get("client_id"):
        return None

    if not _should_accept_hermes_state(hermes_state, previous_state):
        return None

    if _is_expiring(str(hermes_state.get("access_token") or ""), 0):
        return None

    rehydrated = dict(hermes_state)
    rehydrated["last_refresh_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rehydrated["last_refresh_status"] = "rehydrated_from_hermes"
    rehydrated["last_refresh_error_class"] = None
    rehydrated["refresh_token_rotated"] = hermes_state.get("refresh_token") != previous_state.get("refresh_token")
    rehydrated["refresh_success_count"] = _count_from_state(previous_state, "refresh_success_count")
    rehydrated["refresh_failure_count"] = _count_from_state(previous_state, "refresh_failure_count")
    rehydrated["credential_source"] = "hermes_rehydrated"
    rehydrated["reauth_required"] = False
    await _save_json(LOCAL_AUTH_PATH, rehydrated)
    return rehydrated


async def get_access_token(*, force_refresh: bool = False) -> str:
    """Return a valid access_token, refreshing if needed (async-safe)."""
    state = await read_local_state()
    access_token = str(state.get("access_token") or "").strip()

    if not access_token:
        raise RuntimeError("No access_token available.")

    should_refresh = force_refresh or _is_expiring(access_token, REFRESH_SKEW_SECONDS)
    if should_refresh:
        async with _refresh_lock:
            # Re-read under lock in case another coroutine already refreshed
            state = await read_local_state()
            access_token = str(state.get("access_token") or "").strip()
            should_refresh = force_refresh or _is_expiring(access_token, REFRESH_SKEW_SECONDS)
            if should_refresh:
                state = await refresh_access_token(state)
                access_token = str(state.get("access_token") or "").strip()

    return access_token


async def save_local_state(data: Dict[str, Any]) -> None:
    """Persist token state to the local auth_state.json (async-safe, atomic)."""
    await _save_json(LOCAL_AUTH_PATH, data)


def _read_api_key_file_sync(path: Path) -> str:
    _restrict_existing_file(path)
    return path.read_text(encoding="utf-8").strip()


async def get_api_key_fallback() -> str:
    """Return an xAI API key fallback, if configured."""
    api_key = str(getattr(config, "XAI_API_KEY", "") or "").strip()
    if api_key:
        return api_key

    api_key_file = getattr(config, "XAI_API_KEY_FILE", None)
    if not api_key_file:
        return ""
    try:
        return await asyncio.to_thread(_read_api_key_file_sync, Path(api_key_file))
    except FileNotFoundError:
        return ""


def get_token_expiry(access_token: str) -> Optional[float]:
    """Return the Unix timestamp when the access token expires, or None."""
    return _decode_jwt_exp(access_token)


async def get_auth_headers() -> Dict[str, str]:
    """Return headers ready for an xAI API call."""
    context = await get_auth_context()
    return context["headers"]


async def get_auth_context(*, force_refresh: bool = False) -> Dict[str, Any]:
    """Return xAI auth headers plus the credential source used."""
    try:
        if force_refresh:
            token = await get_access_token(force_refresh=True)
        else:
            token = await get_access_token()
        credential_source = "xai-oauth"
    except Exception as exc:
        api_key = await get_api_key_fallback()
        if not api_key:
            raise
        logger.warning("OAuth token unavailable; using XAI_API_KEY fallback: %s", exc.__class__.__name__)
        token = api_key
        credential_source = "xai-api-key-fallback"
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "credential_source": credential_source,
    }
