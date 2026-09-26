# HERMES-PROACTIVE-CORE-V1：设计审查与闸门记录

> 审查用途：交给 ChatGPT 做独立设计审核。  
> 审查日期：2026-09-17（Asia/Singapore）  
> 生产变更边界：本阶段没有生产变更、部署、重启、升级、通知开关调整或数据写入。

> 状态说明：第 1–9 节保存初始设计审核快照。ChatGPT 后续结论及强制修订见第 10 节；第 10 节是当前有效 Gate 状态。

## 1. 闸门状态

~~~text
PROACTIVE_CORE_RESEARCH = PASS
PROACTIVE_CORE_DESIGN = READY
PRODUCTION_CHANGED = NO
IMPLEMENTATION_STARTED = NO
GATE_PROACTIVE_CORE_DESIGN_REVIEW = WAITING_FOR_CHATGPT
~~~

这表示调研和设计材料已准备好，但不表示实现、部署或生产启用已经获批。

## 2. 建议的设计决策

### 推荐

采用 B：Hermes 原生 Cron + no-agent 探针 + 轻量 Proactive Core。

核心边界：

1. VPS-first；不新增常驻 daemon。
2. 确定性探针先行，模型只做少量排序/摘要。
3. Event、Policy、Action、Notification、Audit 分层。
4. silent by default；空 stdout 才是静默。
5. HIGH 默认 ASK，不能被模型或开关绕过。
6. Goal Progress Engine 只生成 NEXT_ACTION_CANDIDATE，不自动推进无限任务。
7. Cloudflare = LATER；公网 Webhook、Temporal、n8n、OpenViking 写入、Brief 均延后。
8. 任何实施都按 shadow → canary → 通知 → 低风险动作的顺序推进。

### 不推荐

- 只靠 Cron prompt 充当策略层；
- 每 120 分钟启动一次独立 Agent 并期待它自行判断所有事情；
- 把 Hermes /goal 的 session continuation 当成跨重启工作流；
- 先搭外部事件系统再寻找需求；
- 把当前 JSON 探针输出误称为“静默主动系统”；
- 将 Windows Executor、OpenViking、Telegram 和 Proactive Core 绑定成一个不可回滚的大变更。

## 3. 本次实际核验的证据

| 证据 | 结果 | 对设计的影响 |
|---|---|---|
| VPS Gateway/systemd | hermes-gateway active/running；MainPID 51717；NRestarts=0 | 保持现有 Gateway，不新增 daemon |
| VPS Hermes 版本 | v0.19.0（虚拟环境内确认） | v1 以已证实能力为基线；升级另行审批 |
| VPS Cron | 4 个启用 Job，全部 no-agent/script | 可复用调度，但必须修正静默/输出语义 |
| proactive-review-v1 最近执行 | Mode: no_agent (script)，输出完整 JSON | 当前是探针/报告，不是决策层 |
| proactive_monitor.py | 6149 bytes；无 [SILENT]；无 LLM client | P0 缺口：增加统一 policy/静默路径 |
| proactive 状态 | 有 last_run、last_notification_signature/type 等轻量键 | 需要事件索引、fingerprint、cooldown 和 reason |
| Kanban/Cron | 采样 active task=0、error_count=0；有历史 incidents | 历史事件和当前告警必须分开 |
| Telegram | Gateway connected，现有 Job 已使用 delivery | 只复用现有受控通道，不开新通知面 |
| Webhook | 生产 CLI 报告 platform not enabled | v1 不开公网入口 |
| Hooks | 未发现用户 shell hook；源码显示 Gateway hook 机制 | 先不启用 Hook，未来作为事件来源 |
| Windows Executor | 是桥接，不是 Gateway | 维持 Windows Hermes retirement 边界 |
| OpenViking/KnowledgeVault | weekly 链路独立且已有降级标记 | 不并入 v1 事件动作 |

## 4. 评审必须回答的问题

请 ChatGPT 审核并明确回答：

