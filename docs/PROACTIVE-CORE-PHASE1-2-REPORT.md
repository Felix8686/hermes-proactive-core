# HERMES-PROACTIVE-CORE-V1：Phase 1–2 验收报告

日期：2026-09-17（Asia/Singapore）  
设计审核：APPROVED_WITH_REQUIRED_CHANGES  
实施范围：离线 Event/Policy 合约与本地 fixture-only Shadow Detector  
实现目录：proactive-core-v1/

## Gate 状态

Phase 1 与 Phase 2 已完成。本报告只记录离线实现、测试与只读 VPS 核验；没有部署或生产写入。

EVENT_SCHEMA = PASS  
SQLITE_STATE_STORE = PASS  
POLICY_FIXTURES = PASS  
DEDUPE = PASS  
RECOVERY = PASS  
ACT_NOTIFY_SPLIT = PASS  
RISK_GATE = PASS  
PRIVACY_FIXTURES = PASS  
BUDGET = PASS  
CIRCUIT_BREAKER = PASS  
KILL_SWITCHES = PASS  
SHADOW_DETECTOR = PASS  
LLM_CALLS_IN_HEALTH_PATH = 0  
PRODUCTION_CHANGED = NO  
TELEGRAM_MESSAGES_SENT = 0  
ACTIONS_EXECUTED = 0  
PROACTIVE_CORE_GIT_REPO = UNRESOLVED  
GATE-PROACTIVE-CORE-PRE-PRODUCTION-REVIEW = WAITING_FOR_CHATGPT

## 实施与验证证据

- Event 合约包括 OPEN/RESOLVED/EXPIRED、resolved_at、recovery_of、UTC 时间、schema_version 与 policy_version。Fingerprint 确定性生成并校验；condition 使用受限状态 token，subject 为资源标识符。
- SQLite 使用 WAL、synchronous=FULL、BEGIN IMMEDIATE 事务、OPEN fingerprint 与 recovery parent 唯一约束。Event observation、Decision、Action candidate/transition、Notification candidate/delivery transition 与可选 LLM outcome 分表追加记录。
- Event Store 对未解决事件不作保留期清理；resolved/expired 在 30 天后清理；aggregate metrics 在 90 天后清理。POSIX 使用 0700 目录与 0600 数据库；本机 Windows 已验证目录/数据库 ACL 仅授予当前用户。
- 普通通知预算为 3/day；URGENT 独立受 2/hour、5/day、fingerprint cooldown 与 escalation 保护。跨 UTC 日期的 cooldown 有测试。
- 普通 owner 通知不要求 ASK；第三方外发为 HIGH/ASK。HIGH 决策不能由 ACTIONS_ENABLED 自动授权。ACT 与 NOTIFY 可在同一 Decision 中并存。
- SILENT 输出为空；Shadow 的 NOTIFY/ASK/URGENT 预览只能由固定 renderer 产生。Event/audit JSON 不进入 stdout。Shadow 即使收到通知/动作开关为 true 的配置，也没有发送或执行适配器。
- LLM helper 只接受 NEXT_ACTION_CANDIDATE、SEMANTIC_RANKING、SHORT_SUMMARY 字段；超时、不可用、非法 JSON 返回无副作用错误码并可追加记录。Shadow 健康路径没有 LLM callable。
- Goal progress helper 只返回 SILENT、NOTIFY_CANDIDATE、ASK_CANDIDATE；没有读取或修改 GOALS.md，也没有 /goal 或任务派生路径。

测试命令：

    python -B -m unittest discover -s tests -v

结果：33/33 tests passed。覆盖首次/重复/恢复/恢复后再失败、severity escalation、过期与重复过期、并发 tick、SQLite 锁、事务回滚、子进程在 commit 前 abrupt exit、action STARTED 后缺失结果、delivery failure、malformed event、隐私脱敏、HIGH action、ACT+NOTIFY、预算耗尽、URGENT rate/cooldown、熔断、kill switches、空 stdout 与 Shadow 重复运行。

Shadow 验收：相同输入在相同 tick 时间下 fingerprint 与 policy decision 相同；重复运行不新增 notification candidate；recovery candidate 只创建一次；无事件 stdout 为空；健康路径 LLM calls=0；notification delivery transitions=0；Telegram messages=0；action transitions/execution=0。

## VPS 当前模型：只读核验

核验时间：2026-09-17（Asia/Singapore）。所有命令均为只读；没有更改模型或生产文件。

- systemd user service hermes-gateway.service 的环境显示 HERMES_HOME=/home/mzer8/hermes-shadow/data；未显示 HERMES_PROFILE 覆盖。
- 在该 HERMES_HOME 下运行 VPS venv 的 Hermes status，输出当前 Model 为 gpt-5.6-luna、Provider 为 OpenAI Codex。
- 配置来源交叉核验：/home/mzer8/hermes-shadow/data/profiles/default/config.yaml 第 66–67 行为 model=gpt-5.6-luna、provider=openai-codex；/home/mzer8/hermes-shadow/data/config.yaml 也包含 provider=openai-codex。配置值与 Hermes status 一致。
- systemd 未设置 HERMES_PROFILE，因此 default profile 是根据服务环境与配置路径推断的来源；Hermes status 的活动 provider/model 是本次运行值的直接依据。
- 此前目标 deepseek-v4-flash 仍出现在其他模型/provider 配置项中，但不等于当前活动值。本阶段没有对该值做任何修改。

## 边界、差异与下一步

- 没有 VPS Canary、部署、服务重启、Cron/Hook 修改、Telegram 主动发送、外部内容投递或生产 ACT。
- 测试中的 ActionAttempt 仅用于验证 PLANNED→STARTED→UNKNOWN_AFTER_RESTART 审计；没有真实动作执行。
- IdeaForge 根目录 .git 不存在；没有初始化仓库、借用嵌套仓库、创建分支或提交。PROACTIVE_CORE_GIT_REPO 保持 UNRESOLVED。
- 下一步只能由 ChatGPT 审核本报告及 Gate。当前停止在 GATE-PROACTIVE-CORE-PRE-PRODUCTION-REVIEW = WAITING_FOR_CHATGPT；Phase 3、Telegram 主动通知和任何生产 ACT 均未获准。
