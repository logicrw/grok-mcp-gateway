#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-${GROK_GATEWAY_BASE_URL:-http://127.0.0.1:9996}}"
MCP_URL="${BASE_URL%/}/mcp"

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 2
  fi
}

json_field() {
  python3 -c '
import json
import sys

path = sys.argv[1].split(".")
data = json.load(sys.stdin)
value = data
for key in path:
    value = value[key]
print(json.dumps(value, ensure_ascii=False))
' "$1"
}

require_tool curl
require_tool python3

echo "== /health =="
health="$(curl -sS "${BASE_URL%/}/health")"
printf '%s\n' "$health" | json_field "mcp"

echo
echo "== /health?deep=1 =="
if deep_health="$(curl -sS "${BASE_URL%/}/health?deep=1")"; then
  printf '%s\n' "$deep_health" | json_field "status"
else
  echo "deep health failed" >&2
fi

echo
echo "== MCP tools/list =="
tools="$(
  curl -fsS "$MCP_URL" \
    -H "Content-Type: application/json" \
    --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
)"
printf '%s\n' "$tools" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
names = [tool["name"] for tool in payload["result"]["tools"]]
print(json.dumps(names, ensure_ascii=False))
if "x_retrieve" not in names:
    print("x_retrieve is not exposed by tools/list", file=sys.stderr)
    sys.exit(1)
'

cat <<'TXT'

If a client still says `tool x_retrieve is not enabled for server grok_mcp_gateway`,
check that client's private MCP config for stale schema cache or disabledTools entries.
This script only checks the gateway runtime; it does not modify client configuration.
TXT
