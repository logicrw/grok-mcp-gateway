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

The installer renders `services/grok-oauth-proxy.service` with your current username, home directory, Hermes auth path, repository path, and virtualenv path before copying it to `/etc/systemd/system/grok-oauth-proxy.service`.

Manual installation is still possible, but edit the template first and replace all `__SERVICE_*__` placeholders with real values for your server. Make sure `Environment=HOME=...`, `Environment=HERMES_AUTH_PATH=...`, and `Environment=PATH=...` are present; systemd services do not always inherit your interactive shell environment, and Hermes CLI is commonly installed under `~/.local/bin`.

## macOS (LaunchAgent)

Create `~/Library/LaunchAgents/dev.yelixir.grok-oauth-proxy.plist` and adjust paths as needed:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>dev.yelixir.grok-oauth-proxy</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USERNAME/grok-oauth-proxy/.venv/bin/python</string>
        <string>main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME/grok-oauth-proxy</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/.local/log/grok-oauth-proxy.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/.local/log/grok-oauth-proxy.err</string>
</dict>
</plist>
```

Then load it with:

```bash
launchctl load ~/Library/LaunchAgents/dev.yelixir.grok-oauth-proxy.plist
```
