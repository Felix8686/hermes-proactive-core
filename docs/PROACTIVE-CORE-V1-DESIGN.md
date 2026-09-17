# HERMES-PROACTIVE-CORE-V1：设计说明

> 原始版本：候选设计；当前生效状态与审核修订见第 15 节  
> 日期：2026-09-17（Asia/Singapore）  
> 运行原则：VPS-first、silent by default、可解释、可暂停、可回滚、默认不自主执行高风险动作。  
> 本文件不改变生产环境。

## 1. 设计目标与非目标

### 1.1 v1 目标

Proactive Core v1 只解决一个窄问题：

> 把 Hermes 已经能观察到的少量状态变化，转成低噪音、可解释、受风险约束的下一步候选；只有满足策略时才通知或执行。

目标包括：

- 统一 Cron、Gateway、Kanban、Executor heartbeat、KnowledgeVault 状态和用户目标的事件形态。
- 让 IGNORE、SILENT、ACT、NOTIFY、ASK、URGENT 成为显式决策结果。
- 提供稳定的 fingerprint、去重、冷却、聚合、恢复和升级规则。
- 让每次通知都有 reason、source、severity、dedupe_key 和 policy_version。
- 让 Goal Progress Engine 只生成 NEXT_ACTION_CANDIDATE，不自动扩展成无限任务链。
- 把模型用于少量语义排序/摘要，而不是让模型拥有最终安全权限。
- 在现有 VPS Gateway/Cron/Telegram 之上落地，不新增独立常驻服务。

### 1.2 明确不做

v1 不做：

- 恢复 Windows Hermes、旧 creator/tech/multi-Hermes；
- 修改生产模型/provider、Gateway、Cron、Webhook、Hook 或 Telegram token；
- 启用公网 Webhook；
- 将 OpenViking/KnowledgeVault 变成主动执行目标；
- 自动发布、自动发送外部消息、删除数据、付款、升级、改权限；
- 引入 Cloudflare、Temporal、n8n 或新的消息队列；
- 把 Hermes /goal、/bg、delegation 当成长流程持久执行引擎；
- 用新 Brief 重复现有 fav、knowledge、update-check 的通知。

## 2. 推荐形态：B，原生能力 + 轻量 Proactive Core

### 2.1 三个候选

| 方案 | 内容 | 优点 | 主要风险 | 结论 |
|---|---|---|---|---|
| A：纯原生 Cron + Hooks + Webhook | 继续拼装现有能力，策略分散在 prompt/script/config | 改动少，容易开始 | 没有统一事件、去重、风险、审计；当前 no-agent 已暴露 stdout/静默缺口 | 不足以满足 v1 |
| B：原生能力 + 轻量 Core | no-agent 做确定性探针；Core 做 Event/Policy/State；Cron 负责 tick/delivery；必要时一次小 LLM 摘要 | 复用现有运行面，边界清楚，成本低，可逐层开关 | 需要新增小型状态与测试；必须防止 Core 变成隐藏 Agent | 推荐 |
| C：独立事件系统 + Hermes Agent | 公网入口/队列/常驻 worker/Agent orchestrator 全部独立 | 长期可扩展、可接更多来源 | 资源、权限、故障域和运维复杂度明显上升；与现有 2GB VPS 和 v1 目标不匹配 | v2 以后再评估 |

### 2.2 推荐数据流

~~~
现有探针/Hook/受控入口
       │
       ▼
Event Normalizer
       │ 统一字段、脱敏、fingerprint
       ▼
Event Store + Policy Evaluator
       │ 记录 DETECTED/DECIDED/RESOLVED
       ├── IGNORE / SILENT
       ├── ACT（仅 LOW + 显式 allowlist）
       ├── NOTIFY（现有 Telegram delivery）
       ├── ASK（等待用户选择）
       └── URGENT（受限、去重后的紧急通知）
       │
       ▼
Observability / Audit / Metrics
~~~

v1 不新增常驻 daemon。一个现有 no-agent Job 可以在一次运行中完成探针、标准化、策略计算和状态提交；如果以后需要 LLM 摘要，单独调用受限的 Agent Cron/模型步骤，并把它视为可失败的辅助步骤。

