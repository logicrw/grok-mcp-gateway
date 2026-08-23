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

---

## 第五阶段记录

- **日期**: 2026-08-23
- **选题**: 方向 D — `retrieve/` 包收拢 + MCP 门面拆解
- **commit**: `a9792f4`
- **验证**: `ruff check .` 通过；`basedpyright` 0 errors；`pytest -q -W error` **191 passed**（断言期望值未改）

### 为什么选它

第一阶段路线图把 D 放在 A 之后。payload 构造器已收口，再搬家不会一边改 JSON 一边改路径。根目录 `retrieve_*.py` + `mcp_x_search.py` 身兼注册/信号量/payload，后续改并发只能碰整文件。

### 实际完成

- 新建 `retrieve/`：`policy.py`、`routing.py`、`stages.py`、`payload.py`、`oembed.py`、`text_parser.py`、`schema.py`、`metrics.py`、`pipeline.py`（原 `mcp_retrieve.py`）。
- `mcp_tools.py`：`tool_definitions` / `call_tool` / allowlist / removed tools。`main.py` 与 `mcp_server.py` 改从这里进。
- `retrieve/x_search.py`：`_x_search_payload`、信号量、`GROK_PROXY_MCP_X_SEARCH_CONCURRENCY` 指标。
- `mcp_x_search.py` 保留为兼容门面（stdio 入口、测试 monkeypatch `mcp_x_search._call_x_search_result` 与 `mcp_x_search.mcp_retrieve`）。`mcp_tools._search_caller()` 优先用该模块上的 `_call_x_search_result`，所以旧测试不用改断言。
- BasedPyright `include` 加上 `retrieve/`；tests 环境补 `reportOperatorIssue=none`（`__getattr__` 转发计数器）。

### 未做

- 没有删 `mcp_x_search.py`。`python mcp_x_search.py` 与大量测试仍依赖这个名字。
- 没有改 env 名。

---

## 第六阶段记录

- **日期**: 2026-08-23
- **选题**: P2-4 / P2-5 / P2-2 / P2-3 扫尾 + 版本 `0.2.0`
- **commit**: `6d3b763`
- **验证**: `ruff check .` 通过；`basedpyright` 0 errors；`pytest -q -W error` **189 passed**（删除 5 条只测死函数 `reasoning_effort_for` 的 parametrize，新增 3 条 stdio/quality-gate）

### 实际完成

- **P2-4**: `_record_quality()`。exact-only 在 oEmbed 全中或 fallback 后各记一次；seed-then-research 在 smart extract 后记一次。测试 `test_target_pipeline_records_quality_gate_decision`。
- **P2-5**: `mcp_server.stdio_main` 用 `asyncio.StreamReader`（可注入 reader/writer）。`tests/test_mcp_server_stdio.py` 覆盖 ping、initialized 通知、JSON 解析错误。
- **P2-2**: 删除 `retrieve.policy.reasoning_effort_for` 与 `retrieve.payload.should_run_raw`。
- **P2-3**: `retrieve.oembed` 改为使用 `retrieve.payload._groups`。
- **0.2.0**: `pyproject.toml`、`mcp_tools.SERVER_VERSION`、反代 User-Agent、`x_oembed.USER_AGENT`。`mcp_posts.TOOL_VERSION` 仍为 `0.1.0`（内部 `x_posts.v1` 契约字段，不是包版本）。`CHANGELOG.md` 将此前 Unreleased 归档为 `## 0.2.0 - 2026-08-23`。

### 给下一任

1. 新检索代码只从 `retrieve.*` 和 `mcp_tools` 进。不要在根目录再铺 `retrieve_*.py`。
2. 仍缺方向 E（脱敏 xAI fixture）。全套测试仍是 mock。
3. 仍未改：错误载荷 stage 名 `stable_extract`；Fast 预算不足直接抛错 vs Smart 降级（P2-9）。改这些会动公开 JSON/错误形态。
4. `mcp_x_search.py` 可以在下一个大版本删，但要先把测试 monkeypatch 迁到 `retrieve.x_search`。
5. 验证口令：`ruff check . && basedpyright && pytest -q -W error`。

