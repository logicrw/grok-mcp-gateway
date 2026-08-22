import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_ENV_KEYS = ("GROK_PROXY_RETRIEVE_MODEL", "GROK_PROXY_MCP_MODEL")


def _read_default_model(env_overrides: dict[str, str]) -> str:
    env = os.environ.copy()
    for key in MODEL_ENV_KEYS:
        env.pop(key, None)
    env.update(env_overrides)
    completed = subprocess.run(
        [sys.executable, "-c", "import mcp_x_search; print(mcp_x_search.DEFAULT_MODEL)"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_default_retrieve_model_is_grok_4_6_when_overrides_are_absent():
    assert _read_default_model({}) == "grok-4.6"



def test_legacy_model_override_remains_supported():
    assert _read_default_model({"GROK_PROXY_MCP_MODEL": "grok-legacy"}) == "grok-legacy"


def test_retrieve_model_override_keeps_precedence():
    assert (
        _read_default_model(
            {
                "GROK_PROXY_RETRIEVE_MODEL": "grok-retrieve",
                "GROK_PROXY_MCP_MODEL": "grok-legacy",
            }
        )
        == "grok-retrieve"
    )


def _read_smart_model(env_overrides: dict[str, str]) -> str:
    env = os.environ.copy()
    for key in MODEL_ENV_KEYS:
        env.pop(key, None)
    env.update(env_overrides)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from retrieve_policy import get_routing_config; print(get_routing_config().smart_model)",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_retrieve_model_override_drives_smart_lane_routing():
    assert _read_smart_model({"GROK_PROXY_RETRIEVE_MODEL": "grok-retrieve"}) == "grok-retrieve"


def test_legacy_model_override_drives_smart_lane_routing():
    assert _read_smart_model({"GROK_PROXY_MCP_MODEL": "grok-legacy"}) == "grok-legacy"