## 3. Event 模型

### 3.1 最小字段

| 字段 | 必需 | 说明 |
|---|---|---|
| event_id | 是 | 稳定 UUID 或可重建 ID；用于一次观测记录 |
| event_type | 是 | 如 cron.failure、cron.recovered、goal.stale、executor.offline |
| source | 是 | cron、gateway、kanban、executor、knowledge、goal、manual |
| observed_at | 是 | UTC 时间；展示时转换为用户时区 |
| subject | 是 | 资源主体，如 Job ID、项目 ID、目标 ID |
| severity | 是 | LOW/MEDIUM/HIGH |
| fingerprint | 是 | 规范化后用于去重 |
| decision | 是 | IGNORE/SILENT/ACT/NOTIFY/ASK/URGENT |
| reason | 是 | 规则命中或模型辅助理由；不可为空 |
| requires_action | 是 | true/false |
| action_scope | 是 | none/read/status/retry-safe/user-approved/high-risk |
| related_goal | 否 | 关联 GOALS 目标的稳定引用，不存整份目标内容 |
| first_seen / last_seen | 是 | 状态窗口 |
| occurrence_count | 是 | 重复次数 |
| expires_at | 否 | 过期后不再通知/执行 |
| payload_ref | 否 | 指向受控本地证据，不把完整敏感 payload 放入事件表 |
| policy_version | 是 | 做到规则可追踪 |

### 3.2 示例

~~~json
{
  "event_id": "evt_20260917_0001",
  "event_type": "cron.failure",
  "source": "cron",
  "observed_at": "2026-09-17T00:10:00Z",
  "subject": "proactive-review-v1",
  "severity": "MEDIUM",
  "fingerprint": "cron.failure:c5f3e6e4ac27:script_exit",
  "decision": "NOTIFY",
  "reason": "同一 Job 在冷却窗口外出现新的失败，且尚未恢复",
  "requires_action": true,
  "action_scope": "read",
  "occurrence_count": 1,
  "policy_version": "pc-v1-draft"
}
~~~

事件表只存必要元数据。日志、原始输出、Telegram 内容、文件路径和用户数据采用 payload_ref 或受控摘要；默认不复制完整凭证、cookies、token、私聊正文或浏览器内容。

## 4. 决策状态与风险

### 4.1 决策状态

| 状态 | 语义 | 默认行为 |
|---|---|---|
| IGNORE | 无关、过期、非本系统主体或被明确排除 | 记录计数，不通知、不执行 |
| SILENT | 已知、已处理、重复且未升级，或没有用户价值 | 只写状态/指标，stdout 为空 |
| ACT | 低风险、可逆、allowlist 内且无需额外授权 | 执行一次，记录结果；失败转 NOTIFY/ASK |
| NOTIFY | 对用户有明确信息价值，但不需要选择 | 用短摘要投递既有 Telegram 目标 |
| ASK | 需要用户选择、授权、确认或存在中等风险 | 投递选择题/确认码，等待且有 expiry |
| URGENT | 安全、服务不可用或不可逆损失风险达到阈值 | 立即通知，但仍不能绕过高风险动作闸门 |

优先级不是“越主动越好”，而是：

1. 先阻断高风险动作；
2. 再阻断重复通知；
3. 再判断是否真的需要用户注意；
4. 最后才考虑模型摘要是否值得成本。

### 4.2 风险等级

| 风险 | 允许例子 | 不能自动做的例子 |
|---|---|---|
| LOW | 读取状态、生成摘要、标记已观察、重新计算 fingerprint、一次安全的 status probe | 任何外部发送或数据变更 |
| MEDIUM | 已有 allowlist 内的可逆 retry-safe 动作、暂停单个重复探针、生成待确认草稿 | 影响生产配置、跨系统写入、对外代表用户行动 |
| HIGH | 删除、付款、发帖/发信、改 provider/API key、升级/重启、权限/安全策略、批量执行 | 全部默认 ASK；无 ASK 不得执行 |