---

## 第七阶段记录（终局收官）

- **日期**: 2026-08-23
- **前提**: 网关仅个人及专属 Agent 团队自用，不再为未知第三方保留历史 JSON 别名。
- **commit**: `53d4e6b`（stage 名 / Fast 降级 / fixture）；本轮续作见后续 commit
- **验证**: `ruff check .` 通过；`basedpyright` 0 errors；`pytest -q -W error` **197 passed**

### 实际完成

1. **干掉 `stable_extract` 假名字**
   - `error_result`：参数解析失败 → `validation`；能解析则按 `resolve_plan` 回显 `fast_extract` / `smart_extract` / `custom_extract` / `target_fallback` / `smart_extract`（seed）。
   - 超长 `model` 不再在 `tool_error_result` 里先截断再解析（否则会误判成 `custom_extract`）；stage 的 `model` 标签仍截到 128 字符。
   - 一般流水线显式模型 lane 用 `custom_extract`，不再回落到 `stable_extract`。
   - `_failed_stage_payload` 本来就吃调用方传入的 `stage_name`，未再硬编码。

2. **P2-9 Fast 失败优雅降级**
   - Fast 抛错时不再 `raise` 成 MCP `isError`。
   - 预算够 → 仍升 Smart，再 raw；预算不够 → 跳过 Smart，仍走 `_maybe_run_raw_expansion`。
   - 测试：抬高 Smart 升级门槛后，Fast 抛错 + raw 救回一条推文 → `isError=false`、`retrieval_status=degraded`。

3. **方向 E fixture 回放**
   - `tests/fixtures/xai/fast_latest.json`、`smart_verify.json`、`raw_non_json.json`：Responses 形状、无 Authorization/JWT/email。
   - `tests/test_xai_fixtures.py`：生产提取器组 `ResponsesResult`，再打 `assemble_payload` / `finalize_payload` / `parse_raw_posts_from_text`。
   - `docs/retrieval-architecture.md` 增加 Fixture refresh 表。不在 CI 打真实 API。

4. **开放 grok-4.6 `xhigh`，默认档位保持经济**
   - `ReasoningEffort` 含 `xhigh`；`DEFAULT_CAPABILITIES` 仅 `grok-4.6` / `grok-4.6-latest` 接受。
   - 显式 `_reasoning_effort=xhigh` 经 `build_xai_responses_payload` 透传；grok-4.5 仍丢弃。
   - 默认：日常 Smart `medium`，`verify_claim` `high`，不默认 `xhigh`。

5. **自愈登录**
   - `token_manager.login_command()` 输出 `{sys.executable} {abs/main.py} --login`。
   - `AuthRequiredError` 前缀 `AUTH_REQUIRED:`，正文含绝对路径命令和「授权后重试」中英指引。
   - `mcp_tools.tool_definitions()` 描述追加同一命令；`error_result` 在 AUTH_REQUIRED 时写入 `auth_login_command`。

### 仍可后续做、但不挡自用收官

- `mcp_x_search.py` 兼容门面仍在，测试 monkeypatch 还走这个名字。
- 指标测试仍可以主动 `record_stage(stage="stable_extract")`，那是标签字符串，不是流水线产出。
- 没有真实 xAI 采样刷新 fixture；三份是按线上形状手写的脱敏样例。
- 验证：`pytest -q -W error` **197 passed**。

## 第八阶段记录（外部架构审计采纳与修复）

- **日期**: 2026-08-23
- **输入**: 外部资深架构师（ChatGPT Pro）对抗性审计报告两份（P0×1 / P1×8 / P2×7），对照 `main` HEAD（197 tests green）逐条核验。
- **验证**: `ruff check .` 通过；`basedpyright` 0 errors；`pytest -q -W error` **234 passed**（197 原有 + 37 新增复现用例，`tests/test_audit_phase8.py`）。

