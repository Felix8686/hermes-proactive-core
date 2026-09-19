# Hermes staged execution handoff

## Purpose

Execution is transferred from Codex to Hermes through GitHub.

Repository:
`Felix8686/hermes-proactive-core`

Branch:
`codex/proactive-core-v1`

This file is the staging controller for the remaining Proactive Core work.

The user explicitly requires **incremental execution**:

- execute only one authorized stage at a time;
- when that stage is complete, report the result to the user;
- STOP;
- do not start the next stage until the user explicitly says to continue and GitHub is updated to authorize it.

Do not infer permission from the existence of future-stage descriptions.

## Global boundaries

These remain in force for every stage unless a later GitHub handoff explicitly changes them:

- VPS_ONLY = TRUE
- Windows dependency = NONE
- do not merge `main`
- do not change the active Hermes model/provider
- do not enable ACT unless a later stage explicitly authorizes it
- do not add Windows Executor back
- do not create a second Hermes profile
- preserve rollback evidence and audit data
- use the smallest safe change
- if evidence is insufficient, report the blocker instead of widening scope

## Current repository state

Known completed work:

- Phase 1–2 implementation and regression tests
- Phase 3 VPS-only Shadow Canary
- Phase 4A controlled Telegram delivery and duplicate suppression
- Phase 4 implementation tests reached 52/52 PASS at commit `124a07c`

Current state in GitHub:

- Phase 4 final report exists and Phase 4 notification canary passed.
- H1 completed at commit `32b6e5f1beafb1ef68e7a526a3c605552854de5e`.
- The user explicitly authorized H2 on 2026-09-19.
- `HANDOFF-PHASE5.md` is authorized for H2 implementation + offline tests only. Phase 5A/5B production canaries remain locked.

---

# STAGE H1 — Finish Phase 4B and close Phase 4

STATUS: **COMPLETED**

Completed at commit `32b6e5f1beafb1ef68e7a526a3c605552854de5e`.
Do not rerun H1 unless a later handoff explicitly requests revalidation.

## H1 objective

Finish the remaining real scheduled-observation requirement for Phase 4, generate the final Phase 4 report, push it to GitHub, notify the user, and STOP.

## H1 execution

1. Pull the latest `codex/proactive-core-v1` branch.
2. Read:
   - this file;
   - `HANDOFF-PHASE4.md`;
   - current Phase 4 implementation/evidence;
   - existing Proactive Core production state.
3. Read-only determine whether the required three consecutive real `proactive-review-v1` scheduled executions have already occurred and have valid evidence.
4. If three valid scheduled executions already exist:
   - use them;
   - do not manufacture additional runs merely to satisfy the count.
5. If fewer than three valid scheduled executions exist:
   - wait/observe only the minimum additional scheduled executions needed;
   - do not induce outages;
   - do not send additional synthetic Telegram test messages unless required to diagnose an actual failure.
6. Validate at minimum:
   - Gateway stable;
   - Telegram normal connection healthy;
   - proactive-review scheduled execution status;
   - notification count;
   - duplicate suppression;
   - Event Store health;
   - OpenViking health;
   - Cron definition hash;
   - Proactive code/config hashes;
   - LLM calls = 0;
   - actions executed = 0;
   - Windows dependency = NONE.
7. Generate:
   `PROACTIVE-CORE-PHASE4-NOTIFICATION-REPORT.md`
   according to `HANDOFF-PHASE4.md`.
8. Commit and push only to:
   `codex/proactive-core-v1`
9. Do not merge `main`.
10. Do not start Phase 5.

## H1 completion response to user

After pushing, tell the user only the useful result:

- Phase 4 final status;
- report commit SHA;
- whether any regression/problem was found;
- whether Phase 5 is technically ready for review.

Then explicitly state:

`STAGE_H1_COMPLETE = YES`
`NEXT_STAGE_AUTHORIZED = NO`
`USER_DECISION_REQUIRED = YES`

Then STOP.

Do not run H2 in the same session.

---

# STAGE H2 — Phase 5 implementation + offline tests only

STATUS: **AUTHORIZED NOW**

The user explicitly authorized H2. This is the only stage Hermes may execute now.

Authorized scope only:

- review the final Phase 4 report;
- implement Goal Progress inputs/prefilter/candidate logic from `HANDOFF-PHASE5.md`;
- add/extend tests;
- run the complete regression suite;
- no Phase 5 production canary;
- no Goal Progress Telegram notification;
- no ACT;
- stop and report back to the user.

H2 acceptance requirements:

- start from the latest branch state and review the final Phase 4 report;
- implement only Goal Progress inputs / deterministic prefilter / candidate logic / semantic-call budget scaffolding required by `HANDOFF-PHASE5.md`;
- add or extend offline tests;
- run the complete regression suite;
- do not deploy Phase 5A to production;
- do not enable Goal Progress Telegram notifications;
- do not enable ACT;
- do not alter the current model/provider;
- do not merge `main`;
- keep VPS_ONLY = TRUE and WINDOWS_DEPENDENCY = NONE.

At completion, push code/tests/evidence to `codex/proactive-core-v1`, report the commit SHA, notify the user, then STOP.

Required stop gate:

`STAGE_H2_COMPLETE = YES`
`NEXT_STAGE_AUTHORIZED = NO`
`USER_DECISION_REQUIRED = YES`

Do not run H3 in the same session.

---

# STAGE H3 — Phase 5A Goal Progress Shadow Canary

STATUS: **LOCKED**

Do not execute until separately authorized.

Planned scope only:

- deploy Goal Progress in SHADOW mode;
- inspect real VPS goal/project state read-only;
- candidate stored/audited only;
- no Goal Progress Telegram notification;
- no ACT;
- limited semantic model use only as allowed by `HANDOFF-PHASE5.md`;
- observe required window;
- report candidate quality;
- STOP for user decision.

---

# STAGE H4 — Phase 5B controlled Goal Progress notification

STATUS: **LOCKED**

Do not execute until separately authorized after H3 review.

Planned scope only:

- enable at most one grounded Goal Progress suggestion/day;
- owner Telegram only;
- clearly mark as suggestion, not action;
- no ACT;
- STOP after validation and report.

---

# STAGE H5 — Safe ACT design review

STATUS: **LOCKED**

Do not execute until separately authorized.

This stage is design/review only unless a future GitHub handoff explicitly grants implementation permission.

Goal:

- define the first low-risk autonomous action allowlist;
- define ASK/HIGH boundaries;
- rollback/idempotency/circuit-breaker rules;
- propose tests;
- do not enable ACT.

---

## Rule for all future stages

The user controls progression.

At the end of every stage:

1. push evidence/report to GitHub;
2. notify the user;
3. STOP;
4. wait for explicit user instruction.

Never chain stages automatically.
