# Grok MCP Gateway 健康报告

- **日期**: 2026-08-22
- **基线**: `main` @ `16df605`（`feat(auth): integrate native PKCE browser OAuth login and remove runtime Hermes dependency`）
- **验证命令**: `ruff check .`、`basedpyright`、`pytest -q -W error`
- **验证结果**: 基线 171 passed；本轮后 183 passed、Ruff All checks passed、BasedPyright 0 errors

本报告覆盖一次资深维护者巡检：先通读入口、配置、MCP 协议层、检索编排、OAuth、测试与 CI，再按“低风险高收益优先”直接修可验证问题。架构/协议/需拍板项一律不改，只写入第 2–4 节。

公开 MCP 工具仍只有 `x_retrieve`；`tools/list`、input/output schema、removed-tool 错误语义未改。

---

## 1. 已执行的优化清单

### 1.1 接通 `GROK_PROXY_RETRIEVE_MODEL` 到 Smart Lane

| 项 | 内容 |
| --- | --- |
| 文件 | `config.py:121-125`、`retrieve_policy.py:49`、`mcp_x_search.py:23` |
| 原因 | README / CHANGELOG / RFC 都写明 `GROK_PROXY_RETRIEVE_MODEL`（回退 `GROK_PROXY_MCP_MODEL`）是 Smart Tier 旗舰模型。实际路由用 `getattr(config, "GROK_PROXY_RETRIEVE_MODEL", "grok-4.6")`，而 `config.py` 没有该属性，Smart Lane **永远**落到 `grok-4.6`。只有 `mcp_x_search.DEFAULT_MODEL` 读环境变量，造成 tool schema 默认值和真实路由模型分叉。 |
| 改动 | 在 `config.py` 增加与文档一致的 env 解析；`get_routing_config().smart_model` 与 `mcp_x_search.DEFAULT_MODEL` 都读 `config.GROK_PROXY_RETRIEVE_MODEL`。 |
| 验证 | `tests/test_model_defaults.py` 新增 `test_retrieve_model_override_drives_smart_lane_routing`、`test_legacy_model_override_drives_smart_lane_routing`（子进程隔离环境变量）。`pytest tests/test_model_defaults.py -q -W error` 通过。全量 183 passed。 |

### 1.2 修复 BasedPyright，恢复 CI type gate

| 项 | 内容 |
| --- | --- |
| 文件 | `oauth_flow.py:300`、`oauth_flow.py:500-516` |
| 原因 | CI `.github/workflows/ci.yml` 跑 `basedpyright`。`CallbackHandler.log_message` 把基类参数 `format` 改名为 `_format`（`oauth_flow.py:300`，`reportIncompatibleMethodOverride`）；`_parse_expires_in` 对 `object` 直接 `int(value)`（`oauth_flow.py:506`，`reportArgumentType`）。巡检时 BasedPyright 报 3 errors，主分支 type job 会红。 |
| 改动 | `log_message` 参数名改回 `format`；`_parse_expires_in` 只接受 `int` / 数字字符串，`bool`、`0`、负数、float、容器一律 `OAuthLoginError`。 |
| 验证 | `basedpyright` → `0 errors, 0 warnings, 0 notes`。新增 `tests/test_oauth_login.py` 中 `test_parse_expires_in_accepts_int_and_numeric_string` 与 8 组 `test_parse_expires_in_rejects_invalid_values`。 |

### 1.3 清掉 Ruff F401

| 项 | 内容 |
| --- | --- |
| 文件 | `mcp_retrieve.py`（删除 `should_run_raw`、`reasoning_effort_for` 导入）、`tests/test_routing_v2.py:12`（删除未用的 `RoutingConfig`） |
| 原因 | `ruff check .` 在 `mcp_retrieve.py:28`、`mcp_retrieve.py:37`、`tests/test_routing_v2.py:12` 报 F401。CI quality job 会红。函数本身仍被测试或其它模块使用，只删未使用导入。 |
| 验证 | `ruff check .` → All checks passed。 |

### 1.4 导出已记录但从未暴露的 route 指标

| 项 | 内容 |
| --- | --- |
| 文件 | `retrieve_metrics.py:105-113` |
| 原因 | `record_route()` 写入 `_route_count`（`retrieve_metrics.py:13,60-62`），`mcp_retrieve.call_retrieve` 在 `mcp_retrieve.py:74,147,247,259` 调用它，但 `metrics_lines()` 从不输出。`/metrics` 看不到 lane / objective / escalation。 |
| 改动 | 增加低基数 counter `mcp_x_retrieve_route_total{lane,objective_mode,escalated}`。只导出已记录的组合，不按模型名扩基数。 |
| 验证 | `tests/test_retrieve_metrics.py:10-37` 增加 `record_route(...)` 断言。Prometheus 文本只新增系列，不改 MCP JSON。 |

### 1.5 阶段合并时按 status ID/URL 去重 `posts`

