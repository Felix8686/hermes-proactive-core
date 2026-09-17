# Phase 3 handoff — VPS Canary dry-run

## Source of truth

The already tested Phase 1–2 implementation exists locally at:

`D:\Codex Projects\IdeaForge\proactive-core-v1`

The acceptance report exists at:

`D:\Codex Projects\IdeaForge\PROACTIVE-CORE-PHASE1-2-REPORT.md`

Do **not** reimplement Phase 1–2 from scratch. Import the exact tested implementation into this repository branch, preserving the 33/33-tested behavior, then rerun the same test suite from the repository checkout.

## Git boundary

Repository: `Felix8686/hermes-proactive-core`

Development branch: `codex/proactive-core-v1`

Rules:

- Do not commit directly to `main` beyond the existing bootstrap commit.
- Do not merge to `main`.
- Do not commit secrets, cookies, OAuth tokens, Telegram tokens, SSH private keys, or production `.env` files.
- Keep Phase 1–2 commits separate from Phase 3 canary/deployment commits.

## Phase 1–2 import acceptance

Before any VPS deployment:

1. Copy the exact isolated implementation and tests from the local tested workspace into this branch.
2. Include the Phase 1–2 acceptance report and relevant design docs under `docs/`.
3. Run the test suite from the repository checkout.
4. Required result: all existing tests pass; no network, Telegram, action, or production adapters are active.
5. Confirm code defaults remain false:
   - `PROACTIVE_ENABLED=false`
   - `PROACTIVE_NOTIFICATIONS_ENABLED=false`
   - `PROACTIVE_ACTIONS_ENABLED=false`
6. Commit the import and record the commit SHA.

## Phase 3 scope

Phase 3 is a **VPS Canary dry-run only**.

The canary may read real production state, normalize events, evaluate policy, and write to an isolated Proactive Core SQLite store. It must not:

- send Telegram messages;
- execute ACT actions;
- call an LLM;
- modify existing Hermes Cron jobs;
- modify Gateway, OpenViking, KnowledgeVault, Memory, Cloudflare, model/provider, or systemd service configuration;
- restart production services.

Windows Executor is not part of the current architecture and must not be used as a Canary source or dependency.

The existing VPS business jobs remain unchanged unless a separate explicit instruction has already retired one of them.

## Canary switch contract — authoritative clarification

The earlier handoff incorrectly required all three runtime switches to remain false while also requiring the Shadow path to process real Canary inputs. That is internally inconsistent because the Shadow entry point correctly exits when `PROACTIVE_ENABLED=false`.

For **Phase 3 manual isolated Canary only**, the following temporary runtime state is explicitly authorized:

- `PROACTIVE_ENABLED=true`
- `PROACTIVE_NOTIFICATIONS_ENABLED=false`
- `PROACTIVE_ACTIONS_ENABLED=false`

This is not a production enablement. It applies only to the manually invoked isolated Canary process.

Hard requirements:

1. The source-code defaults in `switches.py` remain false.
2. Do not persist `PROACTIVE_ENABLED=true` into Hermes production config, systemd, Cron, shell profile, `.env`, or any startup path.
3. Set `PROACTIVE_ENABLED=true` only in the environment of the single manual Canary invocation (or an equally isolated ephemeral process environment).
4. Notifications and actions remain false for the entire Phase 3 window.
5. After each manual Canary invocation, no persistent feature-switch change may remain.
6. If the Canary cannot run under these constraints, stop and report rather than widening permissions.

## Canary deployment strategy

Prefer a narrow sidecar-style manual/isolated invocation rather than wiring the Core into the production Cron.

Required controls:

- deploy under an isolated path outside the active Hermes runtime code;
- use a separate SQLite canary DB with restrictive permissions;
- source-code defaults for all three switches remain false;
- only the isolated manual Canary process may temporarily receive `PROACTIVE_ENABLED=true`;
- read production inputs only through known read-only files/status commands;
- capture pre/post hashes for every production file that might otherwise be suspected of change;
- record Gateway PID, `NRestarts`, Telegram connected state, OpenViking health, and existing Cron status before and after each canary window;
- do not change the active Hermes model. The current `OpenAI Codex / gpt-5.6-luna` setting is intentional.

## Canary observation targets

At minimum, feed these real sources through the Shadow path where safely available:

- current VPS Cron/job status and failure/recovery state;
- Kanban active-task summary;
- Gateway health state;
- existing proactive monitor output, but treat it as probe input rather than user-facing output.

Do **not** include Windows Executor heartbeat/offline/recovery events. The current architecture is VPS-only and Windows is no longer a Hermes execution dependency.

If a source cannot be adapted without modifying production, leave it out and record that limitation. Do not widen scope to make the canary look complete.

## Canary invariants

For the entire Phase 3 window:

- `LLM_CALLS = 0`
- `TELEGRAM_MESSAGES_SENT = 0`
- `ACTIONS_EXECUTED = 0`
- production Cron definitions unchanged
- production Gateway remains active
- no additional Hermes profile is created
- no model/provider change
- no new public webhook or listener
- Windows remains outside the active Hermes architecture

## Required evidence

Run enough observations to cover at least:

- no-change/silent input;
- duplicate event suppression;
- one synthetic or naturally occurring failure-like fixture mapped through the same deployed canary code without touching production state;
- recovery handling;
- restart of the canary process itself, proving persisted dedupe/state survives process restart;
- SQLite lock/fail-closed behavior;
- Core disabled behavior (`PROACTIVE_ENABLED=false` must still fail closed with empty output/no processing).

Natural production failures must not be induced merely for testing.

## Stop gate

At the end of Phase 3, generate:

`PROACTIVE-CORE-PHASE3-CANARY-REPORT.md`

Required final fields:

- `PHASE1_2_IMPORTED_TO_GITHUB = PASS / FAIL`
- `REPO_TESTS = PASS / FAIL`
- `CANARY_DEPLOYMENT = PASS / FAIL`
- `PRODUCTION_FILES_CHANGED = 0 / NONZERO`
- `PRODUCTION_SERVICES_RESTARTED = 0 / NONZERO`
- `LLM_CALLS = 0 / NONZERO`
- `TELEGRAM_MESSAGES_SENT = 0 / NONZERO`
- `ACTIONS_EXECUTED = 0 / NONZERO`
- `DEDUPE_PERSISTENCE = PASS / FAIL`
- `RECOVERY_HANDLING = PASS / FAIL`
- `KILL_SWITCHES = PASS / FAIL`
- `GATEWAY_REGRESSION = PASS / FAIL / NOT_APPLICABLE_WITH_REASON`
- `CRON_REGRESSION = PASS / FAIL / NOT_APPLICABLE_WITH_REASON`
- `OPENVIKING_REGRESSION = PASS / FAIL / NOT_APPLICABLE_WITH_REASON`
- `WINDOWS_DEPENDENCY = NONE / FOUND`
- `PHASE3_CANARY = PASS / FAIL`
- `GATE_PROACTIVE_CORE_NOTIFICATION_REVIEW = WAITING_FOR_CHATGPT`

Do not report `FAIL` merely because a regression check was not executed; use `NOT_APPLICABLE_WITH_REASON` where appropriate. `FAIL` means evidence of an actual failed requirement.

Then **STOP**. Do not enable production notifications or actions. Do not proceed to Phase 4 without a new ChatGPT review.
