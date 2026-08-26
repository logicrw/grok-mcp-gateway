import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_config_discards_unrelated_inherited_environment_variables(tmp_path):
    environment = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GROK_PROXY_AUTH_STATE": str(tmp_path / "auth_state.json"),
        "UNRELATED_SERVICE_CREDENTIAL": "must-not-reach-gateway",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import config, os; print(os.getenv('UNRELATED_SERVICE_CREDENTIAL')); print(os.getenv('GROK_PROXY_AUTH_STATE')); print(config.RUNTIME_ENV_DISCARDED_VARIABLE_COUNT)",
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        check=True,
        text=True,
    )
    lines = result.stdout.splitlines()
    assert lines[:2] == ["None", str(tmp_path / "auth_state.json")]
    assert int(lines[2]) >= 1
