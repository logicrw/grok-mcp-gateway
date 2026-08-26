from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Literal, Mapping, Optional, cast

import config
from retrieve.schema import X_POSTS_STAGE_SCHEMA

ObjectiveMode = Literal[
    "latest_by_handle",
    "structured_posts",
    "semantic_research",
    "source_discovery",
    "reaction_tracking",
    "claim_verification",
]
TargetStrategy = Literal["none", "exact_only", "seed_then_research"]
ExecutionLane = Literal["deterministic", "fast", "smart", "custom"]
ReasoningEffort = Literal["low", "medium", "high", "xhigh"]


@dataclass(frozen=True)
class ModelCapabilities:
    reasoning_efforts: FrozenSet[str]
    structured_outputs: bool


@dataclass(frozen=True)
class RoutingConfig:
    smart_model: str = "grok-4.6"
    fast_model: str = "grok-4.20-0309-non-reasoning"
    auto_tiering: bool = True
    fast_max_turns: int = 2
    smart_max_turns: int = 3
    fast_stage_timeout_seconds: float = 10.0

    smart_stage_timeout_seconds: float = 120.0
    raw_stage_timeout_seconds: float = 50.0
    smart_escalation_min_remaining_seconds: float = 35.0
    fallback_reserve_seconds: float = 8.0
    fast_max_handles: int = 2
    fast_max_count: int = 10
    fast_max_query_chars: int = 200


def get_routing_config() -> RoutingConfig:
    """Return RoutingConfig populated from current config module values."""
    return RoutingConfig(
        smart_model=config.GROK_PROXY_RETRIEVE_MODEL or "grok-4.6",
        fast_model=config.GROK_PROXY_FAST_MODEL,
        auto_tiering=config.GROK_PROXY_ENABLE_AUTO_TIERING,
        fast_max_turns=config.GROK_PROXY_FAST_MAX_TURNS,
        smart_max_turns=config.GROK_PROXY_SMART_MAX_TURNS,
        fast_stage_timeout_seconds=config.GROK_PROXY_FAST_STAGE_TIMEOUT_SECONDS,
        smart_stage_timeout_seconds=config.GROK_PROXY_SMART_STAGE_TIMEOUT_SECONDS,
        raw_stage_timeout_seconds=config.GROK_PROXY_RAW_STAGE_TIMEOUT_SECONDS,
        smart_escalation_min_remaining_seconds=config.GROK_PROXY_SMART_ESCALATION_MIN_REMAINING_SECONDS,
        fallback_reserve_seconds=config.GROK_PROXY_FALLBACK_RESERVE_SECONDS,
        fast_max_handles=2,
        fast_max_count=10,
        fast_max_query_chars=200,
    )


@dataclass(frozen=True)
class RetrievalPlan:
    objective_mode: ObjectiveMode
    target_strategy: TargetStrategy
    initial_lane: ExecutionLane
    model: Optional[str]
    reasoning_effort: Optional[ReasoningEffort]
    max_turns: int
    stage_timeout_seconds: float
    allow_smart_escalation: bool
    explicit_model: bool
    route_reason: str
    route_warning: Optional[str] = None


@dataclass(frozen=True)
class QualityDecision:
    passed: bool
    reason: str


DEFAULT_CAPABILITIES: Dict[str, ModelCapabilities] = {
    "grok-4.5": ModelCapabilities(
        reasoning_efforts=frozenset({"low", "medium", "high"}),
        structured_outputs=True,
    ),
    "grok-4.5-latest": ModelCapabilities(
        reasoning_efforts=frozenset({"low", "medium", "high"}),
        structured_outputs=True,
    ),
    "grok-4.6": ModelCapabilities(
        reasoning_efforts=frozenset({"low", "medium", "high", "xhigh"}),
        structured_outputs=True,
    ),
    "grok-4.6-latest": ModelCapabilities(
        reasoning_efforts=frozenset({"low", "medium", "high", "xhigh"}),
        structured_outputs=True,
    ),
    "grok-build-latest": ModelCapabilities(
        reasoning_efforts=frozenset({"low", "medium", "high"}),
        structured_outputs=True,
    ),
    "grok-4.20-0309-non-reasoning": ModelCapabilities(
        reasoning_efforts=frozenset(),
        structured_outputs=True,
    ),
    "grok-4.20-non-reasoning": ModelCapabilities(
        reasoning_efforts=frozenset(),
        structured_outputs=True,
    ),
    "grok-4.20-non-reasoning-latest": ModelCapabilities(
        reasoning_efforts=frozenset(),
        structured_outputs=True,
    ),
}