| 项 | 内容 |
| --- | --- |
| 文件 | `retrieve_payload.py:91-104`、`retrieve_payload.py:289-301` |
| 原因 | `merge_stage_payload` 对 `items` 按 URL/文本去重，对 `posts` 却 `extend`。oEmbed 路径 `retrieve_oembed._append_post`（`retrieve_oembed.py:66-88`）已经按 status ID upsert。Fast→Smart 升级时同一条推文会在 `posts` 里出现两次，而 `items` 只有一条。 |
| 改动 | 新增 `_post_key`：优先 `status:<id>`，否则 `url:`，否则 `author::text`。合并时跳过已见 key。先出现的 stage 文本保留（与 items 的“先到先得”一致）。 |
| 验证 | 新增 `tests/test_retrieve_payload.py::test_merge_stage_payload_deduplicates_posts_by_status_id`：同 status 的 smart 副本被丢弃，新 status 被追加。 |

### 1.6 修正 README LaunchAgent 指向不存在的 plist

| 项 | 内容 |
| --- | --- |
| 文件 | `README.md:122-127`、`README.zh-CN.md:122-127` |
| 原因 | 文档写 `cp services/io.logicrw.grok-mcp-gateway.plist`，仓库 `services/` 只有 `grok-mcp-gateway.service` 与 `service-examples.md`。macOS 示例实际在 `services/service-examples.md:21-74`。 |
| 改动 | 改为复制 `service-examples.md` 中的示例、替换占位路径，并用 `launchctl bootstrap` / `kickstart`（与 service-examples 一致）。 |
| 验证 | 人工对照 `services/` 目录列表与 `services/service-examples.md` 命令块。无运行时行为变化。 |

### 1.7 补 grok-4.6 reasoning 能力测试

| 项 | 内容 |
| --- | --- |
| 文件 | `tests/test_retrieve_policy.py:28-34` |
| 原因 | `DEFAULT_CAPABILITIES` 已为 `grok-4.6` / `grok-4.6-latest` 打开 reasoning（`retrieve_policy.py:93-100`），但测试名仍是 `test_reasoning_effort_is_only_enabled_for_grok_4_5`，且不断言 4.6。 |
| 改动 | 重命名并断言 `grok-4.6`、`grok-4.6-latest` 为 True，composer / custom 仍为 False。 |
| 验证 | `pytest tests/test_retrieve_policy.py -q -W error` 通过。 |

### 本轮统一验证

```text
ruff check .          → All checks passed
basedpyright          → 0 errors, 0 warnings, 0 notes
pytest -q -W error    → 183 passed in 1.41s
```

未跑真实 xAI / 浏览器 OAuth；未改公开 MCP schema。

---

## 2. 未处理问题（按严重度排序）

### P1

**P1-1 `xhigh` 写在文档里，代码路径不会发出去**

- 证据：
  - `README.md:56`、`README.zh-CN.md:56`、`docs/retrieval-architecture.md:40` 写 Smart Lane 会挂 `low/medium/high/xhigh`。
  - `retrieve_policy.py:20`：`ReasoningEffort = Literal["low", "medium", "high"]`。
  - `retrieve_policy.py:84-100`：`grok-4.6` 的 `reasoning_efforts` 不含 `xhigh`。
  - `retrieve_policy.py:166-175`：`verify_claim` 最高只到 `high`。
  - `mcp_x_search.py:186-188`：`_x_search_payload` 只转发 `{low, medium, high}`。
- 为何没改：把 `xhigh` 接到 `verify_claim` 会改变上游请求体、延迟和账单。RFC（`docs/rfc-grok-46-and-two-tier-routing.md:152`）自己也在 `high` / `xhigh` 之间犹豫。需要先用真实 grok-4.6 确认 API 接受 `xhigh`，再决定默认档位。
- 建议二选一：实现并加契约测试；或从 README/architecture 文档删掉 `xhigh`，避免调用方误以为已经生效。

**P1-2 生产路径与测试路径各有一套 Responses payload 构造器，会静默漂移**

- 证据：
  - 生产：`mcp_x_search._x_search_payload`（`mcp_x_search.py:140-188`），由 `run_search_stage` → `_call_x_search_result` 调用。
  - 测试/死代码：`retrieve_policy.build_responses_payload`（`retrieve_policy.py:434-467`），仅 `tests/test_routing_v2.py` 使用。
- 差异实例：`build_responses_payload` 按 `ModelCapabilities.structured_outputs` 决定是否挂 JSON schema；`_x_search_payload` 只在 `_structured_output` 为真时挂 schema。`build_responses_payload` 无条件 `store: False` 和 `max_turns`；`_x_search_payload` 看 `_max_turns` / `_store` / `GROK_PROXY_STORE_RESPONSES`。
- 为何没改：把生产切到 `build_responses_payload` 是编排层重构，现有测试不能证明与 xAI 线上行为等价。

**P1-3 `error_result` 丢掉真实 mode / stage**

- 证据：`mcp_retrieve.py:429-459`。无论入参 `intent`/`handles`/`sort` 是什么，错误载荷都写 `mode: "semantic_research"`、`retrieval_stages[0].name: "stable_extract"`。
- 触发：校验失败（例如超长 `model`）走 `mcp_server.handle` → `tool_error_result` → `error_result`。`tests/test_retrieve_orchestration.py:164-168` 只断言 warning 文本。
- 为何没改：错误载荷是 `x_retrieve.v1` 的一部分。改 `mode` 字段会改变客户端看到的公开 JSON。需要先定：错误响应是“尽力回显已解析元数据”，还是“固定占位”。

