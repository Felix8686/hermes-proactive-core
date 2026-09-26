# HERMES-PROACTIVE-CORE-V1：现有能力与只读审计

> 审计日期：2026-09-17（Asia/Singapore）  
> 审计边界：VPS Hermes 生产链路、现有 Windows Executor 桥接、IdeaForge 中的设计材料，以及 Hermes 官方文档/源码/Issue。  
> 操作边界：本阶段只读；没有修改生产配置、Cron、Hooks、Webhook、Telegram、OpenViking、Memory、KnowledgeVault、Windows Executor、Cloudflare、模型、Hermes 版本或 Gateway。

## 1. 结论先行

当前 Hermes 已经具备“定时唤醒、脚本检测、隔离 Agent 会话、Telegram 投递、持久目标循环、生命周期 Hook、入站 Webhook 接口”等零散能力，但线上还没有一个统一的 Proactive Core。

当前 VPS 上的 proactive-review-v1 实际是：

~~~
Cron ticker
  -> no-agent script
  -> proactive_monitor.py
  -> JSON stdout
  -> Cron 直接投递
~~~

它是一个确定性的状态探针和输出源，不是“事件标准化 → 风险判断 → 去重/冷却 → 决策 → 执行 → 通知”的决策层。最关键的现场证据是：

- 线上 Job 为 no-agent；执行记录明确标为 Mode: no_agent (script)。
- 线上 proactive_monitor.py 不包含 [SILENT] 输出分支，也没有 LLM 客户端调用。
- 最近一次输出是完整 JSON 检测报告，而不是空 stdout 或静默标记。
- 所以当前 Job prompt 中关于“无新事项则输出 [SILENT]”的意图，没有在 no-agent 脚本路径中实现。

结论：v1 不需要再造一个常驻自主 Agent；应在现有 Hermes Cron/no-agent 能力之上加一层很薄的、可关闭的 Proactive Core。推荐方案是 B：原生能力 + 轻量 Proactive Core。

## 2. 生产侧现状快照

### 2.1 VPS Gateway

本次通过已有只读 SSH 目标采样，未执行任何远程写入、重启或部署。

| 项目 | 现场结果 |
|---|---|
| Hermes 运行版本 | v0.19.0，发布日期 2026-07-20 |
| 运行方式 | systemd user service，hermes-gateway.service |
| Gateway | active/running；采样到 MainPID 51717；NRestarts=0 |
| Telegram | Gateway state 显示 connected |
| 当前模型 | gpt-5.6-luna；仅用于能力盘点，不代表本阶段要改模型 |
| 线上数据根 | /home/mzer8/hermes-shadow/data |
| Hermes 官方最新版本 | 本次审阅到 v0.21.3（2026-09-14）；需要另行审批升级，v1 设计不依赖升级 |