class RequestBudget:
    def __init__(self, total_seconds: Optional[float] = None) -> None:
        total = config.GROK_PROXY_RETRIEVE_TOTAL_TIMEOUT_SECONDS if total_seconds is None else total_seconds
        self._deadline = time.monotonic() + max(0.0, total)

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def stage_timeout(self, stage_seconds: float) -> float:
        return min(max(0.0, stage_seconds), self.remaining())


def _objective_mode(metadata: Mapping[str, Any]) -> ObjectiveMode:
    intent = str(metadata.get("intent") or "auto")
    mode = str(metadata.get("mode") or "semantic_research")

    if intent == "verify_claim" or mode == "claim_verification":
        return "claim_verification"
    if intent == "reaction_tracking" or mode == "reaction_tracking":
        return "reaction_tracking"
    if intent == "source_discovery" or mode == "source_discovery":
        return "source_discovery"
    if intent == "research" or mode == "semantic_research":
        return "semantic_research"
    if mode == "latest_by_handle":
        return "latest_by_handle"
    return "structured_posts"


def _target_strategy(
    metadata: Mapping[str, Any],
    objective: ObjectiveMode,
) -> TargetStrategy:
    if not metadata.get("target_status_ids"):
        return "none"
    if objective in {
        "claim_verification",
        "reaction_tracking",
        "semantic_research",
        "source_discovery",
    }:
        return "seed_then_research"
    return "exact_only"


def _smart_effort(
    objective: ObjectiveMode, explicit_effort: Optional[str] = None
) -> ReasoningEffort:
    if explicit_effort in {"low", "medium", "high", "xhigh"}:
        return cast(ReasoningEffort, explicit_effort)
    configured = (getattr(config, "GROK_PROXY_SMART_REASONING_EFFORT", "") or "").strip().lower()
    if configured in {"low", "medium", "high", "xhigh"}:
        return cast(ReasoningEffort, configured)
    if objective == "claim_verification":
        return "high"
    if objective in {
        "semantic_research",
        "source_discovery",
        "reaction_tracking",
    }:
        return "low"
    return "low"


def _is_simple_fast_request(
    metadata: Mapping[str, Any],
    objective: ObjectiveMode,
    routing_config: RoutingConfig,
) -> bool:
    if objective == "latest_by_handle":
        return (
            len(metadata.get("handles") or []) <= routing_config.fast_max_handles
            and int(metadata.get("count") or 10) <= routing_config.fast_max_count
            and not (metadata.get("best_effort_filters") or {})
        )

    if metadata.get("reasoning_effort"):
        return False

    if metadata.get("intent") in {
        "research",
        "verify_claim",
        "reaction_tracking",
        "source_discovery",
    }:
        return False

    if objective not in {"structured_posts", "semantic_research"}:
        return False

    quality = metadata.get("quality") or {}
    query = str(metadata.get("query") or "")
    complex_markers = (
        "verify",
        "fact-check",
        "reaction",
        "controversy",
        "compare",
        "source",
        "证实",
        "核查",
        "反应",
        "争议",
        "对比",
        "信源",
    )
    return (
        len(metadata.get("handles") or []) <= routing_config.fast_max_handles
        and int(metadata.get("count") or 10) <= routing_config.fast_max_count
        and len(query) <= routing_config.fast_max_query_chars
        and not (metadata.get("best_effort_filters") or {})
        and int(quality.get("min_items") or 1) <= 3
        and not bool(quality.get("require_original_text"))
        and not bool(quality.get("require_status_url"))
        and not any(marker in query.lower() for marker in complex_markers)
    )



def _capabilities_for(
    model: str,
    capabilities: Mapping[str, ModelCapabilities],
) -> ModelCapabilities:
    return capabilities.get(
        model.strip().lower(),
        ModelCapabilities(reasoning_efforts=frozenset(), structured_outputs=False),
    )


def _validated_effort(
    model: str,
    desired: Optional[ReasoningEffort],
    capabilities: Mapping[str, ModelCapabilities],
) -> Optional[ReasoningEffort]:
    if desired is None:
        return None
    caps = _capabilities_for(model, capabilities)
    return desired if desired in caps.reasoning_efforts else None


def model_supports_reasoning_effort(model: str) -> bool:
    """Return True only if the model explicitly supports reasoning effort."""
    caps = _capabilities_for(model, DEFAULT_CAPABILITIES)
    return bool(caps.reasoning_efforts)


def model_supports_structured_output(model: str) -> bool:
    """Return True only if the model is known to support strict JSON schema output."""
    caps = _capabilities_for(model, DEFAULT_CAPABILITIES)
    return caps.structured_outputs