### 逐条核验判定（Triaging）

| 问题 | 判定 | 依据（当前源码） |
|---|---|---|
| P0-1 DNS-rebinding | **Confirmed** | `_validate_startup_security` 仅豁免 loopback 认证；无 Host/Origin 校验；catchall 剥离来路凭证后注入网关 OAuth 转发 api.x.ai |
| P1-1 跨进程刷新覆盖 | **Confirmed** | `_refresh_lock` 为进程内 `asyncio.Lock`；失败路径把 stale 全量快照写回磁盘 |
| P1-2 取消丢失轮换 token | **Confirmed** | `await asyncio.to_thread(_refresh_sync)` 的等待者被取消后线程结果被丢弃，`_save_json` 不再执行 |
| P1-3 seed 研究阶段 0 秒预算 | **Confirmed** | `resolve_plan` 对 target 策略返回 `stage_timeout_seconds=0.0`、`max_turns=0`；`run_search_stage` 在 `timeout<=0` 时直接 raise，`seed_then_research` 的 Smart 研究阶段从未真正执行 |
| P1-4 finalize 误删旁证 | **Confirmed** | `finalize_payload` 对所有 `target_status_ids` 应用 `_retain_exact_targets`，不区分 exact_only / seed_then_research 两种语义 |
| P1-5 排队时间计入阶段超时 | **Confirmed** | 信号量在 `search()` 内部获取，`asyncio.wait_for` 同时包住排队+执行；过载被误判为质量失败并触发升级放大 |
| P1-6 invalid_grant 无自愈 | **Confirmed** | `_refresh_sync` 只抛 `RuntimeError("Token refresh failed (400)")`；`error_result` 仅识别字符串 `AUTH_REQUIRED`，登录命令不透传；且超时/5xx 也被误标 `reauth_required=True` |
| P1-7 坏帧杀死 stdio 进程 | **Confirmed** | `readline()`/`decode()` 在 JSON try 边界之外；UnicodeDecodeError / LimitOverrunError→ValueError 逃逸出 `stdio_main` |
| P1-8 误删客户端工具调用 + CRLF 无界缓冲 | **Confirmed（修复方案修订）** | 按通用 `type=="custom_tool_call"` 删除（fixtures 证明内部名为 `x_keyword_search`，可精确归因）；SSE 只分割 `"\n\n"` |
| P2-1 RuntimeError 同步重跑 | **Confirmed** | `_load_json/_save_json` 捕获所有 RuntimeError 后在事件循环内重跑安全检查/写盘 |
| P2-2 无版本 CAS | **Confirmed** | 并入 P1-1 一并修复 |
| P2-3 JSON-RPC 校验不全 | **Confirmed** | 不校验 `jsonrpc=="2.0"`；无 id 的非 initialized 消息也返回 `id:null` 响应；非对象 JSON 映射 -32603 |
| P2-4 effort 静默省略 | **部分确认（降级）** | `reasoning_effort` 不是 `x_retrieve` 的用户参数（`RETRIEVE_ARGUMENT_KEYS`），「显式请求被静默丢弃」不可达；真实面是自定义模型 auto effort 静默省略 |
| P2-5 参数化 URL 去重不足 | **Confirmed** | `_source_key` 用原始 URL 字符串 |
| P2-6 非对象 auth state | **部分确认** | falsy 非对象（`[]`/`null`）已被 `not data` 拦截为 AuthRequiredError；truthy 非对象（`123`/`"abc"`/`true`）会 `AttributeError` |
| P2-7 stdio 不关共享 client | **Confirmed** | `stdio_main` 无 finally 清理（FastAPI lifespan 关闭顺序是完整的） |

无「Already Fixed」或整体「Rejected」项；报告与代码事实高度一致。P1-8/P2-4/P2-6 的**触发面**经核验后收窄，修复方案相应最小化。

### 实施的修复