Telegram 外发本身属于用户明确授权的通知能力；Proactive Core 不能借“已连接 Telegram”自动扩张通知范围、目标或消息类型。

## 5. 去重、冷却、聚合与升级

### 5.1 Fingerprint

建议规范化键：

~~~
fingerprint =
  hash(source + event_type + subject + normalized_condition + scope)
~~~

不要把时间戳、随机错误文本、完整日志正文直接放入 fingerprint，否则同一问题会被误认为新事件。

### 5.2 状态规则

- 首次出现：建立 first_seen、occurrence_count=1。
- 同一 fingerprint 在冷却窗口内再次出现：只更新 last_seen/count，不通知。
- severity 上升、requires_action 从 false 变 true、subject 变更或恢复后再次失败：视为可升级的新决策。
- 出现健康状态：建立 event_type 对应的 recovered 事件；恢复通知最多一次。
- 超过 expires_at：转 SILENT/EXPIRED，不再执行。
- 同一资源并发：用 subject 级锁；旧观测不能覆盖新状态。

### 5.3 v1 默认预算

这是设计预算，不是当前生产配置：

| 预算 | 默认上限 | 说明 |
|---|---:|---|
| Proactive tick | 每 120 分钟 | 延续当前巡检频率，先不改变线上调度 |
| LLM calls/day | 0（健康巡检）；最多 1 次目标候选 | 没有语义价值时不调用模型；硬上限建议 2 |
| notifications/day | 3 | 同类聚合；URGENT 仍受安全策略和去重保护 |
| actions/tick | 1 | 一次只允许一个低风险动作；失败不自动连环重试 |
| retry attempts | 0–2 | 仅 transient、幂等、allowlist 动作；永久错误直接 ASK/NOTIFY |
| aggregate window | 15–30 分钟 | 适合把同一来源的多个失败压成一条 |

预算耗尽时默认 SILENT + 记录 budget_exhausted；只有安全/服务不可用类 URGENT 进入受限通知路径。

## 6. Goal Progress Engine（候选能力）

### 6.1 目标

Goal Progress Engine 不负责替用户创造新目标，也不自动编辑 GOALS.md。它只做：

1. 读取明确标记为 active 的目标引用；
2. 读取该目标允许的证据来源；
3. 判断是否有新进展、阻塞、等待用户决策或下一步候选；
4. 输出 NEXT_ACTION_CANDIDATE；
5. 按风险决定 SILENT、NOTIFY 或 ASK。

### 6.2 与 Hermes /goal 的边界

Hermes v0.19 的 goals.py 已有持久 Goal loop：每轮有 judge call，目标存在 SessionDB state_meta，用户消息会抢占，达到预算或失败阈值会暂停。但它是 session-scoped continuation，不是跨来源、跨重启的独立主动任务编排器。

所以 v1 采用：

~~~
GOALS.md / active goal registry
   -> evidence snapshot
   -> candidate next action
   -> policy
   -> SILENT / NOTIFY / ASK
~~~

不采用：

~~~
每个周期自动启动一个 /goal
   -> 自动继续
   -> 自动再派生更多任务
~~~

### 6.3 目标进度状态

建议的最小状态：

| 状态 | 含义 |
|---|---|
| ACTIVE | 目标明确、证据来源允许读取 |
| PROGRESS | 有可验证的新结果 |
| WAITING_USER | 缺少选择/授权/输入 |
| BLOCKED | 外部依赖或权限阻塞 |
| STALE | 超过目标的检查窗口，没有新证据 |
| COMPLETE | 有明确完成证据；不再主动重复 |

目标状态转移也必须有 event_id、reason、evidence_ref 和 observed_at。

## 7. 中断、恢复与幂等

### 7.1 核心原则

- “进程退出”不等于事件丢失；提交 Event 前后采用可识别的状态。
- “任务重复运行”不等于可以重复动作；action 必须携带 idempotency_key。
- “模型超时”不等于动作失败；模型调用和动作结果分开记录。
- “通知失败”不等于模型或探针失败；delivery outcome 单独记录。