**P1-4 阶段合并仍不去重 `sources`**

- 证据：`retrieve_payload.py:106` 仍 `payload["sources"].extend(stage_payload["sources"])`。`items`/`posts` 已去重。同一 status URL 可在 `sources` 出现多次。
- 为何没改：本轮只把 `posts` 拉齐到与 `items`/oEmbed 相同的先到先得规则。`sources` 去重会改变 `source_extraction_status` 附近的数组长度，需要单独契约测试。

### P2

**P2-1 `HERMES_POLL_INTERVAL` 是死配置**

- 证据：`config.py:82-83` 定义并注释“How often to poll Hermes auth.json”。全仓库只有这一处赋值。token 预热用的是 `TOKEN_REFRESH_WINDOW`（`main.py:363,372`）。`token_manager.py:3-6` 已写明运行时不再依赖 Hermes。
- 处理建议：删除该 env，或在 CHANGELOG 标明 ignored。不要在没公告的情况下静默删除，以免有人以为轮询仍生效。

**P2-2 生产不用、测试还在用的遗留函数**

- `retrieve_policy.reasoning_effort_for`（`retrieve_policy.py:249-257`）：只被 `tests/test_retrieve_policy.py:14-25` 调用。真实路由用 `_smart_effort` + `_validated_effort`。
- `retrieve_payload.should_run_raw`（`retrieve_payload.py:73-75`）：`raw_decision` 的薄包装，生产走 `raw_decision`。
- 删除它们会缩短 API 面，但要连测试一起改，属于清理而非 bug。

**P2-3 `_groups` 复制了两份**

- `retrieve_payload.py` 的 `_groups` 与 `retrieve_oembed.py:101-104` 同构。抽公共函数是纯重构。

**P2-4 Quality gate 计数漏掉 target pipeline**

- `_quality_gate_counts` 只在 `_run_general_pipeline`（`mcp_retrieve.py:254`）递增。`_run_target_pipeline`（`mcp_retrieve.py:88-213`）做了 quality / smart fallback，但不记 `mcp_x_retrieve_quality_gate_total`。指标会低估 exact-target 流量。

**P2-5 `mcp_server.stdio_main` 同步读 stdin，会堵住事件循环**

- 证据：`mcp_server.py:72-88`，`for line in sys.stdin`。HTTP `/mcp` 是主路径；stdio 是备用入口。改成 `asyncio.StreamReader` 不改协议，但现有测试几乎不覆盖 stdio。

**P2-6 模块命名仍停在已删除的公开工具上**

- `mcp_x_search.py`、`mcp_posts.py`、`GROK_PROXY_MCP_X_SEARCH_CONCURRENCY`、内部 `_x_search_*` 指标前缀。公开工具只有 `x_retrieve`。重命名会动 import 与 env 名，属于破坏性清理。

**P2-7 文档/RFC 仍把 Hermes 写成核心价值**

- `docs/rfc-grok-46-and-two-tier-routing.md:28,63` 仍写“桥接本地 Hermes OAuth”。`docs/chatgpt-pro-consult-standalone-oauth.md:7` 写空 state 时会从 `~/.hermes/auth.json` bootstrap。`token_manager.read_local_state` 的测试 `tests/test_oauth_login.py:252-264` 明确禁止隐式 Hermes bootstrap。RFC 过期，容易误导后续实现。

**P2-8 CHANGELOG `Unreleased` 从项目诞生累积到现在，从未打版本**

- `pyproject.toml` / `mcp_x_search.SERVER_VERSION` 仍是 `0.1.0`。不阻塞运行，但外部无法引用稳定版本。

**P2-9 Fast 失败且剩余预算 < 35s 时，异常直接抛给客户端**

- `_run_general_pipeline`（`mcp_retrieve.py:244-252`）：Fast 超时/失败且预算不够升级时 `raise`，变成 `error_result`。Smart 路径失败则写入 warning 并继续 raw expansion。同一公开工具的失败形态不对称。改返回 `degraded` 是行为变化，需要明确错误契约。

---

## 3. 架构体检

### 3.1 分层（当前实际，不是 README 示意图）

```text
客户端 (Claude/Codex/Cursor/…)
    │  HTTP JSON-RPC POST /mcp
    ▼
main.py          FastAPI：鉴权中间件、/health、/metrics、/mcp、OpenAI 兼容反代
    │
mcp_server.py    JSON-RPC 方法分发（initialize/ping/tools/list/tools/call）
    │
mcp_x_search.py  公开工具注册、并发信号量、内部 x_search payload、指标外壳
    │
mcp_retrieve.py  4 段流水线编排（oEmbed / fast / smart / raw）
    ├── retrieve_routing.py   入参清洗、mode/target 判定
    ├── retrieve_policy.py    RetrievalPlan、质量门、升级条件
    ├── retrieve_stages.py    单阶段超时与 usage 记录
    ├── retrieve_payload.py   规范化、合并、finalize
    ├── retrieve_oembed.py    oEmbed 合并
    ├── retrieve_text_parser.py  status URL/ID 提取、非 JSON 兜底
    └── retrieve_schema.py    公开 tool/output schema、RAW_MODEL
         │
         ├── x_oembed.py      publish.twitter.com
         └── xai_responses.py api.x.ai /v1/responses
                  │
                  └── token_manager.py + oauth_flow.py
```