版本判断以 VPS 虚拟环境内的 Hermes 包和正在运行的 Gateway 为准；不能把 Windows 本地的 v0.21.2 当成 VPS 生产版本。官方发布页见 [Hermes Agent releases](https://github.com/NousResearch/hermes-agent/releases)。

### 2.2 线上 Cron 清单

当前线上有 4 个启用 Job，均为 no-agent/script 模式，且最近状态采样均为 ok：

| Job | 调度 | 模式 | 当前职责 | 与 Proactive Core 的关系 |
|---|---:|---|---|---|
| fav-daily-sync | 每日 09:00 | no-agent | 通过 Windows Executor 做收藏同步 | 业务自动化，不应被 Proactive Core 重复触发 |
| knowledge-weekly-consumption | 每周日 20:00 | no-agent | 生成单主题 KnowledgeVault 证据包 | 业务自动化；保留 degraded 标记 |
| proactive-review-v1 | 每 120 分钟 | no-agent | 检查 Kanban、Cron、现有状态 | 当前唯一“主动巡检”入口，但还不是决策层 |
| hermes-update-check | 每周一 10:00 | no-agent | 更新检查 | 独立生命周期 Job，不应由 v1 重新包装成重复提醒 |

线上 Cron 的输出语义是脚本 stdout 直投递；空 stdout 才能表达静默。当前 proactive-review-v1 输出 JSON，因此会产生“每次运行都有输出”的结构性风险。

### 2.3 当前 proactive_monitor.py 的实际能力

只读审阅线上脚本后，确认其核心探针为：

1. 只读读取 Kanban SQLite，统计状态、活动任务和评论数量。
2. 读取 Cron incidents；没有表时从 executions 的失败状态派生错误。
3. 读取启用 Job、最近状态、failure streak、delivery error。
4. 读取 proactive/state.json 中的上次通知签名和类型。
5. 以稳定排序的 JSON 输出结果。

它当前不负责：

- 把不同来源转成统一 Event；
- 计算 IGNORE、SILENT、ACT、NOTIFY、ASK、URGENT；
- 做跨运行的事件去重、冷却、聚合或升级；
- 读取并推进 GOALS.md 中的目标；
- 调用 LLM 做语义判断或通知摘要；
- 执行独立动作；
- 产生一个可恢复的任务状态机。

线上脚本本次采样的元数据为：6149 字节；SHA-256 为 371590b44c5d490431a560f13acaf87d6eadd2eb610bc0202184680fab4d30a5；没有 [SILENT] 字面量，没有 openai/anthropic/litellm 客户端引用。最新输出文件显示 Mode: no_agent (script)，内容从 JSON 检测结果开始。

### 2.4 生产数据与已有边界

| 资产 | 现场状态 | 处理原则 |
|---|---|---|
| GOALS.md | 存在 G1/G2/G3 和 Hermes cognitive system phase 2 目标 | v1 只读取并生成候选下一步，不自动改写目标 |
| proactive/PROACTIVE_RULES.md | silent by default；不重复生命周期事件；不确定则通知、不自动行动 | 作为策略基线，未来要落成可测试的规则 |
| proactive/state.json | 有 last_run、last_silent_check、last_notification_signature/type 等键 | 当前状态不足以支撑多事件去重与审计，需要未来新增隔离状态表 |
| Kanban | 当前巡检采样 active_task_count=0、comments=0 | 允许“无事件”成为正常结果，不能将探针 JSON 当通知 |
| Cron incidents | 采样到 4 条历史 incident，当前 error_count=0；4 个 Job failure_streak=0 | 历史 incident 不等于当前告警；恢复应单独建事件 |
| Telegram | 现有 Gateway 已连接并用于既有 Cron 投递 | v1 只复用现有投递，不新增任意外发通道 |
| KnowledgeVault/OpenViking | weekly 任务已有证据门、状态和降级标记 | 不把知识消费链路并入 v1 核心状态机 |
| Windows Executor | 保留为轻量桥接，不恢复 Windows Hermes | 只有明确、低风险、已授权的动作才可被未来策略引用 |

## 3. Hermes 原生能力矩阵

“已部署”只表示本次现场看到的生产状态；“可用”不表示已经接入 Proactive Core。

| 能力 | VPS v0.19.0 | 官方较新版本/文档 | VPS 当前是否部署 | v1 判断 |
|---|---|---|---|---|
| Gateway ticker 与 Cron | 支持 | [Cron docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron) | 是 | 直接复用 |
| no-agent/script-only | 支持 | [Script-only Cron](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/cron-script-only.md) | 4 个 Job 均使用 | 作为确定性探针和 dry-run 入口 |
| Telegram delivery | 支持 | [Gateway internals](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-internals) | 是 | 复用现有目标；通知仍受预算与风险策略控制 |
| 持久 Goal loop | v0.19 源码存在；session-scoped | [Persistent Goals](https://hermes-agent.nousresearch.com/docs/user-guide/features/goals) | 不是后台 Proactive Engine | 只复用“目标上下文/候选下一步”概念 |
| Gateway hooks | 支持 startup/session/agent/command 事件 | [Hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks) | 未发现用户 Hook | 可作为未来事件来源，先不启用 |
| Shell hooks | CLI/目录机制存在 | 同上 | hermes hooks list 报告未配置 | v1 不依赖 |
| Inbound webhook | CLI 能力存在 | [Webhooks](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks) | Webhook platform is not enabled | v1 不开公网入口 |
| Outbound webhook | v0.19 本次未确认部署 | 官方 Hooks 文档有描述 | 未部署 | 延后 |
| BOOT.md | 不是 v0.19 内建能力；是官方 Hook recipe | [BOOT.md pattern](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks) | 未部署 | 不作为隐藏启动逻辑 |
| Background/delegation | 有会话级后台能力 | [Delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation) | 未作为 Proactive Engine 使用 | 不能当作重启后可恢复工作流 |
| SessionDB/state.db | 有会话与 Goal 状态 | [Sessions](https://hermes-agent.nousresearch.com/docs/user-guide/features/sessions) | 是 | 不等于统一事件存储 |
| Checkpoint | 有可选快照能力 | [Checkpoints](https://hermes-agent.nousresearch.com/docs/user-guide/features/checkpoints) | 未接入 proactive | 不替代任务状态机 |
| Cron monitor-script/url | 本次 v0.19 CLI 未见 | 当前源码/文档可见相关能力 | 未部署 | 升级差异，不能提前假定 |
| 全局 pause | 当前 v0.19 顶层 CLI 未见 | 当前 Cron 文档已描述 | 未确认可用 | 先用 Job 级开关和 feature flag 设计 |

一个重要兼容性事实：在未设置正确 HERMES_HOME 时，VPS CLI 会给出“没有 Job/Telegram 未配置”等误导性结果。所有生产判断必须以 systemd 环境和远程数据根为准，不能用默认用户目录的单次 CLI 输出替代。

## 4. Windows 与其他已有链路

### 4.1 Windows Executor

已有材料和现场边界一致：VPS Cron → queue → 强制 SSH → Windows Executor → Windows 登录/OpenCLI → queue → KnowledgeVault。Executor 是轻量桥接，不是 Gateway；cookies、tokens、API keys 不复制到 VPS。

因此本阶段不做以下动作：

- 不启动或恢复 Windows Hermes；
- 不让 Proactive Core 直接访问浏览器会话；
- 不把 Windows Executor 改成通用远程执行器；
- 不把 Executor heartbeat 误判为 Hermes Gateway heartbeat。

相关背景材料：[WINDOWS-EXECUTOR-REPORT.md](<D:\Codex Projects\IdeaForge\_remediation_stage\WINDOWS-EXECUTOR-REPORT.md>)、[WINDOWS-HERMES-RETIREMENT-INVENTORY.md](<D:\Codex Projects\IdeaForge\_remediation_stage\WINDOWS-HERMES-RETIREMENT-INVENTORY.md>)。

### 4.2 本地 Windows Hermes

本机 D:\Hermes\data 曾存在 v0.21.2 Gateway、3 个 disabled Cron Job、proactive state 和 GOALS。它是本地运行时，不是 VPS 生产状态。本阶段只把它当作设计材料和边界参考，不修改也不重新启用。

## 5. 相似架构可借鉴点

| 系统 | 已验证的设计点 | 对 v1 的启示 |
|---|---|---|
| OpenClaw | heartbeat 是主会话的系统自动化；独立任务记录与 heartbeat 分离；事件唤醒有频率保护 | “周期感知”与“可恢复任务”必须分层，避免把每次 heartbeat 当成新任务；见 [Heartbeat](https://docs.openclaw.ai/heartbeat) |
| Home Assistant | trigger 唤醒；condition 决定是否执行；action 执行；trigger ID 可关联不同路径；trace 记录每次执行 | Event、Policy、Action、Trace 应有清晰边界；见 [Triggers](https://www.home-assistant.io/docs/automation/trigger)、[Conditions](https://www.home-assistant.io/docs/automation/condition/)、[Troubleshooting/Trace](https://www.home-assistant.io/docs/automation/troubleshooting/) |
| n8n | Webhook 有测试/生产 URL、认证、IP allowlist、条件过滤和执行记录 | 将公网入口、鉴权、过滤、执行记录分开设计；见 [Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/)、[Security audit](https://docs.n8n.io/hosting/securing/security-audit/) |
| Temporal | Workflow 负责确定性状态；失败易发的 Activity 单独重试；服务保证崩溃后从历史恢复 | 未来如需长流程，应引入真正的持久工作流；v1 不把 Hermes Goal loop 冒充 Temporal |
| GitHub Actions | 事件触发 workflow；concurrency key 防止同一资源并行；workflow_run 连接前后流程 | dedupe key、锁、并发上限、恢复事件应成为一等字段；见 [Events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)、[Concurrency](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency) |

## 6. 社区样本与限制

已打开并可核验的社区样本：

- Reddit：一个用户用每日 Cron 检查 Hermes 上游更新，只有有价值变化时才通知，脚本无变化时返回 [SILENT]。它说明“小探针 + 高信号输出”比持续 Agent turn 更适合健康检查；这是用户实践，不是官方保证。来源：[The cron job every serious Hermes Agent user should probably have](https://www.reddit.com/r/hermesagent/comments/1t9gz2f/the_cron_job_every_serious_hermes_agent_user_should_probably_have/)。
- V2EX：一个个人 Agent 工作流用发布任务 + watchdog，watchdog 检查 jobs.json/agent.log 识别运行、卡住和失败；回复集中提醒权限、配额和审计。来源：[搭建平时的个人 Agent 是怎么搭建的？](https://fast.v2ex.com/t/1227393)。
- 抖音：Hermes 使用视频展示 Cron 的创建、测试、列表、暂停/恢复/运行/删除，反映出用户期待的是可观察、可控的任务生命周期。来源：[为什么hermes不会定时干活](https://www.douyin.com/shipin/7630622000599435290)。

本次未完成的社区平台：

| 平台 | 状态 | 原因 |
|---|---|---|
| Reddit | 完成 | 已打开公开主题全文 |
| X | 未完成 | X 页面打开失败，OpenCLI 无法创建本地配置目录 |
| V2EX | 完成 | 已打开公开主题全文 |
| 知乎 | 未完成 | 目标页面返回 Internal Error，未把搜索摘要当作正文证据 |
| 小红书 | 未完成 | 没有得到可核验笔记；OpenCLI 本地目录权限失败 |
| 抖音 | 完成 | 已打开公开视频页面 |

联网检索的完整范围、失败记录、来源链接和模型签名见 [PROACTIVE-CORE-RESEARCH-20260917.html](<D:\Codex Projects\Files\PROACTIVE-CORE-RESEARCH-20260917.html>)。

## 7. 现有能力的缺口分级

### P0：必须在 v1 设计中正面处理

1. no-agent 脚本 stdout 与“静默”语义不一致，可能造成重复通知。
2. 没有统一 Event ID/fingerprint，无法可靠去重、冷却、聚合和恢复。
3. 历史 incident、当前 error、恢复事件没有统一状态模型。
4. 高风险动作没有统一的 ASK/授权闸门。
5. Goal loop 只有会话范围，不能代表跨重启、跨来源的长期主动任务。
6. 现有 proactive state 过于简化，不能解释“为什么通知/为什么不通知”。

### P1：可以在 v1.1 或 v2 处理

1. 公网 Webhook、外部事件接入和签名校验。
2. Cloudflare 边缘入口、队列或 Durable Object。
3. Morning Brief/Evening Brief 的跨来源聚合。
4. 真正的长流程恢复、补偿、死信和人工审批队列。
5. 面向多个用户/多个 Agent 的策略隔离。

## 8. 审计结论

现有 Hermes 原生能力足以承载一个小型、VPS-first、可关闭的 Proactive Core v1；不需要现在升级 Hermes，也不需要现在引入 Cloudflare、n8n、Temporal 或独立常驻 Agent。

最小安全落点是：

1. 保留现有 4 个业务 Cron 的职责边界。
2. 让 proactive-review-v1 先变成“探针 → 统一 Event → 策略计算”的 dry-run/shadow 路径。
3. 把空 stdout 真正留给 SILENT，把可通知内容变成明确的 NOTIFY/ASK/URGENT。
4. 所有 ACT 先限于低风险、可逆、已列明的动作；高风险默认 ASK。
5. 先保留生产 Gateway 和 Telegram 的现状，单独设置 Proactive 开关、通知开关和动作开关。

本文件只记录能力与证据，不授权任何实现或生产变更。
