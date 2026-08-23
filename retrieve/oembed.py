from __future__ import annotations

from typing import Any, Dict

from retrieve.payload import _groups
from retrieve.text_parser import status_id_from_url
from x_oembed import OEmbedPost


def target_status_ids_needing_text(payload: Dict[str, Any], metadata: Dict[str, Any]) -> list[str]:
    target_status_ids = list(metadata.get("target_status_ids") or [])
    if not target_status_ids:
        return []
    by_id: dict[str, list[Dict[str, Any]]] = {}
    for item in payload["items"]:
        status_id = item.get("id")
        if status_id:
            by_id.setdefault(str(status_id), []).append(item)
    return [
        status_id
        for status_id in target_status_ids
        if not any(str(item.get("text") or "").strip() for item in by_id.get(status_id, []))
    ]


def merge_oembed_posts(payload: Dict[str, Any], posts: list[OEmbedPost]) -> None:
    if not posts:
        return
    for post in posts:
        item = {
            "id": post.status_id,
            "url": post.url,
            "author": post.author,
            "created_at": None,
            "text": post.text,
            "metrics": {},
            "relation": "primary",
            "confidence": "high",
            "warnings": [],
            "citation_backed": True,
            "public_embed_backed": True,
        }
        _upsert_item(payload, item)
        _append_post(payload, post)
        _append_source(payload, post)
    payload["groups"] = _groups(payload["items"])
    payload["source_extraction_status"] = "citation_backed"


def _upsert_item(payload: Dict[str, Any], item: Dict[str, Any]) -> None:
    for index, existing in enumerate(payload["items"]):
        if existing.get("id") == item.get("id"):
            merged = dict(existing)
            merged["text"] = item["text"]
            merged["confidence"] = "high"
            merged["citation_backed"] = True
            merged["public_embed_backed"] = True
            if not merged.get("url"):
                merged["url"] = item["url"]
            if not merged.get("author") and item.get("author"):
                merged["author"] = item["author"]
            payload["items"][index] = merged
            return
    payload["items"].append(item)


def _append_post(payload: Dict[str, Any], post: OEmbedPost) -> None:
    for existing in payload["posts"]:
        if not isinstance(existing, dict) or status_id_from_url(existing.get("url")) != post.status_id:
            continue
        existing["text"] = post.text
        existing["confidence"] = "high"
        existing["citation_backed"] = True
        if not existing.get("url"):
            existing["url"] = post.url
        if not existing.get("author") and post.author:
            existing["author"] = post.author
        return
    payload["posts"].append(
        {
            "text": post.text,
            "author": post.author,
            "created_at": None,
            "url": post.url,
            "metrics": {},
            "confidence": "high",
            "citation_backed": True,
        }
    )


def _append_source(payload: Dict[str, Any], post: OEmbedPost) -> None:
    if any(
        status_id_from_url(existing.get("url")) == post.status_id
        for existing in payload["sources"]
        if isinstance(existing, dict)
    ):
        return
    payload["sources"].append({"url": post.url, "title": "X public oEmbed"})
