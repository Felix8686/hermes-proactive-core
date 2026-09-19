# Phase 4 handoff — Controlled Telegram notification canary

## Gate decision

Phase 3 Canary has been reviewed and accepted with one limitation: Gateway process health was stable, but Telegram-connected baseline was not independently confirmed during the Canary window.

This handoff authorizes **notification-only Phase 4**, not ACT.

Current architecture remains VPS-only.

Repository: `Felix8686/hermes-proactive-core`
Branch: `codex/proactive-core-v1`
Base reviewed commit: `bb5b3c88982b09b06573f68bcbd41f74410617b5`

Do not merge `main`.

## Phase 4 objective

Prove that Proactive Core can deliver a very small number of deterministic, deduplicated, useful notifications to the owner's existing Hermes Telegram conversation without enabling autonomous actions or LLM decision-making.

The desired behavior is:

real read-only probe
→ Event Normalizer
→ deterministic Policy
→ dedupe / cooldown / budget
→ controlled Renderer
→ existing Telegram delivery
→ delivery audit

No event / duplicate / cooldown hit
→ empty stdout / no Telegram message

## Hard safety invariants

For the entire Phase 4:

- `PROACTIVE_ACTIONS_ENABLED=false`
- `ACTIONS_EXECUTED=0`
- `LLM_CALLS=0`
- no model/provider change
- no public webhook/listener
- no Windows dependency
- no change to OpenViking, KnowledgeVault, Memory, Cloudflare, D1 or sing-box
- no new Hermes profile
- no arbitrary external message targets
- no third-party outbound messaging
- no production service restart unless a narrow code reload is technically unavoidable and explicitly documented
- no merge to `main`

Only the existing owner Telegram target already used by Hermes may be used.

## Required preflight

Before any Proactive notification is sent:

1. Read-only confirm `hermes-gateway.service` is active.
2. Read-only confirm Telegram platform state is `connected` from the actual production `HERMES_HOME`.
3. Confirm no current Telegram conflict / Unauthorized / ConnectError condition.
4. Confirm the active owner delivery target from existing Hermes-controlled configuration; do not accept chat_id from an Event payload.
5. Confirm `PROACTIVE_ACTIONS_ENABLED=false`.
6. Confirm no Windows Executor source or dependency exists.

If Telegram connected state cannot be confirmed, STOP before sending and report the exact blocker.

## Phase 4A — one synthetic delivery test

Run exactly one controlled manual delivery through the same notification adapter intended for production.

The message must be visibly marked as a test, for example:

`【Proactive Core 测试】通知链路测试成功；这不是异常告警。`

Requirements:

- exactly one test message;
- owner Telegram only;
- no LLM;
- no ACT;
- record Event/Decision/Notification candidate and delivery result;
- delivery state must become `SENT` only after the Telegram adapter reports success;
- a delivery failure must remain `FAILED`, never masquerade as sent.

After the first message, replay the exact same synthetic Event inside the active cooldown window. It must produce no second Telegram delivery.

Required:

`PHASE4A_SINGLE_DELIVERY = PASS`
`PHASE4A_DUPLICATE_SUPPRESSION = PASS`

If either fails, STOP and rollback Phase 4 notification enablement.

## Phase 4B — deterministic production notifications only

After Phase 4A passes, allow Proactive Core notifications for a very narrow allowlist of deterministic event types.

Initial allowlist:

- `cron.failure`
- `cron.recovered`
- `openviking.unhealthy`
- `openviking.recovered`
- `proactive.circuit_breaker_open`

Do not include:

- Goal suggestions
- Morning/Evening Brief
- semantic recommendations
- project prioritization
- model-generated summaries
- arbitrary system logs
- historical incidents that are not current
- Windows events

The policy must remain deterministic.

## Production integration rule

Prefer the smallest integration into the existing `proactive-review-v1` path.

Do not create a second competing scheduler if the existing 120-minute no-agent job can safely host:

probe
→ normalize
→ policy
→ render

The existing raw JSON probe output must not continue to be delivered to Telegram.

Expected stdout contract:

