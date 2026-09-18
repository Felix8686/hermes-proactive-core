# Phase 5 handoff — Goal Progress (PREPARED, NOT YET AUTHORIZED)

## Authorization gate

This handoff is prepared in advance, but Phase 5 MUST NOT start until ChatGPT has reviewed the final Phase 4 report and explicitly changed the gate to APPROVED.

Required before execution:

- `PROACTIVE-CORE-PHASE4-NOTIFICATION-REPORT.md` exists on `codex/proactive-core-v1`;
- Phase 4 report shows notification canary passed;
- ChatGPT explicitly approves Phase 4 and authorizes Phase 5 in a later instruction.

Until then: STOP after Phase 4. Do not infer approval from the existence of this file.

## Phase 5 objective

Add Goal Progress: Proactive Core should identify a small number of high-value next-step candidates for the owner's active goals/projects without executing them.

This phase is about:

sense project/goal state
→ identify meaningful progress opportunity
→ produce `NEXT_ACTION_CANDIDATE`
→ dedupe/rank/budget
→ optionally notify owner after validation

It is NOT autonomous execution.

## Architecture boundary

Current architecture remains:

- VPS_ONLY = TRUE
- Windows dependency = NONE
- existing Hermes single default profile
- no ACT
- no public webhook/listener
- no automatic write to GOALS.md
- no automatic /goal mutation
- no autonomous GitHub/code/deployment changes

## Inputs

Use only existing authorized VPS-side sources that are already part of the current Hermes environment, preferring structured/read-only sources:

- GOALS.md
- active Kanban/project state
- recent completed/blocked task state
- existing project handoff/status artifacts
- current Proactive Event Store
- existing Cron/probe state where relevant

Do not ingest private chat bodies wholesale. Do not add continuous browser/screen/audio monitoring.

## Goal Progress behavior

For each run:

1. Determine whether an active goal/project has a meaningful state change or a clearly actionable next step.
2. Generate at most one `NEXT_ACTION_CANDIDATE` per run.
3. Candidate must include:
   - goal/project identifier
   - evidence/state that triggered the candidate
   - proposed next action
   - why now
   - confidence
   - whether user decision is required
   - fingerprint for dedupe
4. If nothing meaningfully changed, produce no candidate and remain silent.
5. Do not create chains of follow-on candidates in the same run.

## LLM boundary

This is the first phase where semantic reasoning may be required.

Allowed only after deterministic prefilter says a goal/project is eligible for review.

Hard limits:

- maximum 1 semantic Goal Progress model call per day during canary
- use the currently configured Hermes model; do not change provider/model
- no model call on ordinary health-only ticks
- no model-generated action execution
- store only concise derived candidate/audit data, not raw private chat bodies

If the same candidate fingerprint already exists within cooldown, do not call the model again unless meaningful source state changed.

## Candidate policy

Initial candidate states:

- `IGNORE`
- `NEXT_ACTION_CANDIDATE`
- `ASK`

No `ACT` in Phase 5.

A candidate is eligible only when:

- tied to an active goal/project;
- supported by current evidence;
- concrete enough to act on;
- not merely generic advice;
- not a duplicate of a recently rejected/completed candidate;
- not based solely on historical stale state.

## Notification policy

Phase 5A starts in SHADOW mode:

- candidate stored/audited;
- no Goal Progress Telegram notification.

Collect at least 3 real scheduled observations or 24 hours, whichever comes first.

Then manually review candidate quality.

Only if quality review passes may Phase 5B send Goal Progress notifications.

Phase 5B limit:

- max 1 Goal Progress notification/day
- owner Telegram only
- concise message
- clearly distinguish suggestion from executed action
- no notification if candidate confidence/evidence is insufficient

Example form:

`【主动推进建议】<project>: <next action>. 原因：<why now>. 未执行，需要你决定。`

## Quality acceptance

A Goal Progress candidate must be rejected if it is:

- vague (`继续推进项目`, `多学习`)
- based on stale/closed work
- already completed
- contradicted by current project state
- dependent on Windows
- requiring unavailable credentials/tools without saying so
- a duplicate within cooldown
- an ungrounded model invention

## Tests

Add regression tests for at least:

- no active goal → no candidate
- unchanged state → dedupe/no new candidate
- completed task unlocks next step → candidate
- blocked task remains blocked → no false progress candidate
- stale historical project ignored
- candidate fingerprint dedupe
- rejected candidate suppression
- one-call-per-day LLM budget
- no LLM call on health-only tick
- malformed source fail-closed
- privacy filter
- Windows dependency absent
- ACT remains impossible
- silent stdout when no notify-worthy candidate

All existing tests must remain PASS.

## Phase 5 canary

Deploy only after ChatGPT Phase 4 approval.

Canary must preserve:

- `PROACTIVE_ACTIONS_ENABLED=false`
- `ACTIONS_EXECUTED=0`
- no model/provider change
- no main merge
- no Windows dependency

Observe real goal/project state without modifying it.

## Required report

Generate and commit:

`PROACTIVE-CORE-PHASE5-GOAL-PROGRESS-REPORT.md`

Required fields:

```text
PHASE4_BASE_ACCEPTED = PASS / FAIL
GOAL_PROGRESS_INPUTS = PASS / FAIL
PHASE5A_SHADOW = PASS / FAIL
CANDIDATE_GROUNDING = PASS / FAIL
CANDIDATE_DEDUPE = PASS / FAIL
STALE_STATE_FILTER = PASS / FAIL
LLM_PREFILTER = PASS / FAIL
LLM_CALLS_PER_DAY = <=1 / VIOLATION
HEALTH_TICK_LLM_CALLS = 0 / NONZERO
GOAL_FILES_AUTO_MODIFIED = 0 / NONZERO
ACTIONS_EXECUTED = 0 / NONZERO
WINDOWS_DEPENDENCY = NONE / FOUND
PHASE5B_NOTIFICATION = PASS / NOT_AUTHORIZED / FAIL
GOAL_NOTIFICATION_COUNT_PER_DAY = <=1 / VIOLATION
GATEWAY_REGRESSION = PASS / FAIL
TELEGRAM_REGRESSION = PASS / FAIL
CRON_REGRESSION = PASS / FAIL
OPENVIKING_REGRESSION = PASS / FAIL
PHASE5_GOAL_PROGRESS = PASS / FAIL
GATE_PROACTIVE_CORE_SAFE_ACT_REVIEW = WAITING_FOR_CHATGPT
```

Then STOP.

Do not proceed to Safe ACT without another ChatGPT review.