### 7.2 建议生命周期

~~~
DETECTED
  -> CLASSIFIED
  -> DECIDED
  -> ACTED（可选）
  -> NOTIFIED（可选）
  -> RESOLVED / WAITING / EXPIRED
~~~

每次进程启动时只重放未完成的低风险状态记录；HIGH/MEDIUM 动作不在启动时自动重试，必须转 ASK 或人工复核。未来若需要强持久工作流，应单独评估 Temporal 类系统，而不是把这套轻量状态扩展成隐形队列。

## 8. 通知与 Telegram 交互

### 8.1 通知等级

| 等级 | 内容 | 默认 |
|---|---|---|
| INFO | 状态变化/恢复/有价值的新信息 | 聚合后通知或 SILENT |
| ATTENTION | 用户应在近期处理的阻塞/失败 | NOTIFY |
| ASK | 需要用户选择或授权 | NOTIFY + expiry |
| URGENT | 安全或关键服务风险 | 立即通知；动作仍受闸门 |

短通知模板：

~~~
【ATTENTION】<一句话结论>
来源：<source>/<subject>
变化：<new state>
建议：<one next step>
依据：<event_id> · <observed_at>
回复：<确认码/选项>（如需）
~~~

### 8.2 ASK 的约束

- 每个 ASK 有唯一短 token、创建时间、expiry 和允许动作列表。
- 回复只能匹配当前用户、当前目标和未过期 token。
- 过期回复转 SILENT/ASK_AGAIN，不执行旧请求。
- 文本含糊、身份不匹配、重复回复一律不执行。
- v1 不新增公网 Webhook；优先复用现有 Telegram inbound/session 机制，具体绑定在实施前另行核验。

## 9. 模型使用策略

### 9.1 先规则，后模型

确定性规则先完成：

- 状态读取；
- fingerprint；
- severity；
- dedupe/cooldown/budget；
- 是否允许动作；
- 是否需要用户授权。

模型只可作为：

- 多个已允许候选的语义排序；
- 生成短摘要；
- 从 GOALS 证据中提炼 NEXT_ACTION_CANDIDATE。

模型不能：

- 直接决定 HIGH 动作；
- 直接改配置、执行任意命令或发送任意外部消息；
- 用“模型说应该做”绕过 cooldown、budget、ASK 或 circuit breaker。

### 9.2 成本与稳定性

- 健康巡检默认 0 LLM。
- 目标候选默认关闭；打开后每日最多 1 次。
- 模型失败、超时、解析失败不应阻塞确定性探针；转 SILENT/NOTIFY/ASK 取决于是否产生了可解释事件。
- 连续解析失败 3 次或传输失败 5 次进入模型 circuit breaker；在人工复核前只保留确定性路径。
- 现有 Hermes /goal 的 judge call 不计入 Proactive Core 预算，除非未来明确把它接入并单独标注。

## 10. Circuit breaker、开关与可观察性

### 10.1 三个独立开关

未来实现建议至少有：

| 开关 | 默认 | 作用 |
|---|---|---|
| PROACTIVE_ENABLED | false | 总闸门；关闭后不计算/不执行主动路径 |
| PROACTIVE_NOTIFICATIONS_ENABLED | false | 只控制新通知，不影响 dry-run 记录 |
| PROACTIVE_ACTIONS_ENABLED | false | 只控制 ACT；HIGH 永不因该开关自动放行 |

现阶段不写入这些生产开关；这里只是后续实施接口。

### 10.2 Circuit breaker 条件

任一条件满足时，打开熔断：

- 单 tick 超过 action/LLM/通知预算；
- 同一 fingerprint 在短窗口内异常爆发；
- delivery 连续失败；
- Event Store 无法安全提交；
- policy version 不兼容；
- 观察到 state corruption、锁竞争或时间漂移；
- 进程内存/运行时异常达到阈值。

熔断后：停止 ACT，保留确定性观测，最多发一条“Proactive Core 已暂停/需复核”的通知；不得自我修改配置或自我重启。