1. **P0-1 loopback 边界中间件**（`main.py`）：`local_boundary_middleware` 在 loopback 绑定时强制 Host ∈ {127.0.0.1, localhost, ::1}（带端口亦合法），否则 421；带浏览器 `Origin` 且不在 `GROK_PROXY_ALLOWED_ORIGINS`（新 env，默认空）则 403。非浏览器本地客户端（无 Origin）不受影响；非 loopback 绑定仍走强制 PROXY_API_KEY。
2. **P1-1/P2-2 跨进程刷新事务**（`token_manager.py`）：`refresh_access_token` 全程持有 `auth_state.json.lock` 文件锁（flock，0600）；锁内重读磁盘——他进程已刷新且未过期则直接采纳；成功写入单调 `state_version`；失败路径基于锁内最新状态合并，仅在磁盘凭据未被并发推进时落盘（杜绝 stale 快照回滚 R1）。
3. **P1-2 刷新事务免疫取消**：刷新改为网关持有的共享 task（`_refresh_task` + `add_done_callback` 消费异常）；调用方 `await asyncio.shield(task)`——取消只释放进程内锁，刷新+落盘事务继续；后续调用者加入同一 in-flight task 而非重复消费已轮换的 refresh token。`_refresh_lock` 改为 loop-aware（同 `xai_responses` 惯例），修复嵌入式/多次 `asyncio.run` 的跨循环 Lock 报错。
4. **P1-3 seed 研究阶段预算**（`retrieve/pipeline.py`）：研究阶段 model/max_turns/stage_timeout 从 routing config 回填（原来继承 deterministic 计划的 0 值）；`budget.stage_timeout` 统一做 min(stage, remaining) 截断，预算耗尽如实记 timeout。
5. **P1-4 证据保留**（`retrieve/payload.py`）：`finalize_payload` 仅在 `target_strategy != "seed_then_research"` 时做 exact 过滤；seed 模式保留旁证，`target_match` 照常只报告目标命中。
6. **P1-5 过载分类**（`retrieve/stages.py` + `x_search.py` + `pipeline.py` + `config.py`）：信号量获取独立 `GROK_PROXY_MCP_X_SEARCH_QUEUE_TIMEOUT_SECONDS`（默认 30s）排队超时→`StageOverloaded`；general/exact_only/seed 三条路径遇过载记录 `status="overloaded"` 并**跳过 Smart 升级与 raw 扩展**，不再放大负载。
7. **P1-6 OAuth 错误分类**：`_refresh_sync` 解析 token endpoint JSON `error`，`invalid_grant`/`invalid_client` → `AuthRequiredError`（消息含 AUTH_REQUIRED + 登录命令，经既有管道透传 `auth_login_command`）；其余非 200 → `TokenRefreshUpstreamError(status)`。`reauth_required` 仅在凭据被拒或 400 时置 True（超时/429/5xx 不再误报）。`error_result` 对 AUTH_REQUIRED 额外标记 `stage="auth_refresh"` 与 `auth_error={code, retryable:false}`。
8. **P1-7 stdio 帧边界**（`mcp_server.py`）：读取/长度/解码/解析纳入同一逐帧 try；自建 reader limit=1MiB；超限帧依赖 `readline` 自带的丢弃语义 + 连续 8 次上限保护；坏 UTF-8 → -32700 后继续服务；合法 ping 在坏帧后仍被应答。
9. **P2-3 JSON-RPC 严格化**：`handle` 统一校验 envelope（对象/`jsonrpc=="2.0"`/非空 method/id 类型合法，bool id 拒绝）；一切无 id 消息（通知）不执行副作用也不回响应；非对象 JSON → -32600（原 -32603）。
10. **P1-8 归因过滤 + SSE 解析**（`main.py`）：custom_tool_call 仅按 `name=="x_keyword_search"`（或 type x_search/x_search_call）过滤，客户端自有工具调用完整保留；`custom_tool_call_input.delta/done` 由有状态 filter 按 item_id 归因丢弃；SSE 分隔符支持 `\n\n`/`\r\n\r\n`/`\r\r` 并跨 chunk 安全，单事件缓冲上限 4M 字符，超限关闭上游。
11. **P2-1**：删除 `_load_json/_save_json` 的 RuntimeError 同步重跑 fallback（to_thread 原样传播安全错误，只执行一次）。
12. **P2-4**：`RetrievalPlan.route_warning` —— 自定义/未知名模型不支撑 auto effort 时，警告进入 payload `warnings`（策略漂移可见）。
13. **P2-5**：`_canonical_url_key`（scheme/host 小写、默认端口、fragment、utm_*/fbclid/gclid 等跟踪参数剔除、剩余参数排序）用于 `_item_key`/`_post_key`/`_source_key` 的 URL 键；X 状态 URL 仍按 status ID 建键。
14. **P2-6**：`read_local_state` 对 truthy 非对象 JSON 与非字符串 access_token 抛 `AuthRequiredError`（typed、含登录自愈），不再 AttributeError。
15. **P2-7**：`stdio_main` 以 try/finally 收尾 `xai_responses.aclose_client()`。

