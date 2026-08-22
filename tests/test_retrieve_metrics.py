import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import retrieve_metrics
import xai_responses


def test_metrics_include_stage_usage_timeout_and_final_status():
    retrieve_metrics.record_retrieval_status("ok")
    retrieve_metrics.record_stage(
        stage="stable_extract",
        model="grok-4.5",
        status="success",
        reasoning_effort="low",
        duration_seconds=1.25,
    )
    retrieve_metrics.record_timeout("stage")
    retrieve_metrics.record_usage(
        stage="stable_extract",
        model="grok-4.5",
        reasoning_tokens=12,
        x_search_calls=3,
    )
    retrieve_metrics.record_route(lane="fast", objective_mode="latest_by_handle", escalated=False)

    lines = "\n".join(retrieve_metrics.metrics_lines())

    assert 'mcp_x_retrieve_final_status_total{status="ok"}' in lines
    assert 'stage="stable_extract"' in lines
    assert 'reasoning_effort="low"' in lines
    assert 'mcp_x_retrieve_timeout_total{type="stage"}' in lines
    assert 'mcp_x_retrieve_reasoning_tokens_total{stage="stable_extract",model_role="stable"} 12' in lines
    assert 'mcp_x_retrieve_x_search_calls_total{stage="stable_extract",model_role="stable"} 3' in lines
    assert (
        'mcp_x_retrieve_route_total{lane="fast",objective_mode="latest_by_handle",escalated="false"}'
        in lines
    )


def test_custom_model_names_cannot_expand_metric_cardinality():
    for index in range(25):
        retrieve_metrics.record_stage(
            stage="target_fallback",
            model=f"user-model-{index}",
            status="success",
            reasoning_effort="none",
            duration_seconds=0.01,
        )

    matching = [
        line
        for line in retrieve_metrics.metrics_lines()
        if line.startswith("mcp_x_retrieve_stage_total")
        and 'stage="target_fallback"' in line
        and 'status="success"' in line
        and 'reasoning_effort="none"' in line
    ]

    assert len(matching) == 1
    assert 'model_role="stable"' in matching[0]
    assert "user-model" not in matching[0]


def test_parse_usage_metrics_supports_nested_and_flat_shapes():
    nested = {
        "output_tokens_details": {"reasoning_tokens": 7},
        "server_side_tool_usage_details": {"x_search_calls": 2},
    }
    assert xai_responses.parse_usage_metrics(nested) == (7, 2)
    assert xai_responses.parse_usage_metrics({"reasoning_tokens": 4, "x_search_calls": 1}) == (4, 1)
