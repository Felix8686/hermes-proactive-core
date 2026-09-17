# HERMES-PROACTIVE-CORE-V1：分阶段实施计划

> 原始计划快照：设计阶段；阶段状态见第 14 节及最新 HANDOFF-PHASE3.md
> 目标分支：codex/proactive-core-v1
> 日期：2026-09-17（Asia/Singapore）  
> 当前阶段终点：GATE_PROACTIVE_CORE_NOTIFICATION_REVIEW

> 状态更新：Phase 1–2 已在批准范围内完成，离线测试 33/33 通过。最终验收字段与 VPS 只读核验见 PROACTIVE-CORE-PHASE1-2-REPORT.md。

## 1. 执行约束

> 本节及第 2–13 节保留原计划脉络；与当前 VPS-only normalization 冲突的条目均以第 14 节和最新 HANDOFF-PHASE3.md 为准。

当前阶段已获授权：完成 VPS-only normalization、Phase 1–2 回归测试和受严格限制的 VPS Canary dry-run；本计划不授权通知投递、ACT、生产调度变更或模型变更。

以下范围在 ChatGPT 审核和用户另行批准前保持不动：

- VPS Hermes 配置、profile、Cron、Hooks、Webhook、Telegram；
- OpenViking、Memory、KnowledgeVault；
- 浏览器会话、cookies/tokens/API keys；Proactive Core 不读取或调用 Windows Executor；
- Cloudflare、模型/provider、Hermes 版本、Gateway/systemd；
- 现有 fav、knowledge、update-check 业务 Job；
- 当前 Windows Hermes retirement 状态不得作为运行依赖；只读盘点中仅分类并报告相关残留。

## 2. 阶段总览

| 阶段 | 目标 | 环境 | 默认副作用 | 通过门 |
|---|---|---|---|---|
| 0 | 文档、现状、风险和设计评审 | IdeaForge/离线 | 无 | ChatGPT 审核通过 |
| 1 | Event/Policy 合约与 fixture | IdeaForge/离线 | 无 | schema + policy 测试全绿 |
| 2 | Shadow detector | 本地/隔离目录 | 不发送、不执行 | 证明去重/静默/升级 |
| 3 | VPS canary dry-run | VPS 隔离手工进程 | 只记录，不通知、不动作；不改现有 Cron | 前后快照与 Canary 用例通过 |
| 4 | 只开确定性通知 | VPS | 仅受预算约束的既有 Telegram | 通知质量、恢复和熔断通过 |
| 5 | 低风险动作/目标候选 | VPS，逐项开关 | 一次一个，需 allowlist | 每个动作独立验收 |
| 6 | 复盘与 v1 接受 | 文档/运维 | 可回滚 | 用户明确接受 |

## 3. 阶段 0：当前设计审查（已完成）

### 输入

- 线上只读 VPS audit；
- Hermes v0.19.0 安装包/源码能力；
- Hermes 官方 v0.21.3 release/docs/current source；
- OpenClaw、Home Assistant、n8n、Temporal、GitHub Actions 对照；
- Reddit、V2EX、抖音社区样本；
- 现有 PROACTIVE_RULES、GOALS 与历史审计材料；历史 Windows Executor 资料仅用于只读分类。

### 产出

- PROACTIVE-CORE-EXISTING-CAPABILITIES.md
- PROACTIVE-CORE-V1-DESIGN.md
- PROACTIVE-CORE-V1-IMPLEMENTATION-PLAN.md
- PROACTIVE-CORE-DESIGN-REVIEW.md
- D:\Codex Projects\Files\PROACTIVE-CORE-RESEARCH-20260917.html

### 原始停止点

设计评审 Gate 已在后续阶段通过；这里只保留原始设计阶段记录。当前 Phase 3 范围与 Gate 见第 14 节。

## 4. 阶段 1：离线合约与测试

只有在设计审核通过后，才可开始。建议在 IdeaForge 的独立设计目录中新增：

~~~
proactive_core/
  event_model.py
  fingerprint.py
  policy.py
  dedupe.py
  budget.py
  state_store.py
  render.py
tests/
  fixtures/
  test_event_model.py
  test_policy.py
  test_dedupe.py
  test_interruption.py
~~~

此阶段只允许：

