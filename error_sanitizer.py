"""Helpers for returning upstream errors without leaking credentials."""

from __future__ import annotations

import re
from typing import Any


_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?\s*bearer\s+)[^\"'\s,})]+"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)([\"']?(?:access_token|refresh_token|id_token|x-proxy-api-key|api[_-]?key|"
            r"client_secret|password|secret|token|session|cookie)[\"']?\s*[:=]\s*[\"']?)[^\"'\s,})]+"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\b"), "[REDACTED_JWT]"),
    (re.compile(r"\b(?:sk|xai)-[A-Za-z0-9._-]{6,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
]


def sanitize_text(value: Any, *, max_length: int = 500) -> str:
    """Redact common credentials and trim to a log-safe one-line string."""
    text = str(value or "")
    text = text.replace("\r", " ").replace("\n", " ")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_length:
        return text[: max_length - 3].rstrip() + "..."
    return text


def upstream_error_message(service: str, status_code: int) -> str:
    """Return a stable user-facing upstream error without provider body details."""
    return f"{service} request failed with upstream status {status_code}"
