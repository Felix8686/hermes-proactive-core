# Phase 3 handoff — VPS-only Canary dry-run

## Source of truth

The already tested Phase 1–2 implementation exists locally at:

`D:\Codex Projects\IdeaForge\proactive-core-v1`

The acceptance report exists at:

`D:\Codex Projects\IdeaForge\PROACTIVE-CORE-PHASE1-2-REPORT.md`

Do **not** reimplement Phase 1–2 from scratch. Import the exact tested implementation into this repository branch, preserving the tested behavior, then rerun the same suite from the repository checkout.

## Git boundary

Repository: `Felix8686/hermes-proactive-core`

Development branch: `codex/proactive-core-v1`

Rules:

- Do not commit directly to `main` beyond the existing bootstrap commit.
- Do not merge to `main`.
- Do not commit secrets, cookies, OAuth tokens, Telegram tokens, SSH private keys, or production `.env` files.
- Keep import/baseline commits separate from Canary/deployment commits.

## Authoritative architecture baseline — VPS_ONLY = TRUE

The current Hermes architecture is now VPS-only.

All Windows-dependent Hermes tasks have been cancelled. Windows no longer provides Hermes execution capacity and must be treated only as an ordinary user terminal.

Authoritative architecture:

```text
Telegram / cloud events
        ↓
VPS Hermes
        ↓
Proactive Core
        ↓
OpenViking / Memory / KnowledgeVault / 万象 / Cron
```

Hard invariant:

**Windows being powered off must not degrade any current Hermes production capability or any Proactive Core v1 capability.**

Do not restore Windows Executor dependencies, Windows Hermes, creator/tech profiles, or any VPS→Windows execution path.

## Mandatory pre-Canary Phase 2.5 — VPS-only normalization

Before any Phase 3 Canary deployment, complete all of the following.

### A. Remove Windows Executor from Proactive Core design and implementation

Remove from design, fixtures, event sources, examples, tests, and Canary targets where present:

- Windows Executor heartbeat;
- `executor.offline` / `executor.recovered` events;
- Windows Executor action source;
- VPS → Windows Executor dispatch logic;
- any assumption that Windows availability is a Hermes health signal.

Historical reports may retain historical mentions. Do not rewrite history.

### B. Read-only audit of current production dependency state

Verify and report exactly:

```text
ACTIVE_JOBS_DEPENDING_ON_WINDOWS_EXECUTOR = 0
ACTIVE_CRON_DEPENDING_ON_WINDOWS_EXECUTOR = 0
ACTIVE_SKILLS_DEPENDING_ON_WINDOWS_EXECUTOR = 0
```

Search active runtime configuration, Cron definitions, Skills, scripts, state, and currently loaded references.

Classify every Executor-related hit as:

- `ACTIVE` — currently loaded or executed;
- `STALE` — no longer required but still referenced by active runtime/config;
- `HISTORICAL` — report/log/archive/history only.

Rules:

- `HISTORICAL`: preserve unchanged.
- `STALE`: remove or disable only the stale active-runtime reference, with exact backup and evidence.
- `ACTIVE`: because the user has explicitly cancelled all Windows-dependent tasks, retire the obsolete dependency with the narrowest safe change, exact backup, and regression check. If an `ACTIVE` dependency cannot be safely proven obsolete, STOP and report instead of guessing.

Do not delete user data or broad directories. Do not revive Windows Executor.

### C. Update project documentation

Update at minimum:

- `PROACTIVE-CORE-V1-DESIGN.md`
- `PROACTIVE-CORE-V1-IMPLEMENTATION-PLAN.md`
- `HANDOFF-PHASE3.md` if further implementation-specific clarification is required

The updated documents must state:

```text
VPS_ONLY = TRUE
WINDOWS_HERMES_ROLE = NONE
WINDOWS_EXECUTOR_ROLE = NONE
WINDOWS_POWER_OFF_IMPACT = NONE
```

### D. Rerun Phase 1–2 tests after normalization

Rerun the full repository test suite after Windows Executor removal.

Required result: all applicable tests PASS. If the test count changes because obsolete Windows-specific fixtures are removed, explain the delta; do not preserve meaningless tests merely to keep the old count.

No Phase 3 Canary may begin until this normalization and test pass are complete.

## Phase 1–2 import acceptance

Before any VPS deployment:

1. The tested implementation and tests must exist in this branch.
2. Relevant design and acceptance docs must be under version control.
3. Run the test suite from the repository checkout.
4. No network, Telegram, action, or production adapters may be active in the offline test path.
5. Confirm source-code defaults remain false:
   - `PROACTIVE_ENABLED=false`
   - `PROACTIVE_NOTIFICATIONS_ENABLED=false`
   - `PROACTIVE_ACTIONS_ENABLED=false`
6. Record commit SHA and test result.

