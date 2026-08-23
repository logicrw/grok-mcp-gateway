from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xai_responses
from retrieve.payload import assemble_payload, finalize_payload
from retrieve.text_parser import parse_raw_posts_from_text

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "xai"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _result_from_fixture(name: str) -> xai_responses.ResponsesResult:
    data = _load_fixture(name)
    return xai_responses.ResponsesResult(
        text=xai_responses._extract_output_text(data),
        compact=xai_responses._compact_response(data),
        citations=xai_responses._extract_citations(data),
        usage=data.get("usage"),
        model=str(data.get("model") or ""),
        inline_citations=xai_responses._extract_inline_citations(data),
        degraded=xai_responses._extract_degraded(data),
    )


def test_fast_fixture_assembles_ok_latest_by_handle_payload():
    result = _result_from_fixture("fast_latest.json")
    assert result.model == "grok-4.20-0309-non-reasoning"
    assert xai_responses.parse_usage_metrics(result.usage) == (0, 1)
    assert xai_responses.parse_usage_cost_ticks(result.usage) == 4
    assert result.citations[0]["url"].endswith("/2071385784154759468")

    metadata = {
        "mode": "latest_by_handle",
        "intent": "auto",
        "handles": ["xai"],
        "query": None,
        "count": 5,
        "sort": "latest",
        "quality": {"min_items": 1},
        "target_status_ids": [],
    }
    payload = assemble_payload(result, metadata, stage_name="fast_extract")
    finalize_payload(payload, metadata)

    assert payload["retrieval_status"] == "ok"
    assert payload["posts"][0]["author"] == "xai"
    assert payload["items"][0]["id"] == "2071385784154759468"


def test_smart_fixture_keeps_citation_backed_claim_check():
    result = _result_from_fixture("smart_verify.json")
    assert result.model == "grok-4.6"
    assert xai_responses.parse_usage_metrics(result.usage) == (180, 2)
    assert any(item.get("url", "").endswith("/2071323738201837785") for item in result.citations)

    metadata = {
        "mode": "claim_verification",
        "intent": "verify_claim",
        "handles": [],
        "query": "Did xAI announce Grok 4.6?",
        "count": 10,
        "sort": "relevance",
        "quality": {"min_items": 1, "require_status_url": True},
        "target_status_ids": [],
    }
    payload = assemble_payload(result, metadata, stage_name="smart_extract")
    finalize_payload(payload, metadata)

    assert payload["retrieval_status"] == "ok"
    assert payload["source_extraction_status"] in {"citation_backed", "extracted_unmapped"}
    assert payload["items"][0]["url"] == "https://x.com/xai/status/2071323738201837785"


def test_raw_fixture_parses_status_urls_from_non_json_text():
    result = _result_from_fixture("raw_non_json.json")
    assert not result.text.lstrip().startswith("{")
    posts = parse_raw_posts_from_text(result.text, {"count": 10, "handles": ["logicrw"]})
    assert [post["url"] for post in posts] == [
        "https://x.com/logicrw/status/2071385784154759468",
        "https://x.com/xai/status/2071323738201837785",
    ]
    assert "Fixture candidate" in posts[0]["text"]

    metadata = {
        "mode": "semantic_research",
        "intent": "research",
        "handles": ["logicrw"],
        "query": "gateway retrieval contracts",
        "count": 10,
        "sort": "relevance",
        "quality": {"min_items": 1},
        "target_status_ids": [],
    }
    payload = assemble_payload(result, metadata, stage_name="raw_expansion")
    finalize_payload(payload, metadata)
    assert payload["retrieval_status"] == "ok"
    assert len(payload["items"]) == 2
    assert any("not trusted" in warning for item in payload["items"] for warning in item["warnings"])
