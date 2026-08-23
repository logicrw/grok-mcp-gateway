"""Cross-process refresh transaction integration test.

Two real gateway subprocesses share one auth_state.json and race a refresh
against a fake OAuth server that rotates R0 -> R1 exactly once. The flock
transaction must ensure exactly one upstream refresh and an on-disk R1 that is
never rolled back by the loser.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import token_manager

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
_FUTURE_EXP = 4_102_444_800


def _unsigned_jwt(payload):
    header = {"alg": "none", "typ": "JWT"}

    def encode(part):
        raw = json.dumps(part, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(payload)}."


_WORKER_SCRIPT = """
import asyncio
import sys

sys.path.insert(0, sys.argv[1])
import token_manager

token_manager._validate_token_endpoint = lambda endpoint: endpoint


async def main():
    state = await token_manager.read_local_state()
    await token_manager.refresh_access_token(state)


asyncio.run(main())
"""


class _RotatingOAuthHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", 0))
        params = {key: values[0] for key, values in parse_qs(self.rfile.read(length).decode()).items()}
        refresh_token = params.get("refresh_token", "")
        with self.server.lock:
            first_with_r0 = refresh_token == "R0" and not self.server.rotated
            if first_with_r0:
                self.server.rotated = True
            self.server.requests.append(refresh_token)
        if first_with_r0:
            body = json.dumps(
                {
                    "access_token": _unsigned_jwt({"exp": _FUTURE_EXP, "client_id": "client-1"}),
                    "refresh_token": "R1",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            ).encode()
            self.send_response(200)
        else:
            body = json.dumps({"error": "invalid_grant"}).encode()
            self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # silence request logging
        return


@pytest.mark.skipif(token_manager.fcntl is None, reason="flock-based transaction requires a POSIX host")
def test_two_processes_refresh_once_and_keep_rotated_token(tmp_path):
    state_path = tmp_path / "auth_state.json"
    state_path.write_text(
        json.dumps(
            {
                "access_token": _unsigned_jwt({"exp": 1000, "client_id": "client-1"}),
                "refresh_token": "R0",
                "client_id": "client-1",
                "token_endpoint": "https://auth.x.ai/oauth2/token",
                "state_version": 1,
            }
        ),
        encoding="utf-8",
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), _RotatingOAuthHandler)
    server.lock = threading.Lock()
    server.rotated = False
    server.requests = []
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        token_endpoint = f"http://127.0.0.1:{server.server_address[1]}/oauth2/token"
        # The worker validates the endpoint strictly; point state at the fake server.
        state = json.loads(state_path.read_text())
        state["token_endpoint"] = token_endpoint
        state_path.write_text(json.dumps(state), encoding="utf-8")

        worker = tmp_path / "worker.py"
        worker.write_text(_WORKER_SCRIPT, encoding="utf-8")
        env = dict(os.environ)
        env["GROK_PROXY_AUTH_STATE"] = str(state_path)

        processes = [
            subprocess.Popen(
                [sys.executable, str(worker), _REPO_ROOT],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        for process in processes:
            _, stderr = process.communicate(timeout=60)
            assert process.returncode == 0, stderr.decode()
    finally:
        server.shutdown()
        server.server_close()

    # Exactly one upstream refresh; the loser adopted the winner's state under
    # the flock instead of replaying the consumed R0.
    assert server.requests.count("R0") == 1
    on_disk = json.loads(state_path.read_text())
    assert on_disk["refresh_token"] == "R1"
    assert on_disk["state_version"] >= 2
    assert not on_disk.get("reauth_required")