`mcp_posts.py` 不再是公开 MCP 工具，但仍是检索内部的参数清洗、prompt、JSON 规范化层。这是历史分层，不是清晰的 hexagon。

### 3.2 耦合度

| 边界 | 状态 | 说明 |
| --- | --- | --- |
| MCP 协议 vs 检索 | 中等、可接受 | `mcp_server` 只认识 `mcp_x_search`。`mcp_x_search` 同时做工具门面和 xAI payload，是最厚的胶水。 |
| 检索编排 vs 策略 | 中等 | `mcp_retrieve` 手写 target/general 两条流水线，策略对象 `RetrievalPlan` 没有被当成状态机驱动。 |
| 配置 | 偏散 | 超时/模型有的在 `config.py`，RAW_MODEL 在 `retrieve_schema.py`，DEFAULT_MODEL 现已归到 config。本轮修了 Smart 模型分叉。 |
| 鉴权 | 合理 | 运行时读 `LOCAL_AUTH_PATH`；Hermes 只留显式 import/export 脚本和 `load_from_hermes`。 |
| 指标 | 偏散 | `main.py` 代理计数、`mcp_x_search` 请求计数、`mcp_retrieve` quality/raw、`retrieve_metrics` stage/route。`/metrics` 是拼接出口。 |
| 测试 vs 生产 payload | **高风险** | 见 P1-2。路由测试测的不是线上构造器。 |

循环依赖：`mcp_x_search` ↔ `mcp_server`（stdio 入口互相 import）、`mcp_x_search` → `mcp_retrieve` → `retrieve_routing` → `mcp_posts`。无包结构，全是根目录模块。

### 3.3 分层是否合理

合理的地方：

- 公开面很窄：一个 MCP 工具、一份 `x_retrieve.v1`。
- 确定性 oEmbed 与模型调用分开，exact-target 可以 0 模型调用。
- OAuth 单飞刷新（`token_manager._refresh_lock`）和 MCP 并发信号量是针对多 Agent 本机场景的正确约束。
- CI 矩阵 3.10–3.13，Ruff + BasedPyright + `pytest -W error`，质量门是真的。

不合理 / 债：

1. **根目录 ~20 个 `retrieve_*` / `mcp_*` 文件**，没有 package。新代码只能继续平铺。
2. **`mcp_x_search.py` 名不副实**：公开工具是 `x_retrieve`，文件仍承担 x_search 适配。
3. **`mcp_posts.py`（756 行）是内部库却保留已删除工具的 definition/schema**。
4. **两条流水线（target vs general）复制升级/失败处理**，质量门与 raw 决策在两条路径上不一致。
5. **文档领先实现**：`xhigh`、LaunchAgent plist（已修）、Hermes 核心叙事。
6. **OpenAI 兼容反代**（`main.py` catch-all）和 MCP 检索共享进程与 token。反代是额外攻击面，测试主要在 `tests/test_security.py`。

### 3.4 技术债盘点

| 债 | 位置 | 成本 | 触发条件 |
| --- | --- | --- | --- |
| 双 payload 构造器 | `mcp_x_search._x_search_payload` vs `retrieve_policy.build_responses_payload` | 中 | 下次改 `store` / schema / reasoning 时只改一边 |
| 遗留 Hermes 运行时 API | `token_manager.load_from_hermes`、`rehydrate_from_hermes` | 中 | 新维护者按 RFC 接回隐式 bootstrap |
| 死 env `HERMES_POLL_INTERVAL` | `config.py:83` | 低 | 运维以为还能调轮询 |
| `reasoning_effort_for` 假路由 | `retrieve_policy.py:249` | 低 | 测试绿、生产走另一条函数 |
| 无录制的上游 fixture | `tests/` 全是 mock `ResponsesResult` | 高 | 模型/schema 变更只能靠生产发现 |
| CHANGELOG Unreleased 无版本 | `CHANGELOG.md`、`pyproject.toml:3` | 低 | 无法 pin 行为 |
| stdio 同步读取 | `mcp_server.py:72` | 低 | 有人真用 stdio MCP |

---

## 4. 演化路线图

下列条目按建议执行顺序排列。每条都足以让另一个 Agent 在无本会话上下文的情况下开工。不要一次做完；每条单独 PR，并保持 `x_retrieve.v1` 字段兼容。

### 方向 A — 统一 Responses payload 构造（推荐下一件）

