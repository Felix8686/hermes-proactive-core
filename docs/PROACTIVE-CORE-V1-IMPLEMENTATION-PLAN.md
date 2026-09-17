# HERMES-PROACTIVE-CORE-V1：分阶段实施计划

> 原始计划快照：设计阶段；尚未开始实施  
> 目标分支：codex/proactive-core-v1-design  
> 日期：2026-09-17（Asia/Singapore）  
> 当前阶段终点：GATE-PROACTIVE-CORE-DESIGN-REVIEW

> 状态更新：Phase 1–2 已在批准范围内完成，离线测试 33/33 通过。最终验收字段与 VPS 只读核验见 PROACTIVE-CORE-PHASE1-2-REPORT.md。

## 1. 执行约束

> 本节及第 2–13 节记录初始设计 Gate 时的计划快照。审核后当前批准范围与停止点见第 14 节。

本计划只描述未来如何实施，不授权本轮实施。当前阶段已经完成的动作仅包括只读核查、互联网调研和四份设计/审计文档。

以下范围在 ChatGPT 审核和用户另行批准前保持不动：

- VPS Hermes 配置、profile、Cron、Hooks、Webhook、Telegram；
- OpenViking、Memory、KnowledgeVault；
- Windows Executor、浏览器会话、cookies/tokens/API keys；
- Cloudflare、模型/provider、Hermes 版本、Gateway/systemd；
- 现有 fav、knowledge、update-check 业务 Job；
- Windows Hermes retirement 状态。

## 2. 阶段总览

| 阶段 | 目标 | 环境 | 默认副作用 | 通过门 |
|---|---|---|---|---|
| 0 | 文档、现状、风险和设计评审 | IdeaForge/离线 | 无 | ChatGPT 审核通过 |
| 1 | Event/Policy 合约与 fixture | IdeaForge/离线 | 无 | schema + policy 测试全绿 |
| 2 | Shadow detector | 本地/隔离目录 | 不发送、不执行 | 证明去重/静默/升级 |
| 3 | VPS canary dry-run | VPS，现有 Cron 入口 | 只记录，不通知、不动作 | 前后快照与资源预算通过 |
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
- 现有 PROACTIVE_RULES、GOALS、Windows Executor 和历史审计材料。

### 产出

- PROACTIVE-CORE-EXISTING-CAPABILITIES.md
- PROACTIVE-CORE-V1-DESIGN.md
- PROACTIVE-CORE-V1-IMPLEMENTATION-PLAN.md
- PROACTIVE-CORE-DESIGN-REVIEW.md
- D:\Codex Projects\Files\PROACTIVE-CORE-RESEARCH-20260917.html

### 当前停止点

停止在 GATE-PROACTIVE-CORE-DESIGN-REVIEW，等待 ChatGPT 审核。未进行任何实现或部署。

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
- 调 Telegram、OpenViking、Windows Executor；
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
- 不调用 Windows Executor；
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

需另行取得生产变更授权后，才可以考虑此阶段。推荐先不改原 Job，而是：

1. 备份将要涉及的确切文件；
2. 上传/部署只读或 dry-run 代码；
3. 保持 PROACTIVE_ENABLED=false；
4. 保持 NOTIFICATIONS_ENABLED=false；
5. 保持 ACTIONS_ENABLED=false；
6. 仅在隔离 state store 中记录 shadow 结果；
7. 观察至少一个现有巡检周期和一个 Gateway/Telegram 正常周期；
8. 检查进程、Gateway、既有四个 Job、Executor heartbeat、Knowledge weekly 均未受影响。

### Canary 证据

- 部署前后文件 SHA-256；
- Gateway PID、systemd active、heartbeat 时间；
- Cron Job 数量、启用状态、最近结果；
- proactive shadow event count/decision distribution；
- LLM call count=0；
- notification/action count=0；
- 内存、耗时、错误和锁竞争；
- 旧业务 Job 的成功/失败与 Proactive Core 无因果混淆。

若不能取得完整前后证据，停止在 Canary，不得进入通知阶段。

## 7. 阶段 4：只开确定性通知

### 开启顺序

按独立闸门逐项开启：

1. 只通知新的 Cron failure 且不在 cooldown 内；
2. 再通知恢复；
3. 再通知 Executor offline/heartbeat 超时；
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

## 8. 阶段 5：低风险动作与 Goal 候选

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
- Gateway/业务 Job/Executor 的回归情况；
- 关闭开关后的停止时间。

没有“用户价值”证据，不扩大范围。

### 9.2 回滚顺序

