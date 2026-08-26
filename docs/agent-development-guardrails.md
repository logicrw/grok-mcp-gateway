# Agent 开发与架构改造守则 (Agent Development Guardrails)

本文档是未来任何 AI Agent（或人类工程师）维护、重构、升级本网关（`grok-mcp-gateway`）时**必须严格遵守的铁律（Hard Invariants）**。
旨在彻底杜绝“截断体温计式乱砍超时”、“模型升级刻舟求剑”、“过度工程化违背无状态初衷”等历史事故重演。

---

## 铁律一：物理耗时客观性原则（Physics Over Magic）
- **严禁掩耳盗铃**：当遇到模型耗时偏长时，**绝对禁止通过强行调低网关超时（Timeout）来“掩盖问题”**！
- **实测先行**：更换或升级上游模型时，必须先通过真实调用实测其思维链（CoT）耗时分布（包含 low/medium/high 各档位真实秒数），严禁凭经验臆断。
- **对症下药三板斧**：
  1. **分流优化**：非复杂研究意图，优先分流至非推理轻量模型（Fast Lane）；
  2. **档位适配**：根据模型真实物理耗时适配默认档位（如 Grok 4.6 默认必须是 `low` 29s，绝不能盲目默认 `medium` 55s）；
  3. **参数透传**：将高深度推演权（`reasoning_effort="high"`）开放为调用参数，由外部按需显式唤醒。

---

## 铁律二：最坏情况预算完备性（Worst-Case Budget Guarantee）
- **串联超时数学闭环**：流水线各子阶段硬超时之和，**必须严格小于等于总关门限时**：
  $$\text{Fast (10s)} + \text{Smart (120s)} + \text{Raw (50s)} \le \text{Total Deadline (180s)}$$
- **禁止单阶段挤占兜底预算**：
  - 严禁将单一阶段超时设定过大（例如 >120s），导致前序阶段超时后，后置容灾阶段（Raw Expansion 兜底）因时间耗尽而被迫饥饿退出；
  - 必须确保在最极端的“前两棒全部跑满硬超时”情况下，第三棒兜底模型依然**拥有完整的 50 秒预算**进场抢救候选推文。

---

## 铁律三：客户端 60 秒红线默认收敛（Client Ceiling Awareness）
- **常规请求收敛**：
  - 日常 90% 以上的无特殊修饰查询（默认 auto intent / low 推理档位 / latest_by_handle），必须在 **35 秒以内** 完成并交付（Fast Lane 3~6s，Smart low 29~35s），天然兼容严格限制 60s 的通用客户端；
- **长推演显式契约**：
  - 针对显式声明的深度推演任务（`intent: "verify_claim"` 或显式指定的 `high` / `xhigh` 推理），其真实物理耗时可达 60~80s，明确要求客户端环境放宽超时（或通过异步任务模式运行）；
  - 严禁作出“所有请求在任何高阶推理档位下都免疫 60s”的伪命题承诺；同时依托优雅交付机制，在任何极端客户端超时发生时最大限度保全已抓取信源。

---

## 铁律四：显式优于隐式，拒绝防御性暗门（Explicit Over Implicit）
- **严禁全局隐式兜底**：
  - 严禁设置诸如 `GROK_PROXY_RETRIEVE_STAGE_TIMEOUT_SECONDS` 这类模糊的“全局通用阶段超时”变量；
  - 每一个执行阶段（Fast / Smart / Raw / oEmbed）**必须由调用方显式传入其专属阶段限时**；
  - `RequestBudget.stage_timeout(stage_seconds: float)` 的唯一职责是执行 `min(stage_seconds, remaining())`，严禁替调用方猜测默认超时。

---

## 铁律五：死守无状态代理边界，严防过度工程化（Protect Stateless Non-Goals）
- **网关的核心定位是 Stateless Proxy，不是全功能数据库**：
  - 严禁引入向量数据库、学习型路由、多智能体交互框架等破坏极简原则的重型模块；
  - 现存的 SQLite 机制仅严格用于“确定性推文 ID（oEmbed）”的防刷去重与费用节约，严禁向全文搜索或业务状态蔓延；
  - 必须永久保障 `GROK_PROXY_RETRIEVE_CACHE=false` 纯无状态逃生通道的完整性，任何改动均不得破坏“零磁盘写入”的运行能力。

---

## 铁律六：完整闭环三道门禁（Verification Triple Gates）
任何对代码库的修改，声称“修复完毕”之前，**必须依次通过以下三道硬性门禁**：
1. **静态类型检查**：运行 `.venv/bin/basedpyright`，必须达到 `0 errors, 0 warnings, 0 notes`；
2. **全量自动化回归**：运行 `.venv/bin/pytest`，必须达到 **全量测试通过（263+ tests 全部绿色）**，绝不容许忽略偶发测试 flake；
3. **真实运行态热生效验证**：
   - 必须执行 `launchctl kickstart -k gui/501/io.logicrw.grok-mcp-gateway` 重启常驻服务；
   - 必须通过 `curl -s 'http://127.0.0.1:9996/health?deep=1'` 确认深层健康检查返回 200 OK；
   - 必须在真实 Agent 或客户端中发起一次真实的 MCP 工具调用，验证推文正文正常返回。
