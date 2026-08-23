# x-research-skill

Research X/Twitter discourse from an agent.

## Paths

Default path: local Grok MCP Gateway.

- MCP server name: `grok_mcp_gateway`
- Default endpoint: `http://127.0.0.1:9996/mcp`
- Tool: `x_retrieve`

No alternate X retrieval path is exposed by this skill. Do not look for
separate credentials or another local retrieval path.

## Setup

For the MCP path, install and run [Grok MCP Gateway](https://github.com/logicrw/grok-mcp-gateway) separately, then expose it to your agent as `grok_mcp_gateway`.

If the MCP tool is not enabled, fix the client MCP configuration instead of
falling back to another X retrieval path.

## Security

Do not add separate X credentials to this skill. Treat X discourse as signal,
not primary evidence; verify linked docs, repos, papers, announcements,
filings, or screenshots before presenting factual claims.