- no notify-worthy event → empty stdout
- one or more allowlisted notify-worthy events → one short aggregated controlled message
- audit/debug JSON → state store/file only, never Telegram stdout

Do not modify unrelated business Cron jobs.

If production integration requires changing a Cron definition, back up the exact file first, record SHA-256 before/after, and change only the Proactive job.

## Notification controls

Phase 4 defaults:

- ordinary notification budget: max 3/day
- same fingerprint: cooldown applies
- recovery: max one notification per corresponding failure lifecycle
- aggregation: multiple allowlisted events in one tick should become one concise owner message when possible
- delivery failure does not consume a successful-delivery count
- circuit breaker may send at most one owner notification for entering paused state, then suppress repeats

URGENT is not enabled as a separate bypass category in Phase 4. Treat all Phase 4 notifications under the ordinary owner budget.

## No ACT

Phase 4 does not authorize any autonomous action.

Even if policy would theoretically choose ACT:

- action adapter remains absent/disabled;
- record only an action candidate if needed for audit;
- execute nothing.

`ACTIONS_EXECUTED` must remain 0.

## No LLM

Phase 4 notification rendering must use deterministic templates.

Do not call gpt-5.6-luna or any other model.

`LLM_CALLS` must remain 0.

## Observation window

After production notification-only integration passes manual tests, observe at least:

- 3 consecutive proactive-review scheduled executions, or
- 6 hours,

whichever yields the required three scheduled executions.

During the window, Hermes may continue normal user work.

Collect:

- Gateway PID / NRestarts
- Telegram connected state
- Proactive job execution status
- Proactive notification count
- duplicate suppression count
- Event Store health
- OpenViking health
- production Cron definition hash
- Proactive code/config hashes
- LLM calls
- actions executed
- delivery failures

Do not induce a real production outage merely to get a notification.

If no natural failure occurs, that is acceptable. Synthetic failure/recovery behavior was already validated in Phase 3; Phase 4 primarily validates real delivery, silence, scheduling and regression behavior.

## Rollback

At any point, rollback order:

1. set/restore `PROACTIVE_NOTIFICATIONS_ENABLED=false`;
2. preserve Event Store/audit evidence;
3. restore the exact previous proactive-review entry/script/config from backup if modified;
4. verify Telegram normal chat still works;
5. verify Gateway active and existing business Cron jobs unchanged.

Do not delete audit evidence as part of rollback.

## Required report

Generate and commit:

`PROACTIVE-CORE-PHASE4-NOTIFICATION-REPORT.md`

Required final fields:

```text
PHASE3_BASE_ACCEPTED = PASS / FAIL
TELEGRAM_PREFLIGHT = PASS / FAIL
PHASE4A_SINGLE_DELIVERY = PASS / FAIL
PHASE4A_DUPLICATE_SUPPRESSION = PASS / FAIL
PROACTIVE_NOTIFICATION_INTEGRATION = PASS / FAIL
RAW_JSON_TELEGRAM_OUTPUT_REMOVED = PASS / FAIL
SILENT_EMPTY_STDOUT = PASS / FAIL
NOTIFICATION_BUDGET = PASS / FAIL
COOLDOWN_DEDUPE = PASS / FAIL
RECOVERY_NOTIFICATION = PASS / NOT_OBSERVED_WITH_REASON / FAIL
DELIVERY_AUDIT = PASS / FAIL
LLM_CALLS = 0 / NONZERO
ACTIONS_EXECUTED = 0 / NONZERO
WINDOWS_DEPENDENCY = NONE / FOUND
GATEWAY_REGRESSION = PASS / FAIL
TELEGRAM_REGRESSION = PASS / FAIL
CRON_REGRESSION = PASS / FAIL
OPENVIKING_REGRESSION = PASS / FAIL
PHASE4_NOTIFICATION_CANARY = PASS / FAIL
GATE_PROACTIVE_CORE_GOAL_REVIEW = WAITING_FOR_CHATGPT
```

Then STOP.

Do not proceed to Goal Progress, Briefs, ACT, Hooks, Webhooks, or any LLM-assisted proactive behavior without a new ChatGPT review.