### 10.3 可观察性字段

每次 tick 至少输出/记录：

- run_id、started_at、finished_at、duration_ms；
- source_count、event_count、new_event_count；
- decisions_by_state；
- notification_count、action_count、llm_calls；
- error_count、delivery_failure_count；
- policy_version、schema_version；
- circuit_breaker_state；
- 失败原因分类，而不是完整敏感输出。

## 11. 隐私、安全与权限

- 采用最小读取范围：只读 Kanban/Cron/heartbeat/目标摘要；未经明确授权不读浏览器会话、私聊正文或任意磁盘。
- Event Store 默认 0600/等效权限，保留期限短于原始日志；payload_ref 指向受控证据。
- 所有跨系统动作以 allowlist + action_scope + idempotency_key 保护。
- Windows Executor 只接受既有协议允许的任务，Proactive Core 不直接注入任意命令。
- Telegram 目标必须从现有受控配置解析，不能由事件 payload 提供任意 chat_id。
- 不把 API key、cookies、OAuth token、SSH 私钥、完整 Telegram 内容写入事件或研究文档。
- 任何涉及生产升级、Gateway 重启、Webhook 公网暴露、OpenViking 写入、配置/权限变更的动作均停在 ASK/人工审批。

## 12. Cloudflare 判断

结论：USE_CLOUDFLARE = LATER。

v1 不需要 Cloudflare 的原因：

- 当前主动巡检是 VPS 内部定时状态检查，不需要公网入口。
- 现有 Gateway/Cron/Telegram 已提供最短闭环。
- 引入边缘入口会同时引入签名、重放、租户/来源隔离、队列、故障转移和新的审计面。
- 2GB VPS 需要先验证 Proactive Core 的事件量和通知价值，而不是先扩大架构。

只有在以下需求被确认后才进入 Cloudflare 评估：

1. VPS 不在线时仍需接收事件；
2. 需要公开但强鉴权的多来源 Webhook；
3. 需要 Durable Object/Queue 级别的跨重启事件协调；
4. 需要多个 Agent/用户的边缘隔离。

## 13. v1 范围

### 纳入

- Cron/Gateway/Executor/Kanban 的只读状态事件；
- GOALS 只读摘要与 NEXT_ACTION_CANDIDATE；
- Event schema、fingerprint、去重、cooldown、budget；
- dry-run/shadow 输出；
- LOW 风险、明确 allowlist 的动作接口设计；
- Telegram 短通知/ASK 格式设计；
- lifecycle audit 和 circuit breaker；
- VPS-first、无独立 daemon。

### 延后

- 公网 Webhook；
- Morning/Evening Brief；
- 自动写入 KnowledgeVault/OpenViking；
- 自动发布/自动发送外部消息；
- 多 Agent 统一编排；
- Cloudflare/Temporal/n8n；
- Hermes 升级迁移。

## 14. 设计验收标准

在任何生产开关打开前，必须通过：

1. Event fixture 覆盖首次、重复、恢复、升级、过期、并发和未知输入。
2. policy fixture 证明 HIGH 永远不会自动 ACT。
3. no-agent 空 stdout 真正表示 SILENT；非空输出包含可解释的决策。
4. 模型不可用时，确定性路径仍能完成并留下证据。
5. 进程中断后不会重复执行带副作用的动作。
6. Telegram 投递失败不会伪造“已通知”。
7. 预算、熔断、关闭开关均有离线测试。
8. 线上 canary 有明确的前后快照、回滚命令和停止门。

本文件是设计候选，不是生产实施授权。

## 15. ChatGPT 审核后的强制修订（当前生效规范）

ChatGPT 审核结论为 DESIGN_GATE = APPROVED_WITH_REQUIRED_CHANGES。仅允许离线 Phase 1 与本地 Shadow Phase 2；VPS Production Canary、Telegram 主动投递和任何生产 ACT 均未获准。本节优先于本文早期候选文字，冲突时按本节执行。

### 15.1 Event 生命周期与审计

