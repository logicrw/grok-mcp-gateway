from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_x_search
import token_manager
import xai_responses
from retrieve.policy import (
    build_responses_payload,
    build_xai_responses_payload,
    evaluate_quality,
    resolve_plan,
    should_escalate_to_smart,
)
from retrieve.schema import RAW_MODEL


class RoutingV2Tests(unittest.TestCase):
    def test_latest_by_handle_routes_fast(self) -> None:
        plan = resolve_plan(
            {
                "intent": "auto",
                "mode": "latest_by_handle",
                "handles": ["logicrw"],
                "count": 5,
                "query": None,
                "quality": {"min_items": 1},
            }
        )
        self.assertEqual(plan.initial_lane, "fast")
        self.assertEqual(plan.model, "grok-4.20-0309-non-reasoning")
        self.assertIsNone(plan.reasoning_effort)
        self.assertEqual(plan.max_turns, 2)

    def test_verify_claim_routes_smart_high(self) -> None:
        plan = resolve_plan(
            {
                "intent": "verify_claim",
                "mode": "source_discovery",
                "query": "A claim to verify",
                "count": 10,
            }
        )
        self.assertEqual(plan.initial_lane, "smart")
        self.assertEqual(plan.reasoning_effort, "high")

    def test_explicit_target_is_deterministic_exact_only(self) -> None:
        plan = resolve_plan(
            {
                "intent": "posts",
                "mode": "structured_posts",
                "target_status_ids": ["1234567890123456789"],
            }
        )
        self.assertEqual(plan.initial_lane, "deterministic")
        self.assertEqual(plan.target_strategy, "exact_only")
        self.assertIsNone(plan.model)

    def test_verify_with_target_seeds_research(self) -> None:
        plan = resolve_plan(
            {
                "intent": "verify_claim",
                "mode": "source_discovery",
                "target_status_ids": ["1234567890123456789"],
            }
        )
        self.assertEqual(plan.initial_lane, "deterministic")
        self.assertEqual(plan.target_strategy, "seed_then_research")
        self.assertEqual(plan.objective_mode, "claim_verification")

    def test_explicit_model_is_pinned(self) -> None:
        plan = resolve_plan(
            {
                "intent": "research",
                "mode": "semantic_research",
                "query": "topic",
            },
            explicit_model="custom-preview-model",
        )
        self.assertEqual(plan.initial_lane, "custom")
        self.assertFalse(plan.allow_smart_escalation)
        self.assertIsNone(plan.reasoning_effort)

    def test_fast_payload_never_contains_reasoning(self) -> None:
        plan = resolve_plan(
            {
                "intent": "auto",
                "mode": "latest_by_handle",
                "handles": ["logicrw"],
                "count": 5,
            }
        )
        payload = build_responses_payload(
            query="latest posts",
            x_search_tool={"type": "x_search", "allowed_x_handles": ["logicrw"]},
            plan=plan,
        )
        self.assertNotIn("reasoning", payload)
        self.assertEqual(payload["max_turns"], 2)
        self.assertFalse(payload["store"])
        self.assertEqual(
            payload["text"]["format"]["type"],
            "json_schema",
        )

    def test_smart_payload_contains_valid_reasoning(self) -> None:
        plan = resolve_plan(
            {
                "intent": "verify_claim",
                "mode": "source_discovery",
                "query": "claim",
            }
        )
        payload = build_responses_payload(
            query="claim",
            x_search_tool={"type": "x_search"},
            plan=plan,
        )
        self.assertEqual(payload["reasoning"], {"effort": "high"})

    def test_quality_and_budget_gate(self) -> None:
        plan = resolve_plan(
            {
                "intent": "auto",
                "mode": "latest_by_handle",
                "handles": ["logicrw"],
                "count": 5,
                "quality": {"min_items": 1},
            }
        )
        quality = evaluate_quality(
            {"posts": []},
            {"quality": {"min_items": 1}},
        )
        self.assertFalse(quality.passed)
        self.assertTrue(
            should_escalate_to_smart(
                plan,
                quality,
                remaining_seconds=40.0,
            )
        )
        self.assertFalse(
            should_escalate_to_smart(
                plan,
                quality,
                remaining_seconds=20.0,
            )
        )

    def test_fast_production_payload_matches_plan_builder(self) -> None:
        plan = resolve_plan(
            {
                "intent": "auto",
                "mode": "latest_by_handle",
                "handles": ["logicrw"],
                "count": 5,
                "query": None,
                "quality": {"min_items": 1},
            }
        )
        tool = {"type": "x_search", "allowed_x_handles": ["logicrw"]}
        from_plan = build_responses_payload(query="latest posts", x_search_tool=tool, plan=plan)
        production = mcp_x_search._x_search_payload(
            {
                "query": "latest posts",
                "model": plan.model,
                "allowed_x_handles": ["logicrw"],
                "_max_turns": plan.max_turns,
                "_structured_output": True,
            }
        )
        self.assertEqual(production, from_plan)
        self.assertNotIn("reasoning", production)

    def test_smart_production_payload_matches_plan_builder(self) -> None:
        plan = resolve_plan(
            {
                "intent": "verify_claim",
                "mode": "source_discovery",
                "query": "claim",
                "count": 10,
            }
        )
        from_plan = build_responses_payload(
            query="claim",
            x_search_tool={"type": "x_search"},
            plan=plan,
        )
        production = mcp_x_search._x_search_payload(
            {
                "query": "claim",
                "model": plan.model,
                "_max_turns": plan.max_turns,
                "_structured_output": True,
                "_reasoning_effort": plan.reasoning_effort,
            }
        )
        self.assertEqual(production, from_plan)
        self.assertEqual(production["reasoning"], {"effort": "high"})

    def test_raw_expansion_payload_omits_schema_reasoning_and_max_turns(self) -> None:
        query = "cold topic"
        production = mcp_x_search._x_search_payload({"query": query, "model": RAW_MODEL})
        expected = build_xai_responses_payload(
            query=query,
            x_search_tool={"type": "x_search"},
            model=RAW_MODEL,
            max_turns=None,
            store=False,
            structured_output=False,
            reasoning_effort="high",
        )
        self.assertEqual(production, expected)
        self.assertNotIn("text", production)
        self.assertNotIn("reasoning", production)
        self.assertNotIn("max_turns", production)
        self.assertFalse(production["store"])

    def test_call_x_search_posts_shared_builder_body(self) -> None:
        captured: list[dict] = []

        async def fake_post(payload):
            captured.append(dict(payload))
            return xai_responses.ResponsesResult('{"posts":[]}', {}, [], None, str(payload["model"]))

        original = xai_responses.post
        xai_responses.post = fake_post  # type: ignore[method-assign]
        try:
            plan = resolve_plan(
                {
                    "intent": "auto",
                    "mode": "latest_by_handle",
                    "handles": ["logicrw"],
                    "count": 5,
                    "query": None,
                    "quality": {"min_items": 1},
                }
            )
            tool = {"type": "x_search", "allowed_x_handles": ["logicrw"]}
            asyncio.run(
                mcp_x_search._call_x_search_result(
                    {
                        "query": "latest posts",
                        "model": plan.model,
                        "allowed_x_handles": ["logicrw"],
                        "_max_turns": plan.max_turns,
                        "_structured_output": True,
                    }
                )
            )
            self.assertEqual(
                captured[0],
                build_responses_payload(query="latest posts", x_search_tool=tool, plan=plan),
            )
        finally:
            xai_responses.post = original  # type: ignore[method-assign]


class TokenRefreshCoalescingTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_concurrent_401_callers_refresh_only_once(self) -> None:
        state = {"access_token": "stale_token_123"}
        refresh_count = 0

        async def fake_read_local_state():
            return dict(state)

        async def fake_refresh_access_token(current):
            nonlocal refresh_count
            refresh_count += 1
            await asyncio.sleep(0.02)
            state["access_token"] = "fresh_token_456"
            return dict(state)

        original_read = token_manager.read_local_state
        original_refresh = token_manager.refresh_access_token
        original_is_expiring = token_manager._is_expiring

        try:
            token_manager.read_local_state = fake_read_local_state
            token_manager.refresh_access_token = fake_refresh_access_token
            token_manager._is_expiring = lambda *args: False

            tokens = await asyncio.gather(
                *[
                    token_manager.get_access_token(
                        force_refresh=True,
                        stale_access_token="stale_token_123",
                    )
                    for _ in range(5)
                ]
            )

            self.assertEqual(tokens, ["fresh_token_456"] * 5)
            self.assertEqual(refresh_count, 1)
        finally:
            token_manager.read_local_state = original_read
            token_manager.refresh_access_token = original_refresh
            token_manager._is_expiring = original_is_expiring


if __name__ == "__main__":
    unittest.main()