- **动机**: 生产用 `_x_search_payload`，路由单测用 `build_responses_payload`。下一次改 `store`、JSON schema 或 reasoning 白名单时，测试会绿、线上会错。
- **方案概要**:
  1. 读 `mcp_x_search._x_search_payload`（`mcp_x_search.py:140-188`）和 `retrieve_policy.build_responses_payload`（`retrieve_policy.py:434-467`），列出字段并集：`model`、`input`、`tools`、`temperature`、`max_turns`、`store`、`text.format`、`reasoning.effort`。
  2. 选定**一个**函数作为唯一构造器。建议保留 `build_responses_payload` 为纯函数（plan + tool + query → dict），让 `_x_search_payload` 变成：清洗参数 → `resolve` 出 plan 等价字段 → 调用前者。不要在 `mcp_retrieve` 再手写 `_structured_output`。
  3. 把 `tests/test_routing_v2.py::test_fast_payload_never_contains_reasoning` 和 `tests/test_retrieve_orchestration.py::test_x_search_payload_only_adds_explicit_reasoning_effort` 改成打**同一函数**。
  4. 禁止 `mcp_x_search` 在构造器之外再改 payload 键。
- **验收**:
  - `pytest -q -W error` 全绿。
  - 对 `latest_by_handle` / `verify_claim` / `raw_expansion` 三个 plan，生产构造结果与 `build_responses_payload` 的 JSON 完全一致（可写 `assert actual == expected`）。
  - 不改 `x_retrieve` inputSchema。
- **工作量**: 0.5–1 人日。
- **风险**: 中。漏搬 `_store` 或 schema 开关会让 Fast 模型收到 `reasoning`（xAI 400）或 Smart 丢掉 structured output。用现有 fake search 捕获实际 POST body，不要只测 plan 对象。

### 方向 B — 给 `xhigh` 做一次明确决策（实现或删文档）

- **动机**: README 承诺 `xhigh`，代码最高 `high`。调用方无法从 schema 看出真实档位。
- **方案概要**:
  1. 用真实账号对 `grok-4.6` 打一次 Responses：`reasoning.effort=xhigh` + `x_search` + 本仓库 JSON schema。记录 HTTP 状态和 `usage.output_tokens_details.reasoning_tokens`。把原始响应放进 `outputs/` 或内部笔记，**不要**把 token 写进仓库。
  2. 若 API 拒绝 `xhigh`：从 `README.md:56`、`README.zh-CN.md:56`、`docs/retrieval-architecture.md:40` 删除 `xhigh`，CHANGELOG 写明“文档与实现对齐，未启用 xhigh”。
  3. 若 API 接受：
     - `retrieve_policy.py`：`ReasoningEffort` 加入 `xhigh`；`DEFAULT_CAPABILITIES["grok-4.6*"].reasoning_efforts` 加入 `xhigh`；`_smart_effort("claim_verification")` 是否用 `xhigh` **单独作为产品决定**（建议默认仍 `high`，用 env `GROK_PROXY_VERIFY_REASONING_EFFORT` 打开 `xhigh`，避免账单突变）。
     - `mcp_x_search.py:186` 的集合加入 `xhigh`。
     - 测试：`model_supports_reasoning_effort`、`resolve_plan(verify_claim)`、`_x_search_payload` 转发。
- **验收**: 文档集合与代码集合相等。`grep -n xhigh README.md retrieve_policy.py mcp_x_search.py` 能对上。
- **工作量**: 探活 0.5 人日；若实现再 0.5 人日。
- **风险**: 高（账单/延迟）如果默认改成 `xhigh`。低如果只删文档或放 env 开关且默认不变。

### 方向 C — 结束 Hermes 叙事，只留显式迁移入口

- **动机**: 运行时已是 native PKCE（`oauth_flow.py` + `token_manager.LOCAL_AUTH_PATH`）。RFC、consult 文档、死 env 仍把 Hermes 当主路径，后续 Agent 很容易把隐式 bootstrap 接回来，而这正是 `test_read_local_state_never_implicitly_bootstraps_from_hermes` 禁止的。
- **方案概要**:
  1. 删除 `config.HERMES_POLL_INTERVAL`（`config.py:82-83`）。grep 确认无引用。
  2. 在 `docs/rfc-grok-46-and-two-tier-routing.md` 顶部加 “Historical / superseded by native PKCE in 16df605”，或把 Hermes 段改成“可选迁移源，见 `scripts/import_xai_oauth.py`”。
  3. 更新 `docs/chatgpt-pro-consult-standalone-oauth.md:7`：空 state 时要求 `python main.py --login`，不再写自动读 `~/.hermes/auth.json`。
  4. **不要**删除 `token_manager.load_from_hermes` / `scripts/import_xai_oauth.py`；它们是一次性迁移。保持 `read_local_state` 不调用它们。
  5. `services/service-examples.md` 仍可保留 `HERMES_AUTH_PATH` 作为迁移机环境，但注释“仅 import 脚本使用，守护进程不读”。
- **验收**: `rg HERMES_POLL_INTERVAL` 为空；`read_local_state` 测试仍禁止隐式 bootstrap；`python scripts/import_xai_oauth.py --help` 仍可用。
- **工作量**: 0.5 人日。
- **风险**: 低。唯一风险是有人仍靠未文档化的 poll interval（代码里本来就没生效）。

### 方向 D — 把 `retrieve_*` 收成包，拆开 `mcp_x_search` 门面

