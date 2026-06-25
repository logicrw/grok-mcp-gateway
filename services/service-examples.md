# Persistent Service Examples

## Linux (systemd)

Recommended path from the repository root:

```bash
./install.sh --enable-service
```

For headless install plus service enablement in one step:

```bash
./install.sh --headless --enable-service
```

The installer renders `services/grok-mcp-gateway.service` with your current username, home directory, Hermes auth path, repository path, and virtualenv path before copying it to `/etc/systemd/system/grok-mcp-gateway.service`.

Manual installation is still possible, but edit the template first and replace all `__SERVICE_*__` placeholders with real values for your server. Make sure `Environment=HOME=...`, `Environment=HERMES_AUTH_PATH=...`, and `Environment=PATH=...` are present; systemd services do not always inherit your interactive shell environment, and Hermes CLI is commonly installed under `~/.local/bin`.

## macOS (LaunchAgent)

Create `~/Library/LaunchAgents/io.logicrw.grok-mcp-gateway.plist` and adjust paths as needed:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.logicrw.grok-mcp-gateway</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USERNAME/grok-mcp-gateway/.venv/bin/python</string>
        <string>main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME/grok-mcp-gateway</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>/Users/YOUR_USERNAME</string>
        <key>HERMES_AUTH_PATH</key>
        <string>/Users/YOUR_USERNAME/.hermes/auth.json</string>
        <key>PATH</key>
        <string>/Users/YOUR_USERNAME/grok-mcp-gateway/.venv/bin:/Users/YOUR_USERNAME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>PROXY_HOST</key>
        <string>127.0.0.1</string>
        <key>PROXY_PORT</key>
        <string>9996</string>
        <key>GROK_GATEWAY_MCP_TOOL_ALLOWLIST</key>
        <string>x_retrieve</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/.local/log/grok-mcp-gateway.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/.local/log/grok-mcp-gateway.err</string>
</dict>
</plist>
```

Then load it with:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.logicrw.grok-mcp-gateway.plist
launchctl kickstart -k gui/$(id -u)/io.logicrw.grok-mcp-gateway
```

After editing the plist, restart it with `launchctl bootout` followed by
`launchctl bootstrap`, or use `kickstart -k` if the label is already loaded.
