# ChatGPT Pro 深度咨询包：Grok MCP Gateway 原生独立 OAuth 登录方案

## 🎯 任务背景与核心目标

当前项目 `grok-mcp-gateway` 是一个为本地多个 AI Agent 客户端（Claude Code、Codex、Hermes、Zed、Alma 等）提供推文检索（`x_retrieve`）与 Grok 调用的本地常驻 MCP 服务的 Python 仓库。

当前仓库在启动时如果本地 `~/.local/state/grok-oauth-proxy/auth_state.json` 为空，**不会**再从 `~/.hermes/auth.json` 隐式 bootstrap。运行时入口是 `python main.py --login`（或 `python scripts/login_xai_oauth.py`）。从 Hermes 导入只走显式脚本 `scripts/import_xai_oauth.py`。

**现在的目标是：彻底斩断对外部应用（如 Hermes）的初次引导依赖，让网关自身具备 100% 独立的原生 PKCE OAuth 浏览器登录与认证能力。**

用户在任何一台完全空白的新机器上，只需运行：
```bash
python main.py --login
# 或 python scripts/login_xai_oauth.py
```
即可自动启动临时本地回调服务、拉起系统浏览器完成 xAI / X 账号授权，将凭据原子落盘至网关私有目录并完成开箱即用。

---

## 🛠️ 技术约束与设计规范

1. **依赖纯净性**：
   - 尽量使用 Python 3.10+ 标准库（`http.server`, `urllib`, `secrets`, `hashlib`, `webbrowser`, `asyncio`）或已有的 `httpx`。禁止引入非必要的重型第三方 OAuth 库。
2. **PKCE 授权安全标准**：
   - 采用标准 RFC 7636 PKCE 流程：
     - `code_verifier`：高熵随机字符串（43~128 字符，URL-safe base64 无填充）；
     - `code_challenge`：`BASE64URL(SHA256(code_verifier))`，`code_challenge_method=S256`；
     - `state`：防 CSRF 随机 token，回调时严格比对校验。
3. **本地回调服务器设计（Local Callback Receiver）**：
   - 本地临时 HTTP Server 监听在 `127.0.0.1` 随机未占用端口或固定备选端口范围（例如 `14555`，支持自增回退）；
   - 回调地址为 `http://127.0.0.1:{port}/callback`；
   - 接收到授权码（`code`）和 `state` 后，在浏览器中返回一个精美、现代的成功/失败 HTML 页面（提示用户“授权成功，可以关闭此窗口返回终端”）；
   - 接收成功或超时后，HTTP Server 优雅关闭并释放端口。
4. **Token 兑换与状态保存（Token Exchange & Storage）**：
   - 向 `https://auth.x.ai/oauth2/token` 发送 `POST` 请求（`grant_type=authorization_code`, `code_verifier`, `code`, `redirect_uri`, `client_id`）；
   - 成功换取 `access_token`, `refresh_token`, `expires_in`, `token_type`；
   - 从返回的 JWT 或参数中提取 `client_id`；
   - 严格调用 `token_manager.py` 已有的原子落盘与安全权限校验（POSIX `0700` 目录与 `0600` 文件权限），保存至 `~/.local/state/grok-oauth-proxy/auth_state.json`。
5. **无缝集成到 `main.py`**：
   - 支持命令行参数 `python main.py --login`（登录成功后可直接继续启动常驻服务，或通过 `--login-only` 仅登录后退出）；
   - 当检测到没有可用 Token 且是在交互式终端（TTY）启动时，友好提示用户是否直接进入 `--login` 流程。

---

## 📂 核心参考代码上下文

### 1. `token_manager.py`（现有的凭据存储与刷新逻辑）
```python
# token_manager.py 核心常量与路径
LOCAL_AUTH_PATH = Path(
    os.getenv("GROK_PROXY_AUTH_STATE", str(_STATE_HOME / "grok-oauth-proxy" / "auth_state.json"))
).expanduser()
XAI_TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"
XAI_AUTH_ENDPOINT = "https://auth.x.ai/oauth2/authorize"

# 现有的保存格式样例
# {
#   "access_token": "...",
#   "refresh_token": "...",
#   "client_id": "...",
#   "token_type": "Bearer",
#   "expires_in": 7200,
#   "token_endpoint": "https://auth.x.ai/oauth2/token",
#   "last_refresh_at": "2026-08-15T21:00:00Z"
# }
```

---

## 📋 期望交付物 (Deliverables)

请给出成熟、生产就绪、无伪代码的完整代码实现方案：

1. **`oauth_flow.py`（或 `scripts/login_xai_oauth.py`）**：
   - 包含完整的 PKCE 生成、本地临时 Callback Server、浏览器拉起、Code 换 Token、HTML 友好响应以及落盘到 `auth_state.json` 的全部实现代码。
2. **`main.py` 改造 diff / 代码**：
   - 增加 `--login` 和 `--login-only` 参数支持与启动判断。
3. **`tests/test_oauth_login.py`**：
   - 覆盖 PKCE 校验、State 防重放、Callback 请求处理与 Token 写入的单元测试（带 mock）。