- **动机**: 根目录模块 + `mcp_x_search` 身兼工具注册、信号量、payload、指标，后续改并发或 schema 都要碰整文件。不是用户可见功能，但是方向 A 之后的结构偿还。
- **方案概要**:
  1. 新建包 `retrieve/`，移动：`retrieve_policy.py`、`retrieve_routing.py`、`retrieve_stages.py`、`retrieve_payload.py`、`retrieve_oembed.py`、`retrieve_text_parser.py`、`retrieve_schema.py`、`retrieve_metrics.py`、`mcp_retrieve.py`。根目录留 `from retrieve.xxx import *` 兼容一层 **一个版本**，或直接改测试 import（本仓库无外部 Python API，可一次切）。
  2. 把 `mcp_x_search.py` 拆成：
     - `mcp_tools.py`：`tool_definitions` / `call_tool` / allowlist / removed tools
     - `xai_x_search.py`：`_x_search_payload` + semaphore（方向 A 之后应很薄）
  3. `mcp_posts.py` 继续作为内部 helper，不要重新公开 `x_posts`。
  4. 不要改 JSON-RPC method 名、tool 名、env 名。`GROK_PROXY_MCP_X_SEARCH_CONCURRENCY` 可留别名。
- **验收**: `pytest -q -W error`、`basedpyright`、`ruff check .`。`curl /health` 的 `enabled_tools` 仍是 `["x_retrieve"]`。
- **工作量**: 1–2 人日（含 import 修复）。
- **风险**: 中。漏改测试 `sys.path` / 动态 import。不要顺便改公开 schema。

### 方向 E — 补一条“录制回放”的上游契约测试

- **动机**: 183 个测试全是 mock。xAI 改 `x_search` tool 形状、JSON schema `strict`、或 `cost_in_usd_ticks` 字段时，CI 不会红。质量门和 raw parser 是对真实脏输出最敏感的部分。
- **方案概要**:
  1. 新增 `tests/fixtures/xai/`，放入**脱敏**的 Responses JSON（无 Authorization、无 JWT、无 email）。来源：一次真实 `x_retrieve` 把 `xai_responses.post` 的 `response.json()` 经 `error_sanitizer.sanitize_text` 后保存。
  2. 至少三份：Fast `latest_by_handle` 成功；Smart `verify_claim` 带 citations；Composer raw 非 JSON 含 status URL。
  3. 测试：`xai_responses` 解析 text/citations/usage；`assemble_payload` + `finalize_payload` 的 `retrieval_status`；`parse_raw_posts_from_text` 从 raw fixture 抽出 URL。
  4. 不要在 CI 打真实 API。fixture 用手工 refresh，在 `docs/retrieval-architecture.md` 写刷新步骤。
- **验收**: 无网络 `pytest` 仍全绿；故意改 fixture 缺 `posts` 时对应测试失败。
- **工作量**: 1 人日（含一次真实采样）。
- **风险**: 低（测试债）。注意 fixture 隐私：跑 `scan-secrets` 后再提交。

---

## 附录：本轮刻意没做的事

- 没有把 `xhigh` 接到线上请求。
- 没有删除 Hermes import/export 脚本或 `load_from_hermes`。
- 没有改 `x_retrieve` input/output schema、tool 名、协议版本 `2025-06-18`。
- 没有升级依赖（`requirements.txt` 已钉死，无本次必需的漏洞修复）。
- 没有引入新依赖。
- 没有把根目录模块打成 package（方向 D）。
- 没有对真实 xAI 或本机 9996 端口做集成探测。

---

## 第二阶段记录

- **日期**: 2026-08-22（紧接第一阶段 `d3a4c96`）
- **红线**: 不改 MCP 协议、`x_retrieve` input/output schema、tool 名、removed-tool 错误码；公开 JSON 只修正错误数据或做纯加法（新 Prometheus 系列、request 多字段），不删字段。
- **终点 commits**: `83c3854`、`a5a8cfa`（本文件为随后的 docs commit）
- **验证**: `ruff check .` 通过；`basedpyright` 0 errors；`pytest -q -W error` **189 passed**（第一阶段结束 183）

### 判断逻辑：为什么做这两件、不做别的

额度最后一班，目标是「完整 2 件」而不是 5 个半成品。从第一阶段报告里按 **生产正确性 × 可验证性 × 不碰协议** 排序：