### 新增测试（`tests/test_audit_phase8.py`，37 例）

覆盖：Host/Origin 边界（421/403/白名单放行）；他进程刷新采纳与失败不回滚、state_version 单调；取消后 R1 仍落盘且只刷新一次；20 路 401 storm 单飞；invalid_grant→AUTH_REQUIRED 自愈（含 `auth_login_command`/`stage=auth_refresh`）与 5xx 可重试不误报；seed_then_research E2E（Smart 真实执行、旁证存活）与 exact_only 过滤回归；排队过载不升级不 raw、上游零调用；stdio 坏 UTF-8/超限帧/标量 JSON/通知合规/共享 client 关闭；auto x_search 客户端工具保留、item_id 归因、CRLF/分块/溢出；`_load_json` 不重跑；effort 告警；URL 规范化折叠与语义参数区分；auth state 非法形状矩阵。

### 刻意不做 / 残留

- 未默认生成 PROXY_API_KEY（会破坏现有本地客户端）；Host/Origin 边界已覆盖 rebinding 与跨站浏览器两条路径，密钥仍可作为纵深防御显式配置。
- 真实双进程 flock 竞争未做进程级集成测试（单进程内模拟并发写盘 + 采纳/回滚语义已覆盖核心不变量）。
- `oauth_flow` 登录写入与其他写路径尚未纳入 state_version CAS（交互式低频路径，文档化于此）。
- 审计矩阵 #8（部分成功后 Smart 429）/#9（重复帖富化）已由既有 0.2.0 测试语义覆盖（Fast 成果保留、`_post_key` status-ID 合并），未重复立项。

## 第九阶段记录（审计后续优化：韧性、准入与结构清偿）

- **日期**: 2026-08-23
- **输入**: 第八阶段评审后维护者确认的 8 项后续优化（除「明确不建议做」三项外全部实施）。
- **验证**: `ruff check .` 通过；`basedpyright` 0 errors；`pytest -q -W error` **244 passed**（第八阶段 234 + 本阶段 10 新增）。

### 实施明细