- 读 fixture；
- 生成决定结果；
- 验证空 stdout/非空摘要；
- 写入测试输出和设计样例。

禁止：

- SSH 到生产写文件；
- 读取生产 secrets；
- 调 Telegram、OpenViking 或任何外部执行器；
- 修改现有 Cron/Hook/Webhook；
- 安装新服务。

必须覆盖的 fixture：

| 类别 | 例子 |
|---|---|
| 首次事件 | 新 Cron failure |
| 重复事件 | 同 Job 同错误同 fingerprint |
| 恢复 | failure → ok |
| 升级 | MEDIUM → HIGH 或 requires_action false → true |
| 静默 | 无变化/已处理/过期 |
| 并发 | 同 subject 两个 tick 重叠 |
| 资源错误 | Event Store 锁/损坏、delivery 失败 |
| 模型错误 | timeout、空响应、非法 JSON |
| 高风险 | delete/send/config/upgrade/restart |

## 5. 阶段 2：本地 Shadow detector

### 目标

用脱敏 fixture 和本地副本验证：

~~~
旧 probe output
  -> 新 Event
  -> decision
  -> shadow record
~~~

shadow 模式必须：

- 不发送 Telegram；
- 不修改生产或本地业务状态；
- 不包含 Windows 输入源、动作源或 dispatch；
- 不写 OpenViking/KnowledgeVault；
- 对每个事件记录“如果启用会做什么”和“为什么不做”；
- 可重复运行且结果稳定。

### 通过标准

- 同样输入的 fingerprint、decision、reason 稳定；
- 重复运行通知候选不增长；
- 恢复事件只出现一次；
- Budget/circuit breaker fixture 可复现；
- 采样耗时、内存和输出量在 VPS 预算内；
- 旧 proactive_monitor 结果可以被解释为输入，而不是直接当作通知。

## 6. 阶段 3：VPS Canary dry-run

最新 HANDOFF-PHASE3.md 已授权在所有前置项通过后执行 Canary。必须使用隔离手工进程，不得修改或挂接现有 Cron，也不得改写 Hermes/Gateway/systemd 配置：

1. 先完成 VPS-only normalization 和活动依赖只读审计，记录准确文件/来源与 SHA-256 基线；
2. 完整运行 Phase 1–2 测试，必须全部 PASS；
3. 使用隔离目录和私有 SQLite 状态库，不触碰生产 Event/业务状态；
4. 仅 Canary 子进程临时设置 PROACTIVE_ENABLED=true；两个通知/动作开关显式 false，父环境和持久配置不变；
5. 仅接 VPS Cron/Job、Kanban 摘要、Gateway、proactive_monitor 的只读/脱敏样本；不访问 Windows；
6. 覆盖静默无变化、重复抑制、synthetic failure、恢复、跨进程重启持久性、SQLite lock/fail-closed、Core disabled；
7. 保存进程退出码、受控 stdout、隔离 SQLite 检查结果和前后运行面只读快照；
8. Canary 期间 LLM calls=0、Telegram messages=0、actions=0、Windows dependency=NONE。

任何生产配置变化、意外外发、ACT、LLM 调用、非 VPS 依赖、测试失败或无法证明状态隔离，立即终止并保持在当前 Gate；不得继续到通知阶段。

## 7. 阶段 4：只开确定性通知（本轮未授权）

本阶段不属于当前 Canary 授权。必须先通过 GATE_PROACTIVE_CORE_NOTIFICATION_REVIEW 并取得新的明确授权；本轮 Telegram 主动通知保持关闭。

### 开启顺序

按独立闸门逐项开启：

1. 只通知新的 Cron failure 且不在 cooldown 内；
2. 再通知恢复；
3. 任何未来通知范围均须经单独 ChatGPT Gate 和用户授权；VPS-only 范围之外的来源永久排除；
4. 暂不通知 Goal stale、Brief 或模型摘要；
5. 每一步观察后再决定下一步。

### 通知验收

- 无变化时 stdout 为空，Telegram 不产生重复消息；
- 新 failure 一次一条；
- 同一 failure 在 cooldown 内不重复；
- recovery 最多一次；
- delivery failure 不伪造成功；
- 达到 3/day 后进入 budget_exhausted；
- 关闭 notification flag 后仍可保留 shadow 审计；
- URGENT 的定义、目标和人工响应已书面确认。

