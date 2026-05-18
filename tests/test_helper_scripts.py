from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = REPO_ROOT / "scripts" / "export_xai_oauth.py"
IMPORT_SCRIPT = REPO_ROOT / "scripts" / "import_xai_oauth.py"
REFRESH_REMOTE_SCRIPT = REPO_ROOT / "scripts" / "refresh_remote_xai_oauth.py"


def test_export_outputs_importable_json_on_stdout_and_warning_on_stderr(tmp_path):
    auth_path = tmp_path / ".hermes" / "auth.json"
    auth_path.parent.mkdir()
    auth_path.write_text(
        json.dumps(
            {
                "providers": {
                    "xai-oauth": {
                        "tokens": {"refresh_token": "refresh", "access_token": "access"},
                        "discovery": {"token_endpoint": "https://auth.x.ai/oauth2/token"},
                    },
                    "other": {"keep": True},
                }
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HERMES_AUTH_PATH"] = str(auth_path)

    result = subprocess.run(
        [sys.executable, str(EXPORT_SCRIPT)],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    exported = json.loads(result.stdout)
    assert exported["version"] == 1
    assert exported["source"] == "hermes"
    assert exported["xai-oauth"]["tokens"]["refresh_token"] == "refresh"
    assert "WARNING" in result.stderr
    assert not result.stdout.lstrip().startswith("#")


def test_export_supports_hermes_credential_pool_shape(tmp_path):
    auth_path = tmp_path / ".hermes" / "auth.json"
    auth_path.parent.mkdir()
    auth_path.write_text(
        json.dumps(
            {
                "credential_pool": {
                    "xai-oauth": [
                        {
                            "access_token": "pool-access",
                            "refresh_token": "pool-refresh",
                            "last_status": "ok",
                            "last_refresh": "2026-05-18T00:00:00Z",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HERMES_AUTH_PATH"] = str(auth_path)

    result = subprocess.run(
        [sys.executable, str(EXPORT_SCRIPT)],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    exported = json.loads(result.stdout)
    assert exported["xai-oauth"]["tokens"]["access_token"] == "pool-access"
    assert exported["xai-oauth"]["tokens"]["refresh_token"] == "pool-refresh"
    assert exported["xai-oauth"]["discovery"]["token_endpoint"] == "https://auth.x.ai/oauth2/token"


def test_import_merges_xai_oauth_preserves_other_providers_and_locks_permissions(tmp_path):
    auth_path = tmp_path / ".hermes" / "auth.json"
    auth_path.parent.mkdir(mode=0o755)
    auth_path.write_text(
        json.dumps({"providers": {"other": {"keep": True}}, "active_provider": "other"}),
        encoding="utf-8",
    )
    export_file = tmp_path / "xai-oauth.json"
    export_file.write_text(
        json.dumps(
            {
                "version": 1,
                "source": "hermes",
                "xai-oauth": {
                    "tokens": {"refresh_token": "refresh", "access_token": "access"},
                    "discovery": {"token_endpoint": "https://auth.x.ai/oauth2/token"},
                },
            }
        ),
        encoding="utf-8",
    )
    proxy_state = tmp_path / "state" / "auth_state.json"
    proxy_state.parent.mkdir()
    proxy_state.write_text('{"access_token":"stale"}', encoding="utf-8")

    env = os.environ.copy()
    env["HERMES_AUTH_PATH"] = str(auth_path)
    env["GROK_PROXY_AUTH_STATE"] = str(proxy_state)

    subprocess.run(
        [sys.executable, str(IMPORT_SCRIPT), str(export_file)],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    imported = json.loads(auth_path.read_text(encoding="utf-8"))
    assert imported["providers"]["other"] == {"keep": True}
    assert imported["providers"]["xai-oauth"]["tokens"]["refresh_token"] == "refresh"
    assert stat.S_IMODE(auth_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(auth_path.with_suffix(".json.bak").stat().st_mode) == 0o600
    assert not proxy_state.exists()


def test_import_can_preserve_proxy_state_when_requested(tmp_path):
    auth_path = tmp_path / ".hermes" / "auth.json"
    auth_path.parent.mkdir()
    auth_path.write_text(json.dumps({"providers": {}}), encoding="utf-8")

    export_file = tmp_path / "xai-oauth.json"
    export_file.write_text(
        json.dumps(
            {
                "version": 1,
                "source": "hermes",
                "xai-oauth": {
                    "tokens": {"refresh_token": "refresh", "access_token": "access"},
                    "discovery": {"token_endpoint": "https://auth.x.ai/oauth2/token"},
                },
            }
        ),
        encoding="utf-8",
    )
    proxy_state = tmp_path / "state" / "auth_state.json"
    proxy_state.parent.mkdir()
    proxy_state.write_text('{"access_token":"keep"}', encoding="utf-8")

    env = os.environ.copy()
    env["HERMES_AUTH_PATH"] = str(auth_path)
    env["GROK_PROXY_AUTH_STATE"] = str(proxy_state)

    subprocess.run(
        [sys.executable, str(IMPORT_SCRIPT), "--no-reset-proxy-state", str(export_file)],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    assert proxy_state.exists()
    assert json.loads(proxy_state.read_text(encoding="utf-8"))["access_token"] == "keep"


def test_refresh_remote_documents_split_chain_reauth_flag():
    result = subprocess.run(
        [sys.executable, str(REFRESH_REMOTE_SCRIPT), "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--print-reauth-command" in result.stdout
    assert "separate local xAI OAuth token chain" in result.stdout