1. **刷新失败短负缓存**（`token_manager.py`）：transient 失败（超时/429/5xx）后 8 秒内（`REFRESH_FAILURE_SUPPRESS_SECONDS`）顺序调用方直接收到 `Token refresh failed recently ... suppressed`，不再逐个请求重打 token endpoint；`AuthRequiredError`（invalid_grant 等）**永不抑制**——自愈登录路径每次都能即时探测恢复。成功刷新即清除标记。
2. **交互式写入纳入锁与版本**：`save_local_state`（native login / 导入脚本的唯一写入通道）现在持有 `_state_file_lock` 并基于磁盘当前 `state_version` 单调递增，登录与后台刷新事务彻底互斥，第八阶段的最后一条事务残留关闭。
3. **请求级准入许可**（`retrieve/x_search.py` + `pipeline.py`）：`request_admission()` 以 ContextVar 标记请求上下文，pipeline 在生成式段落（general 全段 / exact fallback+smart / seed 研究+raw）外层获取**一个**许可贯穿 Fast→Smart→raw 全部层级转换，阶段不再各自排队；直接调用 `_call_x_search_result` 的路径仍独立准入。排队超时统一在新 `GROK_PROXY_RETRIEVE_QUEUE_TIMEOUT_SECONDS`（原 `GROK_PROXY_MCP_X_SEARCH_QUEUE_TIMEOUT_SECONDS`，未发布即改名）。
4. **structured_output 能力联动**（`policy.py` + `stages.py`）：`model_supports_structured_output()` 与 effort 校验同构——未知模型携带 strict json_schema 会在 `run_search_stage` 摘除 `_structured_output`（消除上游 400 路径），显式模型的 route_warning 同时列出 effort 与 structured 两项策略漂移。
5. **拆除 `mcp_x_search.py` 兼容门面**：第七阶段标记的结构残留清偿。`mcp_tools._search_caller()` 的 sys.modules 探测删除，调用点改为调用时解析 `x_search._call_x_search_result`（测试 monkeypatch 面统一为 `retrieve.x_search`）；6 个测试文件 141 处引用全部迁移到真实模块（`mcp_tools._handle` / `pipeline` / `x_search` / `mcp_posts`）；services/install.sh 无依赖，stdio 入口收敛为 `python mcp_server.py`。
6. **env / 指标命名统一**：`GROK_PROXY_RETRIEVE_CONCURRENCY` 为正名（旧名 `GROK_PROXY_MCP_X_SEARCH_CONCURRENCY` 未设置新名时仍生效，README 已注明）；metrics 一致读取新值。
7. **`x_keyword_search` 内部名可配置**：`GROK_PROXY_X_SEARCH_INTERNAL_TOOL_NAMES`（默认 `x_keyword_search`），xAI 改名时一处可调，auto-x_search 归因过滤不再硬编码。
8. **测试基建**：`tests/conftest.py` 提供 autouse token 状态重置（负缓存/共享刷新任务跨测试隔离）与 `loopback_client` fixture（9 处 TestClient 收口）；README/README.zh-CN/docs RFC 补齐全部新 env 文档。

### 新增测试

- `tests/test_phase9.py`（9 例）：负缓存抑制/过期重试/invalid_grant 不抑制；save_local_state 版本单调且不回退；三层级单请求恰好一次准入（CountingSemaphore）；未知模型摘除 structured_output、route_warning 含双提示；内部名可配置。
- `tests/test_multiprocess_refresh.py`（1 例，POSIX only）：**真实双子进程** + 一次性轮换 fake OAuth server —— 断言仅一次上游刷新、落盘 R1、版本递增、无 reauth 回滚。第八阶段记录的「跨进程仅单进程模拟」残留关闭。

### 过程记录

- 删除门面后首次全量跑出现 9 例一次性失败，清理 `__pycache__` 后 13 连绿 + `-W error` 双绿，判定为陈旧字节码 artifact（删除模块 + 旧 pyc 并存的瞬时态），非真实竞态；CI 干净检出无此条件。
- 队列超时 env 在第八阶段 commit 后、任何发布前完成改名，无兼容负担；并发 env 保留旧名回退。

### 剩余（低优先级，均不阻塞）

- `_x_search_*` 指标前缀与 `_x_search_semaphore` 内部名仍是历史命名（公开 env 已统一，仅内部标识符）。
- 审计矩阵 #16 的完整 SSE parser fuzz（当前覆盖 LF/CRLF/CR/分块/溢出主路径）。

## 第十阶段记录（v0.3.0：响应缓存与请求合并）

- **日期**: 2026-08-23
- **输入**: Firecrawl 缓存机制对比分析后维护者确认的方案（三处修正后实施：隐私显式化、命中率按确定性分层、L1 合并先行；明确不做 stale-while-revalidate 默认开、语义相似度缓存、Redis）。
- **验证**: `ruff check .` 通过；`basedpyright` 0 errors；`pytest -q -W error` **256 passed**（第九阶段 244 + `tests/test_retrieve_cache.py` 12 新增）。
- **版本**: 0.2.0 → 0.3.0（SERVER_VERSION / pyproject / user-agent）。