## 8. 阶段 5：低风险动作与 Goal 候选（本轮未授权）

本阶段不属于当前 Canary 授权；本轮 ACT 始终关闭，且 Canary 不执行任何动作。

### 8.1 低风险动作

第一个动作只能是：

- 明确 allowlist；
- 幂等；
- 可逆或无副作用；
- 不触发外部消息；
- 有单次执行预算；
- 有独立 idempotency_key；
- 失败后不自动连环执行。

候选示例仅作为评审材料，不是授权：

- 重新读取一个状态；
- 生成诊断快照；
- 标记事件已观察；
- 对已知 transient 探针进行一次安全 retry。

禁止把以下动作归为 LOW：

- 任意 shell；
- SSH 任意命令；
- 浏览器操作；
- 发送/发布；
- 改配置/provider/key；
- 升级/重启；
- 删除/付款/权限变更。

### 8.2 Goal candidate

只有在用户明确标记 active goal、证据来源和允许通知窗口后，才可生成 NEXT_ACTION_CANDIDATE。初期只允许 NOTIFY 或 ASK，不允许自动执行候选。

验收重点：

- 不修改 GOALS.md；
- 不把旧目标/旧会话当成新授权；
- 没有进展时 SILENT；
- 有阻塞时说明证据，不循环催促；
- COMPLETE 后不重复提醒；
- 目标候选与 Hermes /goal 的 judge call 分开计费和审计。

## 9. 阶段 6：复盘、接受与回滚

### 9.1 接受指标

建议至少观察：

- 每日主动通知数量；
- 重复通知率；
- 误报率；
- 用户明确回复/确认率；
- action success/failure；
- delivery failure；
- Event Store 写入失败；
- 模型调用量与费用；
- Gateway/业务 Job 的回归情况；
- 关闭开关后的停止时间。

没有“用户价值”证据，不扩大范围。

### 9.2 回滚顺序

1. 关闭 ACTIONS；
2. 关闭 NOTIFICATIONS；
3. 关闭 PROACTIVE；
4. 停止/暂停新增 Proactive Job（如后续确有新增）；
5. 保留审计和事件 state，避免删除证据；
6. 按精确文件清单恢复上一版；
7. 验证 Gateway PID/lifecycle、Telegram polling 和既有业务 Job；
8. 记录回滚原因和最后一次事件。

禁止使用宽泛删除、清空目录、重置仓库、删除用户数据或“顺手清理”作为回滚。

## 10. 未来可能涉及的文件（现在均未修改）

这是实施前的候选清单，不是本轮变更清单：

| 位置 | 未来用途 | 当前状态 |
|---|---|---|
| Hermes data/scripts/proactive_monitor.py | 将探针输出接到 Core 或保持兼容适配 | 未修改 |
| Hermes data/proactive/PROACTIVE_RULES.md | 规则与 policy version 的来源 | 未修改 |
| Hermes data/proactive/state.json 或新 SQLite | 过渡状态/事件索引 | 未修改 |
| Hermes data/cron/jobs.json | 未来调整 Job 参数或新增隔离 Job | 未修改 |
| IdeaForge proactive_core/ | 离线实现与测试 | 未创建 |
| GOALS.md | 仅未来读取，不应由 Core 自动写 | 未修改 |
| Windows Executor/heartbeat/action/dispatch | 永久不属于当前 Core 架构 | 不接入、不调用 |

## 11. 资源与版本策略

- v1 默认不升级 Hermes；先用 VPS v0.19.0 已证实的 Cron/no-agent/Gateway 能力。
- v0.21.3 现有新能力（如更完整的 monitor/webhook/全局暂停能力）另立升级评估，不在本计划偷渡。
- 不把线上当前 gpt-5.6-luna 的存在理解为 Proactive Core 必须使用 LLM。
- 健康巡检 LLM 调用预算为 0；目标候选默认最多 1/day。
- 新增状态应为小型 SQLite/JSON 索引，先设保留期限；不建立大日志/向量库。
- 资源评估以 canary 实测为准，保留 VPS 内存和 Gateway 稳定性余量；不在设计阶段假定“肯定够”。

## 12. Git 与分支状态

