"""Helpers for returning upstream errors without leaking credentials."""

from __future__ import annotations

import re
from typing import Any


_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,'\")]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)((?:access|refresh|id)_token\s*[=:]\s*)[^\s,'\")]+"),
    re.compile(r"(?i)(x-proxy-api-key\s*[=:]\s*)[^\s,'\")]+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,'\")]+"),
    re.compile(r"(?i)(client_secret\s*[=:]\s*)[^\s,'\")]+"),
    re.compile(r"(?i)(password\s*[=:]\s*)[^\s,'\")]+"),
    re.compile(r"(?i)(secret\s*[=:]\s*)[^\s,'\")]+"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
]


def sanitize_text(value: Any, *, max_length: int = 500) -> str:
    """Redact common credentials and trim to a log-safe one-line string."""
    text = str(value or "")
    text = text.replace("\r", " ").replace("\n", " ")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(r"\1[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED_EMAIL]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_length:
        return text[: max_length - 3].rstrip() + "..."
    return text


def upstream_error_message(service: str, status_code: int) -> str:
    """Return a stable user-facing upstream error without provider body details."""
    return f"{service} request failed with upstream status {status_code}"