def resolve_plan(
    metadata: Mapping[str, Any],
    *,
    explicit_model: Optional[str] = None,
    config: Optional[RoutingConfig] = None,
    capabilities: Mapping[str, ModelCapabilities] = DEFAULT_CAPABILITIES,
) -> RetrievalPlan:
    """Return a deterministic execution plan.

    Exact targets are represented as a deterministic initial lane. The caller
    should run oEmbed first, then call resolve_target_fallback_plan only for
    missing targets.
    """
    routing_config = config or get_routing_config()
    objective = _objective_mode(metadata)
    target_strategy = _target_strategy(metadata, objective)
    pinned_model = (explicit_model or "").strip()

    if target_strategy != "none":
        return RetrievalPlan(
            objective_mode=objective,
            target_strategy=target_strategy,
            initial_lane="deterministic",
            model=None,
            reasoning_effort=None,
            max_turns=0,
            stage_timeout_seconds=0.0,
            allow_smart_escalation=not bool(pinned_model),
            explicit_model=bool(pinned_model),
            route_reason="explicit_status_target",
        )

    if pinned_model:
        desired = _smart_effort(objective, metadata.get("reasoning_effort"))
        validated = _validated_effort(pinned_model, desired, capabilities)
        notices: list[str] = []
        if validated is None and desired is not None:
            # Auto-selected effort is silently unusable on this model; surface the
            # effective-policy change instead of quietly dropping reasoning.
            notices.append(
                f"reasoning_effort '{desired}' is not supported by model '{pinned_model}'; "
                "request will run without reasoning"
            )
        if not _capabilities_for(pinned_model, capabilities).structured_outputs:
            # Sending a strict json_schema to an unknown model risks an upstream
            # 400; run without it and say so instead of failing opaquely.
            notices.append(
                f"model '{pinned_model}' is not known to support structured outputs; "
                "stage will run without the strict JSON schema"
            )
        return RetrievalPlan(
            objective_mode=objective,
            target_strategy="none",
            initial_lane="custom",
            model=pinned_model,
            reasoning_effort=validated,
            max_turns=routing_config.smart_max_turns,
            stage_timeout_seconds=routing_config.smart_stage_timeout_seconds,
            allow_smart_escalation=False,
            explicit_model=True,
            route_reason="explicit_model",
            route_warning="; ".join(notices) if notices else None,
        )

    if routing_config.auto_tiering and _is_simple_fast_request(metadata, objective, routing_config):
        return RetrievalPlan(
            objective_mode=objective,
            target_strategy="none",
            initial_lane="fast",
            model=routing_config.fast_model,
            reasoning_effort=None,
            max_turns=routing_config.fast_max_turns,
            stage_timeout_seconds=routing_config.fast_stage_timeout_seconds,
            allow_smart_escalation=True,
            explicit_model=False,
            route_reason="conservative_simple_request",
        )

    desired_effort = _smart_effort(objective, metadata.get("reasoning_effort"))
    effort = _validated_effort(routing_config.smart_model, desired_effort, capabilities)
    return RetrievalPlan(
        objective_mode=objective,
        target_strategy="none",
        initial_lane="smart",
        model=routing_config.smart_model,
        reasoning_effort=effort,
        max_turns=routing_config.smart_max_turns,
        stage_timeout_seconds=routing_config.smart_stage_timeout_seconds,
        allow_smart_escalation=False,
        explicit_model=False,
        route_reason=(
            "auto_tiering_disabled"
            if not routing_config.auto_tiering
            else "complex_or_unclassified_request"
        ),
        route_warning=(
            None
            if effort is not None
            else (
                f"reasoning_effort '{desired_effort}' is not supported by model "
                f"'{routing_config.smart_model}'; request will run without reasoning"
            )
        ),
    )


def resolve_target_fallback_plan(
    *,
    smart: bool,
    objective: ObjectiveMode,
    config: Optional[RoutingConfig] = None,
    capabilities: Mapping[str, ModelCapabilities] = DEFAULT_CAPABILITIES,
) -> RetrievalPlan:
    routing_config = config or get_routing_config()
    model = routing_config.smart_model if smart else routing_config.fast_model
    effort = (
        _validated_effort(model, _smart_effort(objective), capabilities)
        if smart
        else None
    )
    return RetrievalPlan(
        objective_mode=objective,
        target_strategy="exact_only",
        initial_lane="smart" if smart else "fast",
        model=model,
        reasoning_effort=effort,
        max_turns=routing_config.smart_max_turns if smart else 1,
        stage_timeout_seconds=(
            routing_config.smart_stage_timeout_seconds
            if smart
            else routing_config.fast_stage_timeout_seconds
        ),
        allow_smart_escalation=not smart,
        explicit_model=False,
        route_reason="missing_exact_target_text",
    )


