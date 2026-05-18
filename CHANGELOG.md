# Changelog

All notable changes to this fork are documented here. Dates use `YYYY-MM-DD`.

## Unreleased

### Added

- Document the project scope explicitly so users do not confuse it with a
  general MCP router, Node.js template, Docker deployment, or official X API MCP
  replacement.

## 2026-05-18

### Added

- Rename and present the fork as Grok MCP Gateway.
- Add a resident HTTP MCP endpoint at `/mcp` with the focused `x_search` tool.
- Support non-Grok local models using X Search indirectly through MCP-capable
  clients.
- Add client configuration examples for Alma, LiteLLM, and OpenAI-compatible SDK
  usage.
- Add persistent-run documentation for macOS LaunchAgent and systemd.
- Add health, deep health, metrics, and MCP smoke-test documentation.

### Changed

- Keep the upstream Grok OAuth proxy and headless OAuth transfer flow from
  `yelixir-dev/grok-oauth-proxy`.
- Preserve compatibility with existing `GROK_PROXY_*` environment variables and
  default token-state paths.
