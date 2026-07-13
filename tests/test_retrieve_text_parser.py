import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retrieve_text_parser import extract_status_targets, parse_raw_posts_from_text, status_id_from_url


def test_extract_status_targets_ignores_short_numbers_and_caps_targets():
    query = "likes 42 " + " ".join(str(2071385784154759460 + index) for index in range(7))
    targets, warnings = extract_status_targets(query, limit=5)

    assert len(targets) == 5
    assert "42" not in targets
    assert warnings == ["target status extraction capped at 5 IDs"]


def test_raw_parser_never_synthesizes_requested_target():
    raw = "Main Post:\n- ID: 9999999999999999999\n- Content: unrelated"
    metadata = {"target_status_ids": ["2071385784154759468"], "count": 3}

    assert parse_raw_posts_from_text(raw, metadata) == []


def test_raw_parser_extracts_labeled_status_and_content():
    status_id = "2071385784154759468"
    raw = f"Target status ID: {status_id}\nContent: deterministic raw text"

    posts = parse_raw_posts_from_text(raw, {"count": 3, "handles": ["xai"]})

    assert posts[0]["url"] == f"https://x.com/i/status/{status_id}"
    assert posts[0]["text"] == "deterministic raw text"
    assert posts[0]["warnings"] == ["parsed from non-JSON raw upstream text; not trusted"]


def test_raw_parser_does_not_turn_unlabeled_long_number_into_status():
    raw = "Search result says 9999999999999999999 views"

    assert parse_raw_posts_from_text(raw, {"count": 3}) == []


def test_status_id_from_url_requires_x_or_twitter_host():
    status_id = "2071385784154759468"

    assert status_id_from_url(f"https://x.com/xai/status/{status_id}") == status_id
    assert status_id_from_url(f"https://mobile.twitter.com/xai/status/{status_id}") == status_id
    assert status_id_from_url(f"https://x.com/xai/status/{status_id}/photo/1") == status_id
    assert status_id_from_url(f"https://twitter.com/xai/status/{status_id}/video/2") == status_id
    assert status_id_from_url(f"https://twitter.com/i/web/status/{status_id}") == status_id
    assert status_id_from_url(f"https://example.com/xai/status/{status_id}") is None
    assert status_id_from_url(f"https://x.com/xai/status/{status_id}/analytics") is None


def test_raw_parser_accepts_i_web_status_url():
    status_id = "2071385784154759468"
    raw = f"Main Post: https://twitter.com/i/web/status/{status_id}\nContent: archived link"

    posts = parse_raw_posts_from_text(raw, {"count": 3})

    assert posts[0]["url"] == f"https://twitter.com/i/web/status/{status_id}"


def test_raw_parser_rejects_uncontrolled_status_url_suffixes():
    status_id = "2071385784154759468"
    invalid_urls = [
        f"https://x.com/xai/status/{status_id}/analytics",
        f"https://x.com/xai/status/{status_id}/photo/not-a-number",
        f"https://x.com/xai/status/{status_id}/video/1/extra",
        f"https://x.com/xai/status/{status_id}/photos/1",
    ]

    for url in invalid_urls:
        assert parse_raw_posts_from_text(f"Main Post: {url}", {"count": 3}) == []


def test_raw_parser_accepts_controlled_suffix_and_query_parameters():
    status_id = "2071385784154759468"
    url = f"https://x.com/xai/status/{status_id}/photo/1?s=20"

    posts = parse_raw_posts_from_text(f"Main Post: {url}\nContent: media post", {"count": 3})

    assert posts[0]["url"] == url


def test_raw_parser_rejects_x_url_embedded_in_another_uri():
    status_id = "2071385784154759468"
    misleading_tokens = [
        f"https://evil.example/@x.com/xai/status/{status_id}",
        f"javascript:x.com/xai/status/{status_id}",
        f"mailto:x.com/xai/status/{status_id}",
        f"data:text/plain,x.com/xai/status/{status_id}",
        f"https://evil.example@x.com/xai/status/{status_id}",
    ]

    for token in misleading_tokens:
        assert parse_raw_posts_from_text(f"Main Post: {token}", {"count": 3}) == []


def test_raw_parser_accepts_wrapped_url_with_chinese_punctuation():
    status_id = "2071385784154759468"
    url = f"https://x.com/xai/status/{status_id}"
    raw_values = [
        f"原文：{url}。这是目标帖。",
        f"原文：{url}，这是目标帖。",
        f"“{url}”",
        f"‘{url}’",
        f"{url}、",
        f"{url}……",
        f"（{url}）。",
    ]

    for raw in raw_values:
        posts = parse_raw_posts_from_text(raw, {"count": 3})

        assert posts[0]["url"] == url