1. 关闭 ACTIONS；
2. 关闭 NOTIFICATIONS；
3. 关闭 PROACTIVE；
4. 停止/暂停新增 Proactive Job（如后续确有新增）；
5. 保留审计和事件 state，避免删除证据；
6. 按精确文件清单恢复上一版；
7. 验证 Gateway PID/lifecycle、Telegram polling、四个既有 Job、Executor heartbeat；
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
| Windows Executor 文件 | 不在 v1 改造范围 | 未修改 |

## 11. 资源与版本策略

- v1 默认不升级 Hermes；先用 VPS v0.19.0 已证实的 Cron/no-agent/Gateway 能力。
- v0.21.3 现有新能力（如更完整的 monitor/webhook/全局暂停能力）另立升级评估，不在本计划偷渡。
- 不把线上当前 gpt-5.6-luna 的存在理解为 Proactive Core 必须使用 LLM。
- 健康巡检 LLM 调用预算为 0；目标候选默认最多 1/day。
- 新增状态应为小型 SQLite/JSON 索引，先设保留期限；不建立大日志/向量库。
- 资源评估以 canary 实测为准，保留 VPS 内存和 Gateway 稳定性余量；不在设计阶段假定“肯定够”。

## 12. Git 与分支状态

用户要求的目标分支是 codex/proactive-core-v1-design。检查结果显示 D:\Codex Projects\IdeaForge 根目录没有 Git 元数据；仅存在若干嵌套项目仓库，不能把其中任一仓库冒充 IdeaForge 根仓库。

因此本阶段：

- 没有初始化根仓库；
- 没有创建伪分支；
- 没有提交或合并；
- 文档直接写入用户指定的 IdeaForge 工作区；
- 后续若要真正使用该分支，应由用户先指定准确的 Git 仓库边界，再做文档-only 分支操作。

这不是生产阻塞，但必须在审查记录中明确，不能声称分支已创建。

## 13. 本计划的停止条件

任何一个条件出现，都停在当前阶段并报告：

- 生产状态无法以正确 HERMES_HOME 复核；
- 发现未授权的 Webhook/Hook/Executor 路径；
- Event Store 无法保证幂等；
- 模型/通知预算没有硬上限；
- HIGH 风险缺少 ASK 闸门；
- 无法证明旧业务 Job 未受影响；
- Gateway/Telegram/Executor heartbeat 出现回归；
- 用户要求扩大到 Cloudflare、OpenViking、升级、重启或外部发布；
- ChatGPT 尚未审核设计却要求进入实施。

当前停止在 GATE-PROACTIVE-CORE-DESIGN-REVIEW。

## 14. 审核后执行范围与强制停止点

ChatGPT 设计 Gate 已通过，但附带强制修订。Phase 1–2 仅在隔离目录 proactive-core-v1/ 中实现和测试，输入仅为本地脱敏 JSON fixture；不读取 VPS 输入、Hermes 生产目录或 GOALS.md，不接 Telegram、Executor、OpenViking、Hook、Cron 或其他外部适配器。

Phase 1 必须验证：Event schema 与生命周期、SQLite 事务/权限/版本、policy fixtures、fingerprint 去重、恢复和再失败、severity escalation、过期、并发 tick、提交前崩溃、动作已开始但结果未提交、delivery failure、数据库锁、畸形事件、隐私脱敏、HIGH action、ACT+NOTIFY、预算、熔断及关闭开关。

Phase 2 必须证明：相同输入的 fingerprint/decision 稳定；重复运行不增加通知候选；恢复只产生一次；空输入 stdout 为空；健康路径 LLM calls 为 0；实际通知和动作均为 0。Shadow 输出只能来自固定 renderer，不能输出 Event/audit JSON。

完成后生成 PROACTIVE-CORE-PHASE1-2-REPORT.md，记录全部验收字段、测试证据、生产边界和 PROACTIVE_CORE_GIT_REPO。随后只读核实 VPS 当前 active provider/model 及其来源，不修改模型；最后停在：

GATE-PROACTIVE-CORE-PRE-PRODUCTION-REVIEW = WAITING_FOR_CHATGPT

该 Gate 未通过前，禁止进入 Phase 3 VPS Production Canary、生产分支、Telegram 主动通知或任何生产 ACT。IdeaForge 根目录 Git 状态保持 PROACTIVE_CORE_GIT_REPO = UNRESOLVED，不得初始化仓库或借用嵌套仓库。

### Phase 1–2 完成记录

PROACTIVE-CORE-PHASE1-2-REPORT.md 已生成；本地 unittest 结果为 33/33 PASS。只读 VPS 状态显示 OpenAI Codex / gpt-5.6-luna，与 default profile 配置字段匹配；HERMES_PROFILE 未在 systemd 环境中显式设置。生产未变更，Phase 3 未开始，当前 Gate 等待 ChatGPT。