| 候选 | 决定 | 理由 |
| --- | --- | --- |
| 方向 A 统一 Responses payload | **做，第一件** | P1-2 是最高杠杆的静默漂移：下次改 `store` / schema / reasoning 时，路由测试会绿、线上 `_x_search_payload` 不会。纯函数替换，不改 MCP schema。报告里已写验收条款。 |
| P1-4 `sources` 去重 + P1-3 `error_result` 元数据 | **做，第二件** | 与第一阶段已修的 `posts` 去重是同一合并契约；error payload 在校验失败/上游失败时对 Agent 撒谎 `mode=semantic_research`。都是 payload 正确性，测试可钉死，shape 不删字段。 |
| 方向 B `xhigh` | **放弃** | 没有安全的真实 grok-4.6 探活（会打账单、需要线上 token）。默认改 `xhigh` 是产品决策。只删 README 词是文档活，优先级低于会在下次改 payload 时咬人的双构造器。 |
| 方向 C Hermes 叙事 | **放弃** | `HERMES_POLL_INTERVAL` 本来就没生效；import 脚本必须保留。纯文档/死 env 清理，不修运行时。 |
| 方向 D 收包 | **放弃** | 1–2 人日重构，半途会留下双路径 import。A 刚把构造器收口，此时搬家是给 diff 添噪音。 |
| 方向 E 录制 fixture | **放弃** | 需要一次真实 `x_retrieve` 采样和隐私审查，本班岗没有对外打 xAI。 |
| P2 quality gate 漏记 target pipeline | **未做** | 只影响 `/metrics` 计数，不改检索结果。第二件已经覆盖两条 payload 契约，不再加第三条半相关改动。 |

### 实际完成

#### 1. 唯一 xAI Responses 构造器（`83c3854`）

- 新增 `retrieve_policy.build_xai_responses_payload`：`model` / `tools` / `max_turns` / `store` / JSON schema / `reasoning.effort`。
- `mcp_x_search._x_search_payload` 只做参数清洗，然后调用该构造器。
- `build_responses_payload(plan=...)` 变成 RetrievalPlan 包装：`store` 走 `resolve_store_flag()`（默认 `False`），schema 仍按模型 `structured_outputs`。
- 测试：
  - Fast `latest_by_handle`：生产 body == plan builder，且无 `reasoning`。
  - Smart `verify_claim`：生产 body == plan builder，且 `reasoning.effort=high`。
  - Raw：无 `text` / `reasoning` / `max_turns`，即使传入 `reasoning_effort="high"` 也被能力表丢掉。
  - `_call_x_search_result` 实际 POST 体与 plan builder 一致（monkeypatch `xai_responses.post`）。

**行为对齐说明（刻意保持生产语义）**：structured output 仍由调用方 `_structured_output` 决定，而不是模型能力表。Raw 路径继续不挂 schema。reasoning 改为「effort 必须落在该模型 `DEFAULT_CAPABILITIES` 集合里」，对当前 4.5/4.6（`low/medium/high`）与 non-reasoning / composer 的结果与旧 `{low,medium,high} AND model_supports_reasoning_effort` 相同。

#### 2. 合并 `sources` 去重 + 错误载荷 mode（`a5a8cfa`）

- `merge_stage_payload` 对 `sources` 按 status ID（url 或 title）或裸 URL 先到先得，与 `items`/`posts` 一致。
- `error_result` 先尝试 `build_retrieve_search_arguments`；成功则 `mode` 和 `request` 用真实元数据（含 `handles`/`count`/`target_status_ids` 等，对旧客户端是加法）。解析失败（超长 model、缺 query/handles）仍回退 `semantic_research`。
- `retrieval_stages[0].name` **仍为** `stable_extract`：这是错误载荷里的历史字符串，改名会动公开 JSON 值，本班岗不碰。
- 测试：同源 status 的 smart source 被丢、新 status 保留；`handles+sort=latest` 的 `error_result` 为 `latest_by_handle`；超长 model 的 MCP 错误仍为 `semantic_research`。

### 中途放弃了什么

- **没有**把 `xhigh` 写入能力表或 README 对齐（方向 B）。缺线上证据，默认档位不能猜。
- **没有**删 `HERMES_POLL_INTERVAL` 或改 RFC（方向 C）。
- **没有**开始 `retrieve/` 包迁移（方向 D）。
- **没有**加 xAI fixture（方向 E）。
- **没有**给 target pipeline 补 `mcp_x_retrieve_quality_gate_total`（P2-4）。
- 做方向 A 时一度考虑让 schema 也跟 `caps.structured_outputs` 走：那会改变「调用方显式 `_structured_output=True`」的生产语义，立即止损，schema 继续由 flag 控制。

### 给下一任的交接

1. **先读这三份，不要从 RFC 开工**：`HEALTH-REPORT.md`（本文件）→ `docs/retrieval-architecture.md` → 代码。`docs/rfc-grok-46-and-two-tier-routing.md` 仍把 Hermes 当核心、仍写 `xhigh` 已启用，**不是**运行时事实。
2. **payload 入口现在只有一个**：改 xAI 请求体只动 `retrieve_policy.build_xai_responses_payload`。若 Fast 测绿、线上 400，先看 `_x_search_payload` 是否又手写了字段。
3. **下一件仍建议方向 B 的「探活或删文档」**，不要默认切 `xhigh`。没有 200 + usage 证据就只改 README/architecture，把 `xhigh` 从「会挂载」改成「未发送」。`grep -n xhigh README.md README.zh-CN.md docs/retrieval-architecture.md retrieve_policy.py mcp_x_search.py`。
4. **方向 C 可以当小清扫 PR**：删 `config.py:82-83` 的 `HERMES_POLL_INTERVAL`；RFC 顶部标 superseded；consult 文档改成 `python main.py --login`。不要动 `load_from_hermes` / `scripts/import_xai_oauth.py`。`tests/test_oauth_login.py::test_read_local_state_never_implicitly_bootstraps_from_hermes` 是回归锚。
5. **方向 D 只能在 A 稳定之后做**，一次 PR 搬完 `retrieve_*.py`，不要兼容层留一版。公开 MCP 名称和环境变量名不要一起改。
6. **方向 E 仍然缺**：全套 189 个测试都是 mock。冷启动一条脱敏 fixture 比再重构模块更值钱，但必须先有一次真实采样并跑 secrets scan。
7. **已知仍撒谎/仍不对称的点**（未修，有意留下）：
   - 错误载荷 stage 名仍是 `stable_extract`（`mcp_retrieve.py` `error_result`）。
   - Fast 失败且预算 < 35s 直接抛错；Smart 失败写 warning 再走 raw（P2-9）。
   - Target pipeline 不算 quality-gate 指标（P2-4）。
