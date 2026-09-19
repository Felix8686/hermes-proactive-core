# Proactive Core Phase 4 Notification Report

- Repository: `Felix8686/hermes-proactive-core`
- Branch: `codex/proactive-core-v1`
- Observation completed: 2026-09-19
- Scope: H1 only; no Phase 5 work was started

## H1 observation evidence

The required three consecutive real `proactive-review-v1` builtin scheduled executions were already present and were reused; no extra run and no synthetic Telegram message was induced:

| Run | Execution source | Status | Cron output |
|---|---|---|---|
| 2026-09-19 10:35:14 +08:00 | builtin | completed | silent / empty stdout |
| 2026-09-19 12:35:15 +08:00 | builtin | completed | silent / empty stdout |
| 2026-09-19 14:35:16 +08:00 | builtin | completed | silent / empty stdout |

The three corresponding Markdown envelopes are retained in the production Cron output directory. The current job remains enabled, scheduled every 120 minutes, `no_agent=true`, `failure_streak=0`, and `last_status=ok`.

## Required final fields

```text
PHASE3_BASE_ACCEPTED = PASS
TELEGRAM_PREFLIGHT = PASS
PHASE4A_SINGLE_DELIVERY = PASS
PHASE4A_DUPLICATE_SUPPRESSION = PASS
PROACTIVE_NOTIFICATION_INTEGRATION = PASS
RAW_JSON_TELEGRAM_OUTPUT_REMOVED = PASS
SILENT_EMPTY_STDOUT = PASS
NOTIFICATION_BUDGET = PASS
COOLDOWN_DEDUPE = PASS
RECOVERY_NOTIFICATION = NOT_OBSERVED_WITH_REASON
DELIVERY_AUDIT = PASS
LLM_CALLS = 0
ACTIONS_EXECUTED = 0
WINDOWS_DEPENDENCY = NONE
GATEWAY_REGRESSION = PASS
TELEGRAM_REGRESSION = PASS
CRON_REGRESSION = PASS
OPENVIKING_REGRESSION = PASS
PHASE4_NOTIFICATION_CANARY = PASS
GATE_PROACTIVE_CORE_GOAL_REVIEW = WAITING_FOR_CHATGPT
```

## Verification details

- Gateway: `hermes-gateway.service` active/running; MainPID `150738`; `NRestarts=0`; `ExecMainStatus=0`.
- Telegram: production `gateway_state.json` reports `connected`, with no current error code/message. Normal Telegram traffic after the observation window has successful inbound and outbound log records. No new synthetic test message was sent during H1.
- OpenViking: `GET /health` returned HTTP 200 with `healthy=true`; `GET /ready` returned HTTP 200 with `status=ready`.
- Event Store: SQLite store opened and audited successfully. It contains one Phase 4A synthetic canary event, one notification candidate, one `SENT` transition after Hermes delivery success, zero action candidates/transitions, and zero LLM outcomes. The event is expired after its retention/cooldown observation; no production failure was manufactured.
- Notification/deduplication: Phase 4A has exactly one audited `SENT` delivery and no second delivery on replay. Duplicate, cooldown, budget, recovery, exact-output reconciliation, and fail-closed behavior are covered by the implementation suite. No production notification-worthy event occurred in the three observed scheduled runs, so all three were silent.
- Cron definition hash (SHA-256): `a651ede4eee94741c22b9cb55b69e21d8a99da075ca1dae70e0c82d11a5b0540` (`/home/mzer8/hermes-shadow/data/cron/jobs.json`).
- Proactive runner hash (SHA-256): `5d6ff0e88c258a83827d8d12d6e254454c4903e313416adba06a33e6e0735778` (`proactive_core_v1_runner.py`).
- Proactive package hash (SHA-256, source files excluding `__pycache__`): `0486d65ff99760c7ce64105f66c610c7c8a92c83f5e8517b79addad9c4a7b1d1` (`proactive_core/`).
- Proactive config hash (SHA-256): `a6ceb22621f17f57ccd2117b10e3434403e732355da9182bddd3a87c4c675a12`. Config confirms `PROACTIVE_ACTIONS_ENABLED=false`, `PROACTIVE_ENABLED=true`, and `PROACTIVE_NOTIFICATIONS_ENABLED=true`.
- Windows dependency: no Windows Executor or Windows path is used by the active VPS Cron integration; the active production path is Linux/VPS-only.
- Model/provider: unchanged by H1.

## Tests

`python3 -m unittest discover -s tests -v` completed successfully:

```text
Ran 52 tests in 2.262s
OK
```

One stale test fixture initially created a temporary Phase 4 config as `0644`, while the production safety contract requires a private config. The fixture was corrected to `0600`; production code was not weakened or changed.

## Commit and gate

This report and the test-fixture correction are the only H1 repository changes. No `main` merge was performed. Phase 5 remains technically reviewable but is not authorized by this handoff.

STAGE_H1_COMPLETE = YES
NEXT_STAGE_AUTHORIZED = NO
USER_DECISION_REQUIRED = YES
