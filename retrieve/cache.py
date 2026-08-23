"""Response cache for x_retrieve: SQLite persistence plus in-flight coalescing.

Design (phase 10):
- Only deterministic query classes are cached: exact status targets (long TTL,
  posts are immutable) and latest-by-handle feeds (short TTL). Semantic research
  is generation-shaped and never persisted.
- The cached unit is one complete final ``structuredContent`` payload with
  ``retrieval_status == "ok"``; degraded/error results are never stored.
- ``fetch_history`` stores status IDs, authors, and content hashes only — no
  post text — so ``new_since_last_fetch`` adds monitoring value without
  expanding the privacy surface beyond the cache file itself.
- SQLite runs in WAL mode so multiple gateway processes (HTTP daemon + stdio
  MCP sessions) share one store safely; per-call connections keep the loop free.
- Concurrent identical requests are coalesced in-process: one upstream run,
  every waiter shares the result (the cached write also happens inside the
  shared task, so caller cancellation cannot discard it).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional, Tuple

import config
import token_manager

logger = logging.getLogger(__name__)

_cache_counts: defaultdict[str, int] = defaultdict(int)

# In-flight request coalescing: cache key -> shared task producing the payload.
_inflight: Dict[str, asyncio.Task[Dict[str, Any]]] = {}


@dataclass(frozen=True)
class CacheDirective:
    key: str
    policy: str
    ttl_seconds: int


def record_cache_result(result: str) -> None:
    _cache_counts[result] += 1


def metrics_lines() -> list[str]:
    lines = [
        "# HELP mcp_x_retrieve_cache_total Cache decisions by result",
        "# TYPE mcp_x_retrieve_cache_total counter",
    ]
    for result in ("hit", "miss", "bypass", "write", "error"):
        lines.append(f'mcp_x_retrieve_cache_total{{result="{result}"}} {_cache_counts[result]}')
    return lines


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return "" if value is None else json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return re.sub(r"\s+", " ", value.strip().lower())


def cache_directive(metadata: Dict[str, Any]) -> Optional[CacheDirective]:
    """Return the cache policy for a normalized request, or None when uncacheable."""
    if metadata.get("target_strategy") == "exact_only":
        policy = "exact_only"
        ttl = config.GROK_PROXY_RETRIEVE_CACHE_EXACT_TTL_SECONDS
    elif metadata.get("mode") == "latest_by_handle" and not metadata.get("target_status_ids"):
        policy = "latest_by_handle"
        ttl = config.GROK_PROXY_RETRIEVE_CACHE_LATEST_TTL_SECONDS
    else:
        return None

    key_source = json.dumps(
        {
            "v": 1,
            "mode": metadata.get("mode"),
            "intent": metadata.get("intent"),
            "query": _normalize_text(metadata.get("query")),
            "handles": sorted(str(h).lstrip("@").lower() for h in metadata.get("handles") or []),
            "excluded_handles": sorted(str(h).lstrip("@").lower() for h in metadata.get("excluded_handles") or []),
            "target_status_ids": sorted(str(s) for s in metadata.get("target_status_ids") or []),
            "count": metadata.get("count"),
            "sort": metadata.get("sort"),
            "time_range": metadata.get("time_range"),
            "from_date": metadata.get("from_date"),
            "to_date": metadata.get("to_date"),
            "lookback_days": metadata.get("lookback_days"),
            "include_replies": metadata.get("include_replies"),
            "include_reposts": metadata.get("include_reposts"),
            "best_effort_filters": metadata.get("best_effort_filters"),
            "quality": metadata.get("quality"),
            "model_policy": metadata.get("model_policy"),
            "explicit_model": metadata.get("explicit_model"),
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()
    return CacheDirective(key=key, policy=policy, ttl_seconds=ttl)


def cache_path() -> Path:
    override = config.GROK_PROXY_RETRIEVE_CACHE_PATH
    if override:
        return Path(override).expanduser()
    return token_manager.LOCAL_AUTH_PATH.with_name("cache.sqlite")


def _connect(path: Path) -> sqlite3.Connection:
    token_manager._ensure_private_state_dir(path)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=2000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS responses ("
        "cache_key TEXT PRIMARY KEY, policy TEXT NOT NULL, payload TEXT NOT NULL,"
        "created_at REAL NOT NULL, last_access REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fetch_history ("
        "handle TEXT NOT NULL, status_id TEXT NOT NULL, content_hash TEXT NOT NULL,"
        "first_seen REAL NOT NULL, last_seen REAL NOT NULL,"
        "PRIMARY KEY (handle, status_id))"
    )
    # WAL side files follow the process umask on creation; enforce 0600 on all three.
    for suffix in ("", "-wal", "-shm"):
        try:
            os.chmod(Path(str(path) + suffix), 0o600)
        except OSError:
            pass
    return conn


async def get(
    key: str,
    policy_ttl_seconds: int,
    max_age_seconds: Optional[int],
) -> Optional[Tuple[Dict[str, Any], float, str]]:
    """Return (payload, age_seconds, policy) for a fresh entry, or None."""

    def _get_sync() -> Optional[Tuple[Dict[str, Any], float, str]]:
        ttl = float(max_age_seconds) if max_age_seconds is not None else float(policy_ttl_seconds)
        now = time.time()
        with closing(_connect(cache_path())) as conn, conn:
            row = conn.execute(
                "SELECT payload, policy, created_at FROM responses WHERE cache_key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            payload_text, policy, created_at = row
            conn.execute("UPDATE responses SET last_access = ? WHERE cache_key = ?", (now, key))
        age = max(0.0, now - created_at)
        if age > ttl:
            return None
        payload = json.loads(payload_text)
        if not isinstance(payload, dict):
            return None
        return payload, age, policy

    try:
        return await asyncio.to_thread(_get_sync)
    except Exception as exc:
        record_cache_result("error")
        logger.warning("Cache read failed: %s", exc.__class__.__name__)
        return None


async def put(key: str, policy: str, payload: Dict[str, Any]) -> None:
    def _put_sync() -> None:
        now = time.time()
        with closing(_connect(cache_path())) as conn, conn:
            conn.execute(
                "INSERT INTO responses (cache_key, policy, payload, created_at, last_access)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(cache_key) DO UPDATE SET policy=policy, payload=payload,"
                " created_at=created_at, last_access=last_access",
                (key, policy, json.dumps(payload, ensure_ascii=False), now, now),
            )
            overflow = conn.execute(
                "SELECT COUNT(*) FROM responses"
            ).fetchone()[0] - config.GROK_PROXY_RETRIEVE_CACHE_MAX_ENTRIES
            if overflow > 0:
                conn.execute(
                    "DELETE FROM responses WHERE cache_key IN ("
                    " SELECT cache_key FROM responses ORDER BY last_access ASC, created_at ASC LIMIT ?)",
                    (overflow,),
                )
            history_overflow = conn.execute("SELECT COUNT(*) FROM fetch_history").fetchone()[0] - config.GROK_PROXY_RETRIEVE_CACHE_MAX_ENTRIES
            if history_overflow > 0:
                conn.execute(
                    "DELETE FROM fetch_history WHERE rowid IN ("
                    " SELECT rowid FROM fetch_history ORDER BY last_seen ASC LIMIT ?)",
                    (history_overflow,),
                )

    try:
        await asyncio.to_thread(_put_sync)
        record_cache_result("write")
    except Exception as exc:
        record_cache_result("error")
        logger.warning("Cache write failed: %s", exc.__class__.__name__)


def _ttl_for(policy_ttl_seconds: int, max_age_seconds: Optional[int]) -> float:
    if max_age_seconds is None:
        return float(policy_ttl_seconds)
    return float(max_age_seconds)


def mark_new_items_and_record_history(payload: Dict[str, Any]) -> None:
    """Mark ``new_since_last_fetch`` on unseen items and upsert ID-only history.

    Synchronous and best-effort: failures degrade to no marking, never errors.
    """
    items = [item for item in payload.get("items") or [] if isinstance(item, dict) and item.get("id")]
    if not items:
        return
    now = time.time()
    try:
        with closing(_connect(cache_path())) as conn, conn:
            for item in items:
                handle = str(item.get("author") or "").lstrip("@").lower()
                status_id = str(item["id"])
                content_hash = hashlib.sha256(str(item.get("text") or "").encode("utf-8")).hexdigest()[:16]
                if not handle:
                    item["new_since_last_fetch"] = False
                    continue
                seen = conn.execute(
                    "SELECT 1 FROM fetch_history WHERE handle = ? AND status_id = ?", (handle, status_id)
                ).fetchone()
                item["new_since_last_fetch"] = seen is None
                conn.execute(
                    "INSERT INTO fetch_history (handle, status_id, content_hash, first_seen, last_seen)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(handle, status_id) DO UPDATE SET"
                    " content_hash=content_hash, last_seen=last_seen",
                    (handle, status_id, content_hash, now, now),
                )
    except Exception as exc:
        logger.debug("Fetch history recording skipped: %s", exc.__class__.__name__)


async def coalesce(key: str, runner: Callable[[], Coroutine[Any, Any, Dict[str, Any]]]) -> Dict[str, Any]:
    """Share one in-flight run across concurrent identical requests.

    Caller cancellation releases the waiter but never cancels the shared task;
    the winner's result (and its cache write) always completes.
    """
    task = _inflight.get(key)
    if task is not None and not task.done():
        return await asyncio.shield(task)
    task = asyncio.create_task(runner())
    _inflight[key] = task

    def _cleanup(done: asyncio.Task[Dict[str, Any]]) -> None:
        if _inflight.get(key) is done:
            _inflight.pop(key, None)
        if not done.cancelled():
            done.exception()  # mark retrieved so cancellation never leaves it unread

    task.add_done_callback(_cleanup)
    return await asyncio.shield(task)
