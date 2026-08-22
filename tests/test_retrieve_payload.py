import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retrieve_payload import merge_stage_payload


def test_merge_stage_payload_deduplicates_posts_by_status_id():
    target = "2071385784154759468"
    payload = {
        "items": [
            {
                "id": target,
                "url": f"https://x.com/xai/status/{target}",
                "author": "xai",
                "text": "fast",
                "relation": "primary",
            }
        ],
        "posts": [
            {"text": "fast", "author": "xai", "url": f"https://x.com/xai/status/{target}"},
        ],
        "groups": {"primary": [], "supporting": [], "reactions": [], "rejected_candidates": []},
        "sources": [{"url": f"https://x.com/xai/status/{target}"}],
        "warnings": [],
        "retrieval_stages": [{"name": "fast_extract", "model": "fast", "status": "success"}],
        "models_used": ["fast"],
    }
    stage_payload = {
        "items": [
            {
                "id": target,
                "url": f"https://x.com/xai/status/{target}",
                "author": "xai",
                "text": "smart",
                "relation": "primary",
            },
            {
                "id": "9999999999999999999",
                "url": "https://x.com/xai/status/9999999999999999999",
                "author": "xai",
                "text": "extra",
                "relation": "primary",
            },
        ],
        "posts": [
            {"text": "smart", "author": "xai", "url": f"https://x.com/xai/status/{target}"},
            {
                "text": "extra",
                "author": "xai",
                "url": "https://x.com/xai/status/9999999999999999999",
            },
        ],
        "sources": [{"url": "https://x.com/xai/status/9999999999999999999"}],
        "warnings": ["escalated"],
        "retrieval_stages": [{"name": "smart_escalation", "model": "smart", "status": "success"}],
        "models_used": ["smart"],
    }

    merge_stage_payload(payload, stage_payload)

    assert [post["text"] for post in payload["posts"]] == ["fast", "extra"]
    assert [item["text"] for item in payload["items"]] == ["fast", "extra"]
    assert payload["models_used"] == ["fast", "smart"]
    assert len(payload["retrieval_stages"]) == 2
