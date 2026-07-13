from __future__ import annotations

import time
from typing import Any, Dict, Optional

import config


class RequestBudget:
    def __init__(self, total_seconds: Optional[float] = None) -> None:
        total = config.GROK_PROXY_RETRIEVE_TOTAL_TIMEOUT_SECONDS if total_seconds is None else total_seconds
        self._deadline = time.monotonic() + max(0.0, total)

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def stage_timeout(self, stage_seconds: Optional[float] = None) -> float:
        stage = config.GROK_PROXY_RETRIEVE_STAGE_TIMEOUT_SECONDS if stage_seconds is None else stage_seconds
        return min(max(0.0, stage), self.remaining())


def reasoning_effort_for(metadata: Dict[str, Any]) -> str:
    if metadata.get("target_status_ids"):
        return "low"
    if metadata.get("intent") == "verify_claim":
        return "high"
    if metadata.get("mode") in {"latest_by_handle", "structured_posts"}:
        return "low"
    return "medium"


def model_supports_reasoning_effort(model: str) -> bool:
    return model.strip().lower() == "grok-4.5"