## Phase 3 scope

Phase 3 is a **VPS Canary dry-run only**.

The Canary may read real VPS production state, normalize events, evaluate policy, and write to an isolated Proactive Core SQLite store. It must not:

- send Telegram messages;
- execute ACT actions;
- call an LLM;
- modify existing Hermes Cron jobs during the Canary window;
- modify Gateway, OpenViking, KnowledgeVault, Memory, Cloudflare, model/provider, or systemd service configuration;
- restart production services;
- access or depend on Windows.

The existing VPS business jobs remain unchanged during the Canary window after the pre-Canary normalization has completed.

## Canary switch contract — authoritative clarification

A manual Shadow Canary must be able to process real inputs, therefore `PROACTIVE_ENABLED=false` cannot be required inside that isolated process while simultaneously expecting processing.

For **Phase 3 manual isolated Canary only**, the following temporary runtime state is explicitly authorized:

- `PROACTIVE_ENABLED=true`
- `PROACTIVE_NOTIFICATIONS_ENABLED=false`
- `PROACTIVE_ACTIONS_ENABLED=false`

This is not production enablement.

Hard requirements:

1. Source-code defaults in `switches.py` remain false.
2. Do not persist `PROACTIVE_ENABLED=true` into Hermes production config, systemd, Cron, shell profile, `.env`, or startup paths.
3. Set it only in the environment of the single manual isolated Canary invocation (or equivalent ephemeral process environment).
4. Notifications and actions remain false for the entire Phase 3 window.
5. After each Canary invocation, no persistent enabled state may remain.
6. If the Canary cannot run under these constraints, stop and report rather than widening permissions.

## Canary deployment strategy

Use a narrow manual/isolated sidecar-style invocation rather than wiring the Core into production Cron.

Required controls:

- deploy under an isolated path outside active Hermes runtime code;
- use a separate SQLite Canary DB with restrictive permissions;
- source-code defaults for all three switches remain false;
- only the isolated manual Canary process may temporarily receive `PROACTIVE_ENABLED=true`;
- read production inputs only through known read-only files/status commands;
- capture pre/post hashes for any production file that might otherwise be suspected of change;
- record Gateway PID, `NRestarts`, Telegram connected state, OpenViking health, and existing VPS Cron status before and after each Canary window;
- do not change the active Hermes model. The current `OpenAI Codex / gpt-5.6-luna` setting is intentional.

## Canary observation targets

At minimum, safely feed these VPS-only sources through the Shadow path:

- current VPS Cron/job status and failure/recovery state;
- Kanban active-task summary;
- Gateway health state;
- existing proactive monitor output, treated as probe input rather than user-facing output.

Do **not** include Windows Executor heartbeat/offline/recovery events or any Windows source.

If a source cannot be adapted without modifying production, leave it out and record that limitation. Do not widen scope to make the Canary appear complete.

## Canary invariants

For the entire Phase 3 window:

- `VPS_ONLY = TRUE`
- `WINDOWS_DEPENDENCY = NONE`
- `LLM_CALLS = 0`
- `TELEGRAM_MESSAGES_SENT = 0`
- `ACTIONS_EXECUTED = 0`
- production Cron definitions unchanged during the Canary window
- production Gateway remains active
- no additional Hermes profile is created
- no model/provider change
- no new public webhook or listener

## Required evidence

Run enough observations to cover at least:

- no-change/silent input;
- duplicate event suppression;
- one synthetic or naturally occurring failure-like fixture through the same deployed Canary code without altering production state;
- recovery handling;
- Canary process restart proving persisted dedupe/state survives restart;
- SQLite lock/fail-closed behavior;
- Core disabled behavior (`PROACTIVE_ENABLED=false` must fail closed with empty output/no processing).

Do not induce a real production failure merely for testing.

## Stop gate

At the end of Phase 3 generate:

`PROACTIVE-CORE-PHASE3-CANARY-REPORT.md`

Required final fields:

- `PHASE1_2_IMPORTED_TO_GITHUB = PASS / FAIL`
- `VPS_ONLY_NORMALIZATION = PASS / FAIL`
- `ACTIVE_JOBS_DEPENDING_ON_WINDOWS_EXECUTOR = 0 / NONZERO`
- `ACTIVE_CRON_DEPENDING_ON_WINDOWS_EXECUTOR = 0 / NONZERO`
- `ACTIVE_SKILLS_DEPENDING_ON_WINDOWS_EXECUTOR = 0 / NONZERO`
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

Do not report `FAIL` merely because a check was not executed; use `NOT_APPLICABLE_WITH_REASON` where appropriate. `FAIL` means evidence of an actual failed requirement.

Then **STOP**. Do not enable production notifications or actions. Do not proceed to Phase 4 without a new ChatGPT review. Do not merge `main`.