### 设计与实现

1. **缓存分层**（`retrieve/cache.py`）：
   - **L1 进程内合并**：`coalesce()` 以共享 task 实现同键并发单飞（shield 模式，调用方取消不影响共享执行与落盘），与 token 刷新同款语义；
   - **L2 SQLite WAL**：`~/.local/state/grok-oauth-proxy/cache.sqlite`（可用 `GROK_PROXY_RETRIEVE_CACHE_PATH` 覆盖），per-call 连接 + busy_timeout，跨进程安全（HTTP 网关 + 多 stdio 会话共享）；
   - **只缓存确定性键**：`exact_only`（TTL 86400s，推文恒定）与 `latest_by_handle`（TTL 480s）；semantic/claim/seed 类生成式检索**永不持久化**。键基于规范化 metadata（handle 排序小写、query 折叠空白小写、质量/过滤参数 canonica JSON）。
2. **穿透参数**：`force_refresh`（跳过读缓存，仍合并+回写）与 `max_age_seconds`（当次覆盖 TTL，0 即强制过期），进入 `RETRIEVE_ARGUMENT_KEYS` 与 inputSchema。
3. **只缓存 `retrieval_status == "ok"`** 的完整 structuredContent（含 cache 块与 new 标注）；degraded/no_match/error 永不落盘。命中响应带 `cache: {hit, age_seconds, policy, saved_cost_in_usd_ticks}`。
4. **快照 diff（ID-only）**：`fetch_history` 只存 (handle, status_id, content_hash, first_seen, last_seen)——**零正文**；`new_since_last_fetch` 标注在 item 上，跨查询持续追踪博主更新；responses 与 history 各自 LRU（同 `GROK_PROXY_RETRIEVE_CACHE_MAX_ENTRIES` 阈值）。
5. **成本透明**：`assemble_payload` 在 stage 上记录 `usage_cost_ticks`，payload 顶层汇总 `usage_cost_ticks`；缓存命中显示 `saved_cost_in_usd_ticks`。
6. **可观测**：`mcp_x_retrieve_cache_total{result=hit|miss|bypass|write|error}`。
7. **隐私与权限**：缓存文件与其 `-wal`/`-shm` 全部 0600（WAL 边车不跟随 umask 的缺陷在实施中发现并修复）；README 双语「零内容落盘」条款改写为「日志零落盘 + 功能性缓存可关且同级隔离」；总开关 `GROK_PROXY_RETRIEVE_CACHE=false` 时连 ID-only 历史也完全不落盘。
8. **测试隔离**：conftest autouse 将缓存路径指向 per-test tmp 文件——实施中发现无隔离时测试会污染真实 `~/.local/state` 缓存（已删除污染文件并加固）。

### 新增测试（12 例）

策略分层与键规范化；exact 二次调用零上游命中缓存（含 saved ticks）；force_refresh 回写；max_age=0 强制过期；禁用时不落盘；degraded 不缓存；并发同键单飞（阻塞线程证明 join 而非重复执行）；LRU 上限；new_since_last_fetch 只标未见帖且历史零正文；0600/0700 权限；成本票汇总与免费 stage 不标。

### 明确不做（延续评审结论）

- stale-while-revalidate 默认开（对"要最新"场景有害）；
- 语义相似度缓存（生成式检索键空间近乎无限，脆弱）；
- Redis / 外部缓存服务（本地自用无理由）。

### 残留（低优先级）

- 跨进程并发相同请求的合并（L1 仅进程内；跨进程重复由 L2 命中兜底）；
- 缓存命中_age_ 直方图（当前仅计数器）；
- `new_since_last_fetch` 对 exact_only 模式意义有限，仅对 handle 流真正有用（已按此实现，未做模式限定以保持简单）。