def evaluate_quality(
    payload: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> QualityDecision:
    """Evaluate result quality without deciding which fallback to run."""
    posts = payload.get("posts")
    normalized_posts = posts if isinstance(posts, list) else []
    quality = metadata.get("quality") or {}
    min_items = int(quality.get("min_items") or 1)

    target_match = payload.get("target_match")
    if isinstance(target_match, Mapping) and target_match.get("missing"):
        return QualityDecision(False, "missing_target")

    if len(normalized_posts) < min_items:
        return QualityDecision(False, "insufficient_items")

    if quality.get("require_status_url"):
        if not all(
            isinstance(post, Mapping)
            and isinstance(post.get("url"), str)
            and "/status/" in post["url"]
            for post in normalized_posts
        ):
            return QualityDecision(False, "missing_status_url")

    if quality.get("require_original_text"):
        if not all(
            isinstance(post, Mapping)
            and isinstance(post.get("text"), str)
            and bool(post["text"].strip())
            and not bool(post.get("truncated"))
            for post in normalized_posts
        ):
            return QualityDecision(False, "missing_original_text")

    return QualityDecision(True, "pass")


def should_escalate_to_smart(
    plan: RetrievalPlan,
    quality: QualityDecision,
    *,
    remaining_seconds: float,
    config: Optional[RoutingConfig] = None,
) -> bool:
    routing_config = config or get_routing_config()
    if quality.passed:
        return False
    if plan.initial_lane != "fast" or not plan.allow_smart_escalation:
        return False
    # A latest-by-handle request is a bounded timeline lookup.  An empty Fast
    # result is useful evidence (there were no matching posts in that window),
    # not a reason to spend another two model calls attempting semantic
    # recovery.  Callers that genuinely need broader research use an explicit
    # research/reaction intent and start in the Smart lane.
    if plan.objective_mode == "latest_by_handle":
        return False
    minimum = max(
        routing_config.smart_escalation_min_remaining_seconds,
        routing_config.raw_stage_timeout_seconds + routing_config.fallback_reserve_seconds + 10.0,
    )
    return remaining_seconds >= minimum


def resolve_store_flag(explicit: Optional[bool] = None) -> Optional[bool]:
    """Return the Responses `store` value, or None to omit the field."""
    if explicit is not None:
        return bool(explicit)
    if config.GROK_PROXY_STORE_RESPONSES is False:
        return False
    return None


def build_xai_responses_payload(
    *,
    query: str,
    x_search_tool: Mapping[str, Any],
    model: str,
    max_turns: Optional[int] = None,
    store: Optional[bool] = False,
    structured_output: bool = False,
    reasoning_effort: Optional[str] = None,
    capabilities: Mapping[str, ModelCapabilities] = DEFAULT_CAPABILITIES,
) -> Dict[str, Any]:
    """Build the unique xAI Responses body used by production and plan tests."""
    payload: Dict[str, Any] = {
        "model": model,
        "input": query,
        "tools": [dict(x_search_tool)],
        "temperature": 0,
    }
    if isinstance(max_turns, int) and max_turns > 0:
        payload["max_turns"] = max_turns
    if store is not None:
        payload["store"] = bool(store)

    caps = _capabilities_for(model, capabilities)
    if reasoning_effort in caps.reasoning_efforts:
        payload["reasoning"] = {"effort": reasoning_effort}

    if structured_output:
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": "x_posts_stage_result",
                "schema": X_POSTS_STAGE_SCHEMA,
                "strict": True,
            }
        }
    return payload


def build_responses_payload(
    *,
    query: str,
    x_search_tool: Mapping[str, Any],
    plan: RetrievalPlan,
    capabilities: Mapping[str, ModelCapabilities] = DEFAULT_CAPABILITIES,
) -> Dict[str, Any]:
    if not plan.model:
        raise ValueError("A generative lane requires a model")
    caps = _capabilities_for(plan.model, capabilities)
    return build_xai_responses_payload(
        query=query,
        x_search_tool=x_search_tool,
        model=plan.model,
        max_turns=plan.max_turns,
        store=resolve_store_flag(),
        structured_output=caps.structured_outputs,
        reasoning_effort=plan.reasoning_effort,
        capabilities=capabilities,
    )