1. Event 最小字段是否足以支持去重、冷却、恢复、审计和跨重启？
2. IGNORE/SILENT/ACT/NOTIFY/ASK/URGENT 的边界是否有重叠？
3. LOW/MEDIUM/HIGH 的动作分类是否需要补充“外发消息永远 ASK”规则？
4. 每日 3 条通知、每 tick 1 个动作、LLM 0/1 次的预算是否合理？
5. Goal Progress Engine 是否应在 v1 完全关闭，只保留设计？
6. 是否有任何理由在 v1 前升级 Hermes 到 v0.21.3？
7. 哪些 Hook/Webhook 事件是第一批安全可接入的？
8. 空 stdout、delivery failure、model failure 的状态语义是否足够清楚？
9. Event Store 用 SQLite 还是 JSON 更合适？保留期限应是多少？
10. 未来若引入 Cloudflare，触发条件是否足够具体？

## 5. 风险审查

| 风险 | 等级 | 当前控制 | 是否允许越过设计闸门 |
|---|---|---|---|
| 重复 Telegram 通知 | MEDIUM | fingerprint/cooldown/budget 设计；当前线上仍有 JSON 直投递问题 | 只有 shadow 通过后 |
| 高风险自主动作 | HIGH | 默认 ASK；actions flag 独立关闭 | 未通过策略 fixture 不允许 |
| 模型误判/幻觉 | HIGH | 规则先行；模型无最终权限；circuit breaker | 不允许直接 ACT |
| Cron 历史错误误报 | MEDIUM | 区分 active failure/recovered/history | 需 fixture 验证 |
| Gateway 重启/版本漂移 | HIGH | 不升级、不重启；正确 HERMES_HOME 复核 | 本阶段不越过 |
| Event Store 损坏/锁竞争 | HIGH | 熔断、只读探针、停止动作 | 需中断恢复测试 |
| Windows Executor 越权 | HIGH | 不改协议、不直接任意执行 | v1 默认不接动作 |
| 敏感信息进入事件 | HIGH | payload_ref、最小读取、脱敏 | 需隐私 fixture |
| 公网 Webhook 暴露 | HIGH | v1 明确关闭 | 不纳入 v1 |
| 资源/费用超预算 | MEDIUM | hard budget；健康巡检 0 LLM | 需 canary 测量 |

## 6. 生产与工作区变更记录

### 已发生

- 写入了 4 份设计/审计 Markdown 文档到 D:\Codex Projects\IdeaForge。
- 写入了 1 份互联网调研 HTML 到 D:\Codex Projects\Files。
- 执行了只读本地文件检查。
- 执行了只读 VPS SSH 采样。
- 执行了 Hermes 官方文档、源码、Issue 和同类系统调研。

### 未发生

- 没有改任何 VPS 文件或配置。
- 没有创建/修改 Cron、Hooks、Webhook 或 Telegram。
- 没有写入 OpenViking、Memory、KnowledgeVault。
- 没有改 Windows Executor、浏览器、cookies、tokens 或 API keys。
- 没有改 Cloudflare、模型/provider、Hermes 版本或 Gateway。
- 没有实现 proactive_core 代码。
- 没有启动新服务、重启 Gateway、停止现有服务或发生产通知。
- 没有初始化 IdeaForge 根目录 Git 仓库。

### Git 分支说明

请求目标分支为 codex/proactive-core-v1-design。D:\Codex Projects\IdeaForge 根目录没有 .git；目录下的嵌套仓库属于其他项目，不能安全地用来承载本次根目录文档。因此本次没有伪造分支、初始化仓库、提交或合并。后续若必须使用该分支，需要先确定准确仓库边界。

## 7. 允许进入下一阶段的条件

只有同时满足以下条件，才可从设计审查进入离线实现：

- ChatGPT 对本文件做出明确审核结论；
- 用户明确批准进入阶段 1；
- 事件字段、决策状态、风险表和预算被接受；
- 明确哪些生产文件未来可改、哪些永远不改；
- 先有离线 fixture 和回滚方案；
- 不把“设计 READY”误解为“生产 ENABLED”。