当前项目中心为 `Felix8686/hermes-proactive-core`，本地检出分支为 `codex/proactive-core-v1`。该仓库边界已由用户确认；不得合并 `main`。IdeaForge 根目录仍不是本项目仓库，也不得初始化或冒充项目中心。

## 13. 本计划的停止条件

任何一个条件出现，都停在当前阶段并报告：

- 生产状态无法以正确 HERMES_HOME 复核；
- 发现任何非 VPS 输入或 Windows 执行/dispatch 依赖；
- Event Store 无法保证幂等；
- 模型/通知预算没有硬上限；
- HIGH 风险缺少 ASK 闸门；
- 无法证明旧业务 Job 未受影响；
- Gateway/Telegram 或既有业务 Job 出现回归；
- 用户要求扩大到 Cloudflare、OpenViking、升级、重启或外部发布；
- 最新 HANDOFF-PHASE3.md 的任何前置条件未通过；
- Canary 期间观测到 LLM、外发、ACT、生产配置变化或任何 Windows 依赖。

## 14. 最新交接：VPS-only normalization 与 Phase 3 Canary

最新权威来源：`HANDOFF-PHASE3.md`，提交 `2dbc24c145922905646369ef6232a1d595452d71`。它允许在先完成归一化、生产依赖只读审计和 Phase 1–2 全套测试后，执行一次隔离 VPS Canary dry-run。

### 当前架构硬约束

- VPS_ONLY = TRUE
- WINDOWS_HERMES_ROLE = NONE
- WINDOWS_EXECUTOR_ROLE = NONE
- WINDOWS_POWER_OFF_IMPACT = NONE
- Core 仅使用 VPS Cron/Job、Kanban 摘要、Gateway、VPS proactive_monitor 等批准的 VPS 只读输入；不存在 Windows heartbeat/offline/recovered/action source/dispatch。
- Windows 关机不得影响任何当前 Hermes 生产能力或 Proactive Core 能力。
- 生产模型/provider、Gateway、Cron、Hook、Webhook、Telegram、systemd 不得变更或重启；不得合并 main。

### 顺序与 Canary 开关

1. 完成设计、计划、实现范围的 VPS-only normalization，并对现有生产引用只读分类为 ACTIVE / STALE / HISTORICAL；HISTORICAL 保留。ACTIVE/STALE 只有在依赖证据明确且变更安全时才做最窄处理，保存精确备份与证据，否则停止。
2. 只读确认 active Jobs、Cron、Skills 对 Windows Executor 的依赖数均为 0。
3. 完整运行 Phase 1–2 测试，必须全部 PASS。
4. 仅在 VPS 运行隔离手工 Canary 子进程，可临时设置 PROACTIVE_ENABLED=true；NOTIFICATIONS 与 ACTIONS 必须显式 false，不写入持久配置或父进程环境。
5. 使用隔离目录和 SQLite；不改/新增 Cron，不接 Telegram、不执行 Action、不调用 LLM、不访问 Windows。
6. Canary 需覆盖无变化静默、duplicate suppression、synthetic failure/recovery、进程重启持久性、SQLite lock/fail-closed、Core disabled。

### 最终停止点

生成并提交 `PROACTIVE-CORE-PHASE3-CANARY-REPORT.md` 后停止：

`GATE_PROACTIVE_CORE_NOTIFICATION_REVIEW = WAITING_FOR_CHATGPT`

在 Gate 审核之前，不得开启 Telegram 主动通知或 ACT，也不得部署/合并到 main。

### 本次执行结果（2026-09-17）

- Phase 1–2 全套测试 33/33 PASS；VPS-only active Windows Executor 依赖计数 Jobs/Cron/Skills 均为 0。
- 隔离手工 Canary dry-run 完成；生产文件在 Canary 窗口内未变更，生产服务未重启，LLM/Telegram/ACT/Windows 依赖均为 0。
- duplicate/recovery 的独立新进程 SQLite 复核通过；初始过严的 append-only 计数断言及校正证据见 [PROACTIVE-CORE-PHASE3-CANARY-REPORT.md](../PROACTIVE-CORE-PHASE3-CANARY-REPORT.md)。
- 当前停止于 `GATE_PROACTIVE_CORE_NOTIFICATION_REVIEW = WAITING_FOR_CHATGPT`；通知与动作仍禁止。
