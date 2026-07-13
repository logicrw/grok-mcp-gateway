from __future__ import annotations

import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

STATUS_URL_TOKEN_RE = re.compile(
    r"(?<![/A-Za-z0-9.-])(?:https?://)?(?:(?:www|mobile)\.)?(?:x|twitter)\.com/"
    r"[^\s<>\"']+",
    re.IGNORECASE,
)
URL_TRAILING_PUNCTUATION = ".,;:!?)]}，。；：！？）》】」』"
STATUS_PATH_RE = re.compile(
    r"^/(?:[A-Za-z0-9_]{1,15}/status|i/web/status)/(\d{15,20})(?:/(?:photo|video)/\d+)?/?$",
    re.IGNORECASE,
)
STATUS_HOSTS = {
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
}
LABELED_STATUS_ID_RE = re.compile(
    r"(?:target\s+)?status\s+id\s*[:#=]\s*(\d{15,20})|(?:^|\n)\s*[-*]?\s*id\s*[:#=]\s*(\d{15,20})",
    re.IGNORECASE,
)
BARE_STATUS_ID_RE = re.compile(r"(?<!\d)(\d{15,20})(?!\d)")
CONTENT_LINE_RE = re.compile(r"(?:content|text|tweet)\s*[:#=]\s*(.+)$", re.IGNORECASE)


def extract_status_targets(
    query: Optional[str],
    *,
    limit: int,
    allow_bare_ids: bool = True,
) -> tuple[list[str], list[str]]:
    if not query:
        return [], []
    candidates: list[tuple[int, str]] = []
    for position, _url, status_id in _status_urls(query):
        candidates.append((position, status_id))
    for match in LABELED_STATUS_ID_RE.finditer(query):
        status_id = match.group(1) or match.group(2)
        if status_id:
            candidates.append((match.start(), status_id))
    if allow_bare_ids:
        for match in BARE_STATUS_ID_RE.finditer(query):
            candidates.append((match.start(), match.group(1)))

    ordered: list[str] = []
    seen: set[str] = set()
    for _, status_id in sorted(candidates):
        if status_id not in seen:
            seen.add(status_id)
            ordered.append(status_id)
    warnings = [f"target status extraction capped at {limit} IDs"] if len(ordered) > limit else []
    return ordered[:limit], warnings


def parse_raw_posts_from_text(text: str, metadata: Dict[str, Any]) -> list[Dict[str, Any]]:
    cleaned = text.strip()
    if not cleaned:
        return []
    count_limit = int(metadata.get("count") or 20)
    status_ids, _ = extract_status_targets(cleaned, limit=count_limit, allow_bare_ids=False)
    if not status_ids:
        return []
    requested = {str(item) for item in metadata.get("target_status_ids") or []}
    if requested:
        status_ids = [status_id for status_id in status_ids if status_id in requested]
    handles = metadata.get("handles") or []
    author = str(handles[0]).lstrip("@") if handles else None
    return [
        {
            "text": _content_for_status(cleaned, status_id),
            "author": author,
            "created_at": None,
            "url": _url_for_status(cleaned, status_id),
            "metrics": {},
            "confidence": "low",
            "warnings": ["parsed from non-JSON raw upstream text; not trusted"],
        }
        for status_id in status_ids
    ]


def status_id_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in STATUS_HOSTS:
        return None
    match = STATUS_PATH_RE.match(parsed.path)
    return match.group(1) if match else None


def _status_urls(text: str) -> list[tuple[int, str, str]]:
    matches: list[tuple[int, str, str]] = []
    for match in STATUS_URL_TOKEN_RE.finditer(text):
        url = match.group(0).rstrip(URL_TRAILING_PUNCTUATION)
        status_id = status_id_from_url(url)
        if status_id:
            matches.append((match.start(), url, status_id))
    return matches


def _section_for_status(text: str, status_id: str) -> str:
    match = re.search(rf"(?<!\d){re.escape(status_id)}(?!\d)", text)
    if not match:
        return ""
    next_match = re.search(r"(?<!\d)\d{15,20}(?!\d)", text[match.end() :])
    end = match.end() + next_match.start() if next_match else min(len(text), match.start() + 2000)
    return text[match.start() : end]


def _url_for_status(text: str, status_id: str) -> str:
    for _position, url, candidate_status_id in _status_urls(text):
        if candidate_status_id == status_id:
            return url if url.startswith("http") else f"https://{url}"
    return f"https://x.com/i/status/{status_id}"


def _content_for_status(text: str, status_id: str) -> str:
    section = _section_for_status(text, status_id)
    for line in section.splitlines():
        match = CONTENT_LINE_RE.search(line.strip())
        if match:
            return match.group(1).strip()
    for line in section.splitlines()[1:]:
        candidate = line.strip(" -*\t")
        if candidate and not candidate.lower().startswith(("id", "status", "url", "author", "created")):
            return candidate
    return ""
