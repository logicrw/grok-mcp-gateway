#!/usr/bin/env python3
"""Refresh xAI Grok OAuth credentials on a remote Grok MCP Gateway host.

This helper exports only the local Hermes `xai-oauth` provider, copies it to a
remote/headless Grok MCP Gateway host, imports it there, removes stale proxy
token state through `import_xai_oauth.py`, restarts the service, and optionally
runs a health check.

Recommended split-chain flow:
    1. Authenticate Hermes locally to get a fresh xAI OAuth refresh-token chain.
    2. Run this helper to transfer that chain to the proxy host.
    3. Re-authenticate Hermes locally so Hermes and the proxy each own a separate
       refresh-token chain.

Example:
    python scripts/refresh_remote_xai_oauth.py \
      --host user@example.com \
      --identity ~/.ssh/id_ed25519
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = REPO_ROOT / "scripts" / "export_xai_oauth.py"


def run(cmd: list[str], *, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    if not quiet:
        print("$ " + " ".join(shlex.quote(part) for part in cmd), file=sys.stderr)
    return subprocess.run(cmd, check=True, text=True)


def ssh_base(args: argparse.Namespace) -> list[str]:
    cmd = ["ssh", "-o", "BatchMode=yes"]
    if args.identity:
        cmd.extend(["-i", str(Path(args.identity).expanduser())])
    if args.port:
        cmd.extend(["-p", str(args.port)])
    return cmd


def scp_base(args: argparse.Namespace) -> list[str]:
    cmd = ["scp", "-q"]
    if args.identity:
        cmd.extend(["-i", str(Path(args.identity).expanduser())])
    if args.port:
        cmd.extend(["-P", str(args.port)])
    return cmd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh remote Grok MCP Gateway OAuth credentials.")
    parser.add_argument("--host", required=True, help="Remote SSH target, e.g. user@example.com")
    parser.add_argument("--identity", help="SSH private key path")
    parser.add_argument("--port", type=int, help="SSH port")
    parser.add_argument("--remote-dir", default="/opt/grok-mcp-gateway", help="Remote Grok MCP Gateway directory")
    parser.add_argument("--service", default="grok-mcp-gateway", help="systemd service name")
    parser.add_argument("--health-url", default="http://127.0.0.1:9996/health?deep=1")
    parser.add_argument("--no-restart", action="store_true", help="Import credentials but do not restart systemd service")
    parser.add_argument("--no-health", action="store_true", help="Skip remote health check")
    parser.add_argument(
        "--print-reauth-command",
        action="store_true",
        help="After a successful transfer, print the Hermes command that creates a separate local xAI OAuth token chain",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    remote_tmp = f"/tmp/xai-oauth-refresh-{os.getpid()}.json"

    with tempfile.NamedTemporaryFile("w", prefix="xai-oauth.", suffix=".json", delete=False) as fh:
        local_tmp = Path(fh.name)
    try:
        os.chmod(local_tmp, 0o600)
        with local_tmp.open("w", encoding="utf-8") as out:
            subprocess.run([sys.executable, str(EXPORT_SCRIPT)], check=True, text=True, stdout=out)

        run([*scp_base(args), str(local_tmp), f"{args.host}:{remote_tmp}"])

        remote_dir = shlex.quote(args.remote_dir)
        remote_tmp_q = shlex.quote(remote_tmp)
        service_q = shlex.quote(args.service)
        health_q = shlex.quote(args.health_url)
        restart_cmd = "" if args.no_restart else f"sudo systemctl restart {service_q}"
        health_cmd = "" if args.no_health else f"curl -fsS {health_q} >/dev/null"
        remote_script = f"""
set -euo pipefail
chmod 600 {remote_tmp_q}
cd {remote_dir}
python3 scripts/import_xai_oauth.py {remote_tmp_q}
rm -f {remote_tmp_q}
{restart_cmd}
if [ -n {shlex.quote(health_cmd)} ]; then
  sleep 2
  {health_cmd}
fi
"""
        run([*ssh_base(args), args.host, remote_script])
        print("Remote xAI OAuth credentials refreshed successfully.")
        if args.print_reauth_command:
            print(
                "\nRecommended next step: re-authenticate local Hermes so the desktop "
                "and proxy use separate refresh-token chains:\n"
                "cd ~/.hermes/hermes-agent && ./venv/bin/python - <<'PY'\n"
                "from types import SimpleNamespace\n"
                "from hermes_cli.auth import _login_xai_oauth, PROVIDER_REGISTRY\n"
                "args = SimpleNamespace(timeout=600, no_browser=False)\n"
                "_login_xai_oauth(args, PROVIDER_REGISTRY['xai-oauth'], force_new_login=True)\n"
                "PY"
            )
    finally:
        try:
            local_tmp.unlink()
        except FileNotFoundError:
            pass
        cleanup = f"rm -f {shlex.quote(remote_tmp)}"
        subprocess.run([*ssh_base(args), args.host, cleanup], text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