8. **验证口令**：`ruff check . && basedpyright && pytest -q -W error`。不要只跑单文件就宣称完成。

---

## 第三阶段记录

- **日期**: 2026-08-23
- **选题**: 方向 B — 给 `xhigh` 做明确决策（无探活 → 删用户文档承诺，不改默认档位）
- **commit**: `b280906`
- **验证**: `ruff check .` 通过；`basedpyright` 0 errors；`pytest -q -W error` **190 passed**（第二阶段结束 189）

### 为什么选它

第二阶段交接第 3 条写明「下一件仍建议方向 B 的探活或删文档」。没有安全的 grok-4.6 线上探活，不能把 `verify_claim` 默认改成 `xhigh`（账单/延迟）。用户文档继续写「会挂载 xhigh」比死 env 更会误导调用方。

### 实际完成

- `README.md:56`、`README.zh-CN.md:56`、`docs/retrieval-architecture.md:40` 改为 Smart Lane 只挂 `low` / `medium` / `high`，并写明 **不发送** `xhigh`。
- `tests/test_retrieve_policy.py::test_xhigh_is_not_sent_on_grok_46_or_verify_claim`：
  - `build_xai_responses_payload(..., reasoning_effort="xhigh")` 无 `reasoning` 键；
  - `resolve_plan(verify_claim)` 仍是 `high`；
  - `mcp_x_search._x_search_payload(_reasoning_effort="xhigh")` 同样丢掉。
- `retrieve_policy.py` / `mcp_x_search.py` 能力表未加入 `xhigh`（刻意）。
- RFC 正文里的 `xhigh` 表格留到第四阶段用 historical banner 覆盖，本阶段不改写 RFC 全篇。

### 未做

- 没有对真实 Responses API 打 `effort=xhigh`。
- 没有加 `GROK_PROXY_VERIFY_REASONING_EFFORT` 开关。

---

## 第四阶段记录

- **日期**: 2026-08-23
- **选题**: 方向 C — 结束 Hermes 运行时叙事，只留显式迁移入口
- **commit**: `e765717`
- **验证**: `ruff check .` 通过；`basedpyright` 0 errors；`pytest -q -W error` **191 passed**；`python scripts/import_xai_oauth.py --help` 可用；`rg HERMES_POLL_INTERVAL` 在运行时源码中为空

### 为什么选它

交接第 4 条：小清扫 PR，防止后续 Agent 按过期 RFC 把隐式 Hermes bootstrap 接回来。`test_read_local_state_never_implicitly_bootstraps_from_hermes` 已经禁止该行为，文档却仍在教人相反的事实。

### 实际完成

- 删除 `config.HERMES_POLL_INTERVAL` 和 `.env.example` 中的对应行。新增 `test_runtime_config_does_not_expose_hermes_poll_interval`。
- RFC 顶部加 **Status (2026-08-23): Historical**，写明 native PKCE、`read_local_state` 不读 Hermes、`xhigh` 未发送。
- `docs/chatgpt-pro-consult-standalone-oauth.md`：空 state 要求 `--login`，不再写自动读 `~/.hermes/auth.json`。
- `services/service-examples.md`：`HERMES_AUTH_PATH` 仅 import/export 脚本使用。
- `token_manager.init_local_state` 文档改为「显式一次性导入」，并写明启动路径走 `read_local_state()`。**函数体未删**，测试 `tests/test_security.py` 仍覆盖它。
- **未删** `load_from_hermes`、`rehydrate_from_hermes`、`scripts/import_xai_oauth.py`、`scripts/export_xai_oauth.py`。

### 给下一任（第三/四阶段之后）

1. 用户文档与代码在 `xhigh` 上已对齐。若以后要启用，必须先有真实 200 + usage 证据，再用 env 打开，**不要**默认切 `xhigh`。
2. RFC 只当历史包。实现前读 `docs/retrieval-architecture.md` 和本报告。
3. 方向 D（`retrieve/` 收包）和方向 E（脱敏 xAI fixture）仍未做。E 仍然更值钱，但需要一次真实采样。
4. 仍未修：错误载荷 stage 名 `stable_extract`；Fast 预算不足直接抛错 vs Smart 降级；target pipeline 不计 quality-gate 指标。
5. 验证口令不变：`ruff check . && basedpyright && pytest -q -W error`。
