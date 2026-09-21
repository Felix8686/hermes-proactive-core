# Proactive Core Phase 4 Notification Report

Date: 2026-09-21

Repository: `Felix8686/hermes-proactive-core`

Branch: `codex/proactive-core-v1`

Scope: controlled, owner-only Telegram notification canary. ACT, LLM-assisted behavior, third-party messaging, Windows execution, and main-branch integration were out of scope.

## Result

Phase 4A and the Phase 4B scheduled observation completed with the required notification-only safety invariants intact. The observation covered 34 real executions of the existing `proactive-review-v1` 120-minute no-agent Cron, which is greater than the required three consecutive executions.

No natural allowlisted production event occurred during Phase 4B. Therefore Phase 4B produced no additional Telegram notification, while the Phase 4A synthetic delivery remained the only successful delivery.

## Phase 4A evidence

- The preflight used the production `HERMES_HOME` and verified the actual Hermes user-scoped Gateway service was active, Telegram was connected, and the recent Telegram journal had zero `conflict`, `Unauthorized`, and `ConnectError` matches.
- Exactly one owner-only synthetic test delivery was recorded. Its notification history is `PENDING -> SENT`; `SENT` was recorded only after the Telegram adapter reported success.
- The exact synthetic Event replay was suppressed inside cooldown. The notification-candidate count remained 1, the Event count remained 1, and no second delivery was recorded.
- The target was taken from the existing Hermes-controlled delivery configuration; no Event payload supplied a target. No target identifier is included in this report.

## Phase 4B scheduled evidence

- Existing job: `proactive-review-v1` (`c5f3e6e4ac27`), active, `every 120m`, `no_agent`, script `proactive_core_v1_runner.py`.
- First observed post-fix output: `2026-09-18_14-34-17.md` (VPS local time).
- Latest observed output: `2026-09-21_08-35-35.md` (VPS local time).
- Real scheduled output records: 34.
- Every inspected output had a valid Hermes no-agent envelope, and the extracted runner body was 0 bytes.
- No-event stdout was therefore empty for every inspected scheduled execution; no audit/debug JSON was delivered to Telegram.
- At final read-only verification, the job status was `ok`, the last run was `2026-09-21T08:35:35.841403+08:00`, and the next run was `2026-09-21T11:42:42.884088+08:00`.

## Runtime and privacy evidence

- Actual Hermes Gateway service: user-scoped `systemctl --user`, `active/running`, `MainPID=150738`, `NRestarts=0`.
- The system-scoped unit is inactive/dead because production Hermes is running under the user-scoped unit; it is not the active production service.
- Telegram state from production state was `connected`; recent Gateway journal counts were `conflict=0`, `unauthorized=0`, `connecterror=0`.
- OpenViking health endpoint returned HTTP 200.
- Active Cron job text contained zero Windows Executor, `opencli`, SSH-to-Windows, or Windows-dependency references.
- `PROACTIVE_ACTIONS_ENABLED=false`; no action candidate was executed.
- No provider/model configuration was changed and no model was called.
- The state database and Phase 4 configuration were mode `0600`; no secrets, cookies, tokens, or private-chat body text were added to the report.

The error audit file contains two historical `runtime_error` rows at `2026-09-18T04:35:12.466065Z` and `2026-09-18T06:34:17.263370Z`. They are from the earlier reconciliation/runtime-diagnostic attempts before the stable post-fix observation sequence; no additional error rows were present at final verification.

## State-store evidence

Final SQLite counts:

- `events`: 1
- `event_observations`: 2
- `decisions`: 1
- `notification_candidates`: 1
- `notification_transitions`: 2
- `action_candidates`: 0
- `action_transitions`: 0
- `llm_outcomes`: 0

The one notification candidate is the Phase 4A synthetic delivery. Phase 4B added no candidate and sent no message.

## Hash evidence

- Reviewed Phase 4 implementation commit: `124a07cc08bb400be0020fb73a9bb0fc7b03b837`.
- Normalized `proactive-review-v1` definition SHA-256: `c87a74129f73ab5f0c3290b8debe5493165b2efea8b4e918c2ef69a481b59ca5`.
- Current Cron jobs file SHA-256: `8cd6ca0216754dceb7e1ead7ec619fdcae50cc7c69f28e41ac3e3da179bd14f`.
- Proactive runner SHA-256: `9f6255bc3479124b430d197deead3d67d97c811de80de9812540ad75e7a40875`.
- Phase 4 module SHA-256: `e231cedf24e18c09f2c203ede165abe7eed9ffd4761c5cd0d7f483f398489333`.
- Proactive configuration SHA-256: `a50f721087b4de2ee522bf613f797b5dde00c37bfe4b6f6910fab4b32745969e`.
- SQLite state-store SHA-256: `b7538720bb3f30ef756ba5254c98180809548454974fe926477707c24b50e25a`.

The normalized Proactive job definition remained unchanged. During final health verification, an initial system-scope service read incorrectly showed the unused system unit as inactive; the required safety response briefly paused only `proactive-review-v1`. A user-scope read immediately confirmed the actual Gateway was healthy, so that same job was restored to active. No Cron was manually run, no other job was paused or changed, and no Telegram/LLM/ACT operation occurred. The resumed job has the next-run timestamp recorded above; this operational note is intentionally retained for ChatGPT review.

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

Additional counters:

```text
PHASE4A_TELEGRAM_MESSAGES_SENT = 1
PHASE4B_TELEGRAM_MESSAGES_SENT = 0
PHASE4B_NOTIFICATION_CANDIDATES_ADDED = 0
PROACTIVE_ACTIONS_ENABLED = false
PROACTIVE_NOTIFICATIONS_ENABLED = true
PROACTIVE_ENABLED = true
```

## Gate

`GATE_PROACTIVE_CORE_GOAL_REVIEW = WAITING_FOR_CHATGPT`

STOP. Do not proceed to Goal Progress, Briefs, ACT, Hooks, Webhooks, or any LLM-assisted proactive behavior without a new ChatGPT review.