进入 VPS canary 还需要额外的生产授权、精确文件备份、前后快照和停止门。

## 8. 参考文档

- [现有能力与只读审计](<D:\Codex Projects\IdeaForge\PROACTIVE-CORE-EXISTING-CAPABILITIES.md>)
- [v1 设计说明](<D:\Codex Projects\IdeaForge\PROACTIVE-CORE-V1-DESIGN.md>)
- [分阶段实施计划](<D:\Codex Projects\IdeaForge\PROACTIVE-CORE-V1-IMPLEMENTATION-PLAN.md>)
- [互联网调研 HTML](<D:\Codex Projects\Files\PROACTIVE-CORE-RESEARCH-20260917.html>)

## 9. 最终停止点

~~~text
PROACTIVE_CORE_RESEARCH = PASS
PROACTIVE_CORE_DESIGN = READY
PRODUCTION_CHANGED = NO
IMPLEMENTATION_STARTED = NO
GATE_PROACTIVE_CORE_DESIGN_REVIEW = WAITING_FOR_CHATGPT
~~~

在收到 ChatGPT 设计审核前，本项目不进入实现、部署或生产启用。

## 10. ChatGPT 审核结论与当前执行 Gate

ChatGPT 审核结果：DESIGN_GATE = APPROVED_WITH_REQUIRED_CHANGES。

### 已批准范围

- Phase 1：离线 Event/Policy 合约、SQLite Event Store 与测试。
- Phase 2：本地 fixture-only Shadow Detector。

### 明确未批准

- Phase 3 VPS Production Canary。
- 任何 Telegram 主动消息或生产 ACT。
- 修改当前生产 provider/model、Hermes、Gateway、Cron、Hook、GOALS.md、OpenViking、Executor 或其他生产配置/状态。
- 初始化 IdeaForge 根目录 Git 仓库，或把嵌套仓库冒充项目中心。

### 强制修订

Event 生命周期增加 status / resolved_at / recovery_of；Decision、Action、Notification/Delivery 独立追加审计记录；六种高层决策保留并拆分 action 与 notification 两个维度，支持 ACT + NOTIFY。用户本人现有 Telegram 会话中的系统通知不要求 ASK；代表用户向第三方外发内容默认 HIGH / ASK，v1 不实现 standing authorization 例外。

Event Store 正式使用 SQLite，JSON 仅用于 fixture/export；启用事务、唯一约束、crash-safe commit、schema/policy version、仅当前用户访问权限及 30/90 天保留规则；禁止存 secrets、cookies、token 和完整私聊正文。stdout、Delivery 状态、LLM 限权、普通/紧急预算、Goal candidate-only、三个默认关闭的 kill switch 及 Shadow 验收条件按设计文档第 15 节执行。

### 强制停止点

Phase 1–2 结束时必须生成 PROACTIVE-CORE-PHASE1-2-REPORT.md，再只读核实 VPS 当前 Hermes active provider/model 与其配置来源；不得修改模型。历史审计值 gpt-5.6-luna 与此前目标 deepseek-v4-flash 只作为对照，不预设当前运行值。

最终停止为：

GATE-PROACTIVE-CORE-PRE-PRODUCTION-REVIEW = WAITING_FOR_CHATGPT

当前项目根目录 Git 边界保持 PROACTIVE_CORE_GIT_REPO = UNRESOLVED。在 ChatGPT 审核该 Gate 及用户明确决定仓库边界前，不进入 Phase 3。

### Phase 1–2 完成状态

离线实现与本地 Shadow 已完成，33/33 测试通过。报告记录所有强制字段和 VPS 只读核验。当前运行值为 OpenAI Codex / gpt-5.6-luna；来源核对与 profile 选择边界见报告。未改生产模型；deepseek-v4-flash 仅作为先前目标及其他配置项值保留。

因此当前 Gate 已从设计审核转为：

GATE-PROACTIVE-CORE-PRE-PRODUCTION-REVIEW = WAITING_FOR_CHATGPT

ChatGPT 审核通过并由用户决定 Git 仓库边界前，停止于此。