- Event 包含 status、resolved_at、recovery_of；状态为 OPEN、RESOLVED、EXPIRED。
- Event 本体不是完整审计历史。Event observations、Decision、Action candidate/transition、Notification candidate/delivery transition 及可选 LLM outcome 分开记录；审计记录只追加，清理仅按保留策略执行。
- 同一 fingerprint 至多一个 OPEN Event；恢复事件最多关联并关闭一个父 Event；恢复后再次失败可建立新的 OPEN Event。

### 15.2 决策维度与外发风险

保留六种高层结果：IGNORE、SILENT、ACT、NOTIFY、ASK、URGENT。同时必须保存两个正交字段：

- action_decision: NONE / ACT / ASK
- notification_decision: NONE / NOTIFY / URGENT

必须表达 ACT + NOTIFY，即低风险动作候选与面向用户的通知候选可以并存。发往用户本人现有 Hermes Telegram 会话的系统通知不因此构成 ASK。代表用户向第三方发送 Email、Telegram、社交媒体内容、帖子或评论默认为 HIGH / ASK；仅未来显式、窄范围、可撤销的 standing authorization 才可能成为例外，v1 不实现该例外。

### 15.3 Event Store、隐私与保留

- 正式 Event Store 使用 SQLite；JSON 仅作 fixture/export。
- 写入采用事务、WAL + synchronous=FULL、fingerprint/state 唯一约束，并写入 schema_version 与 policy_version。
- 数据库权限为 POSIX 0600；Windows 使用仅当前用户可访问的等价 ACL，状态目录同样私有。
- 不持久化 secrets、cookies、token 或完整私聊正文。subject 只允许资源标识符；condition 只保留短状态 token，任意句子与正文按隐私规则丢弃或脱敏。
- unresolved Event 保留至 resolved；resolved/expired 保留 30 天；aggregate metrics 保留 90 天。

### 15.4 stdout、投递与模型失败

- SILENT 的 stdout 必须为空。
- NOTIFY / ASK / URGENT stdout 只能来自受控 renderer；Event Store、debug 和 audit JSON 不得由 Cron stdout 直投 Telegram。
- Delivery 状态独立为 PENDING / SENT / FAILED / SUPPRESSED。失败不回写 Event/Decision 正确性，也不表示用户已收到。
- LLM 仅可辅助 NEXT_ACTION_CANDIDATE、语义排序和短摘要；健康路径调用数为 0。超时、不可用或非法 JSON 不改变确定性检测/策略，不触发 ACT；记录安全错误码，有确定性模板则用模板，否则静默。

### 15.5 预算、目标候选与总闸

- 普通通知默认最多 3 条/日。
- URGENT 可越过普通预算，但仍受 fingerprint、cooldown、escalation 和 urgent-specific rate limit 约束；相同 URGENT 不得无限重发。
- Goal Progress Engine 仅允许离线/Shadow 的 SILENT / NOTIFY_CANDIDATE / ASK_CANDIDATE；不得写 GOALS.md、自动 ACT、启动 /goal 或无限派生任务。
- PROACTIVE_ENABLED、PROACTIVE_NOTIFICATIONS_ENABLED、PROACTIVE_ACTIONS_ENABLED 在代码中存在且默认全部 false。HIGH action 始终需要 ASK，不能由 actions 开关授权。

### 15.6 Shadow 与下一 Gate

Shadow 必须证明相同输入得到相同 fingerprint/decision、重复运行不增加通知候选、恢复只记录一次、无事件 stdout 为空、健康路径 LLM calls 为 0、实际通知为 0、动作执行为 0。

Phase 1–2 完成后强制停在 GATE-PROACTIVE-CORE-PRE-PRODUCTION-REVIEW = WAITING_FOR_CHATGPT。Gate 报告须只读核实 VPS 当前 Hermes active provider/model，并与审计记录 gpt-5.6-luna 及此前目标 deepseek-v4-flash 对照；不得修改模型。IdeaForge 根目录不是 Git 仓库，PROACTIVE_CORE_GIT_REPO = UNRESOLVED；确定正式仓库边界前不建立生产分支。
