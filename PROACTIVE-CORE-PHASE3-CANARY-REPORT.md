# Proactive Core Phase 3 Canary Report

日期：2026-09-17（Asia/Singapore）

项目仓库：`Felix8686/hermes-proactive-core`

分支：`codex/proactive-core-v1`

Draft PR：[#1](https://github.com/Felix8686/hermes-proactive-core/pull/1)
本次交接基线：`2dbc24c145922905646369ef6232a1d595452d71`

## 结论

Phase 1–2 实现已在 VPS-only normalization 后重新完整测试，33/33 PASS。随后在 VPS 单独的私有目录运行了手工 Phase 3 Canary dry-run；没有接入 Hermes Cron/Gateway，没有部署常驻服务，没有修改生产配置或在 Canary 窗口写生产文件。通知、动作开关始终为 false，未调用 LLM、未发送 Telegram、未执行动作，也未访问 Windows。

重复/恢复路径的第一份证据脚本把所有 append-only audit 行数也要求完全不变，因正常新增 decision/observation 而出现两条过严断言的 false-negative。原始证据未改写；随后在全新隔离 SQLite 状态库中做了独立多进程复核，按事件数、候选数和 recovery link 验证，全部通过。具体见“证据校正说明”。

## 必需状态字段

```text
PHASE1_2_IMPORTED_TO_GITHUB = PASS (import commit c77a55f; branch codex/proactive-core-v1)
VPS_ONLY_NORMALIZATION = PASS
ACTIVE_JOBS_DEPENDING_ON_WINDOWS_EXECUTOR = 0
ACTIVE_CRON_DEPENDING_ON_WINDOWS_EXECUTOR = 0
ACTIVE_SKILLS_DEPENDING_ON_WINDOWS_EXECUTOR = 0
REPO_TESTS = PASS (33/33)
CANARY_DEPLOYMENT = PASS (isolated manual VPS process; no persistent install)
PRODUCTION_FILES_CHANGED = 3 before Canary for narrow normalization; 0 during Canary
PRODUCTION_SERVICES_RESTARTED = NO
LLM_CALLS = 0
TELEGRAM_MESSAGES_SENT = 0
ACTIONS_EXECUTED = 0
DEDUPE_PERSISTENCE = PASS (fresh-process SQLite recheck)
RECOVERY_HANDLING = PASS (fresh-process SQLite recheck)
KILL_SWITCHES = PASS
GATEWAY_REGRESSION = N/A (Telegram-connected baseline was not independently confirmed; process state stayed stable)
CRON_REGRESSION = PASS
OPENVIKING_REGRESSION = PASS
WINDOWS_DEPENDENCY = NONE
PROACTIVE_CORE_GIT_REPO = RESOLVED
MAIN_MERGED = NO
PHASE3_CANARY = PASS_WITH_RECHECK
GATE_PROACTIVE_CORE_NOTIFICATION_REVIEW = WAITING_FOR_CHATGPT
```

## Phase 1–2 import and tests

- Phase 1–2 implementation is present on the user-selected branch; import commit `c77a55f` is an ancestor of the handoff baseline.
- The 17 implementation/test files transferred to the VPS isolated copy matched the source tree by relative path and SHA-256. The VPS Canary used that copy without changing its implementation files.
- Full repository test command, run after normalization and before Canary: `python -B -m unittest discover -s tests -v` from `proactive-core-v1/`.
- Result: **33/33 passed**. The generated local Python bytecode cache was removed; no code file was changed after the test run.
- Phase 1–2 behavior includes deterministic policy, privacy filtering, SQLite transactions, dedupe/recovery, risk gates, budgets, circuit breaker, default-off switches, and empty stdout on silent input.

## VPS-only normalization and dependency audit

只读复核统计为 4 个 Hermes Cron Job、其中 3 个 enabled；active Jobs、系统 crontab/active timers、active Skills 中不存在 Windows Executor 运行依赖。进程扫描也未发现 Windows Executor 进程。单个 Skill 命中 `skills/note-taking/vault-personal-records/SKILL.md:36` 是 fail-closed 守卫：若操作依赖 Windows-only Executor，应停止当前写入并报告；它不要求或调用 Executor，因此不计为依赖，且保持原样。

残留分类与处理：

- **ACTIVE**：未发现依赖 Windows Executor 的 active Job、Cron、Skill 或运行进程。唯一 Skill 命中是上述 fail-closed 守卫，保留。
- **STALE**：已禁用的 `fav-daily-sync` Prompt 仍有旧 Executor 提示；该 Job 保持 disabled，Cron 定义未改。三个未被 enabled Job 引用的 dormant VPS 脚本，仅把旧错误标签/提示替换为 VPS CLI unavailable 提示，没有改动选择或 dispatch 逻辑。
- **HISTORICAL**：`cron/output/ad749dc02631` 下 4 个历史输出文件及既往审计/设计记录保留，未清理。

三个脚本变更均在 Canary 前完成并逐文件备份。VPS 备份目录为 `/home/mzer8/hermes-shadow/data/migration/proactive-core-vps-only-normalization-20260917T131000Z`，目录权限 `0700`，备份文件和 manifest 权限 `0600`；原文件 SHA-256 与备份逐一匹配：

| 文件 | 备份原 SHA-256 | Canary 前规范化后 SHA-256 |
|---|---|---|
| `scripts/fav-enrich.py` | `00656cfd2e23342df00561363e3873fef6af054967cf4a07f5c6a864913bef06` | `b5d12ff8f1aeed499979ed70ea9bbb1ec85f724a08e386f879b3ea4b45ab98c4` |
| `scripts/x-bookmark-sync.py` | `baf79c4a6a928ded5c0fa0cdf6eb87b09f46bb47ef612a88c13359619ee06009` | `5de1bc6151fabe78c8b33493e34a992e82930df91573a012844b3020dcf7337b` |
| `scripts/zhihu-fav-sync.py` | `e33a92a0870f59d5211ee6747d5dc5129abcf37aa7728f997356f7e715b22639` | `5855cc11be0f4097e0392419b5183416c676660e2a15ddefc7fba2fb17ec1952` |

`cron/jobs.json` 未变更，SHA-256 为 `811f65cd7a6d083782b231d578f1d7fd0c3a27b8bd6bbdbd34a4ac247c515193`。active default profile 配置哈希在 Canary 前后相同；当时只读运行状态为 **OpenAI Codex / gpt-5.6-luna**，本阶段未修改当前 Hermes 模型/provider。

## Canary 范围与结果

运行副本位于 VPS `/home/mzer8/proactive-core-canary/phase3-20260917-run2/`，与活动 Hermes 数据目录分离。运行目录和 SQLite 状态目录为 `0700`，数据库及证据文件为 `0600`。没有修改 Cron、Gateway、OpenViking、Hermes 配置、生产事件库或 systemd；没有重启生产服务。

仅通过每个隔离 Shadow 子进程的显式环境临时设置 `PROACTIVE_ENABLED=true`；`PROACTIVE_NOTIFICATIONS_ENABLED=false`、`PROACTIVE_ACTIONS_ENABLED=false`。父进程环境未改变，systemd manager/Gateway 未持久化任何 `PROACTIVE_*` 开关。所有子进程输出均由隔离父进程捕获，没有接入 Cron stdout 或 Telegram delivery。

- **VPS 真实只读摘要**：采集 Cron/Job、Gateway、Kanban/proactive monitor 摘要；脱敏快照没有发现待处理事件，生成空事件 fixture。相同快照运行两次均为空 stdout，没有事件或通知候选增长。
- **默认关闭/Core disabled**：未提供开关及显式 `PROACTIVE_ENABLED=false` 两种运行均为 exit 0、stdout 为空、未创建对应状态库。
- **Synthetic failure/重复**：首个独立进程只输出一行受控 renderer 文本并产生一个通知候选；新进程重复输入后 stdout 为空，事件与候选保持各一条。
- **Recovery/重复**：恢复事件只关联并解决一个失败事件；新进程重复恢复后仍只有一个 recovery link、一个 recovery 事件及一个恢复候选。
- **SQLite lock/fail-closed**：持有隔离数据库写锁时运行返回 exit 2、stdout 为空；释放锁后核对没有部分 Event/候选写入。
- **外部副作用**：通知 `SENT` 转移数 0；Action candidates/transitions 0；LLM outcomes 0。没有 delivery adapter 或 ACT executor 被调用。

主 Canary 状态库最终为 2 个 synthetic Event（failure 与 recovery，均已 resolved）、2 个 shadow notification candidate、0 个 `SENT`、0 个 action candidate/transition、0 个 LLM outcome。候选不是消息投递，不能解释为用户已收到。

### 证据校正说明

原始证据 `/home/mzer8/proactive-core-canary/phase3-20260917-run2/CANARY-EVIDENCE.json` SHA-256：`c600f300f1f9a8f80e924046910af8c1f7b83371366a5f41e5fe123b55ea3193`。其中两项初始重复断言比较了整个 SQLite 计数快照；重复观测会按设计追加 observation/decision，所以全表计数增长，导致过严比较返回 false。原文件保持未改写。

之后在新的隔离状态库中单独复核并保存 `/home/mzer8/proactive-core-canary/phase3-20260917-run2/CANARY-EVIDENCE-RECHECK.json`，SHA-256：`fd77256068bee69a203897a18824bf8ec18544b10411abf3f5b41e0eaed86bec`。该复核确认：失败重复前后 `events=1`、`notification_candidates=1`；恢复重复前后 `events=2`、`recovery_links=1`、`notification_candidates=2`。append-only observations/decisions 正常递增。复核 9 项全部 PASS，通知/动作开关仍分别为 false。

## 生产前后回归快照

- **Gateway**：前后均 `active/running`，PID 与 `NRestarts` 不变（0）。Telegram connected 状态未能独立确认，故 `GATEWAY_REGRESSION=N/A`；这里只能确认进程级状态稳定，不宣称 Telegram 连接健康。
- **Cron**：定义哈希、enabled 数量（3/4）与 Windows 依赖计数前后相同；未修改 Job 或调度配置，`CRON_REGRESSION=PASS`。
- **OpenViking**：服务前后 active，PID 与 `NRestarts` 不变（0）；`127.0.0.1:19333/health` 前后 HTTP 200，`OPENVIKING_REGRESSION=PASS`。
- Canary 前后核对的生产 Cron、模型/profile 配置及三个已规范化脚本哈希完全相同；Canary 窗口内生产文件改动数为 0。

## Gate

未合并 `main`，未开启 Telegram 主动通知，未开启 ACT，未修改当前 Hermes 模型，也未恢复 Windows Executor。最终停止于：

`GATE_PROACTIVE_CORE_NOTIFICATION_REVIEW = WAITING_FOR_CHATGPT`

STOP，等待 ChatGPT 审核本报告。
