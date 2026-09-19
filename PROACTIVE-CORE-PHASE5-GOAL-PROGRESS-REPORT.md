# Proactive Core Phase 5 Goal Progress Report (H2)

- Repository: `Felix8686/hermes-proactive-core`
- Branch: `codex/proactive-core-v1`
- Scope: H2 implementation and offline tests only
- Phase 5A production Shadow Canary: NOT AUTHORIZED / NOT RUN
- Phase 5B Goal Progress Telegram notification: NOT AUTHORIZED / NOT RUN
- ACT: disabled / not run

## Required fields

```text
PHASE4_BASE_ACCEPTED = PASS
GOAL_PROGRESS_INPUTS = PASS
PHASE5A_SHADOW = NOT_AUTHORIZED
CANDIDATE_GROUNDING = PASS
CANDIDATE_DEDUPE = PASS
STALE_STATE_FILTER = PASS
LLM_PREFILTER = PASS
LLM_CALLS_PER_DAY = <=1 (offline budget seam; no production call)
HEALTH_TICK_LLM_CALLS = 0
GOAL_FILES_AUTO_MODIFIED = 0
ACTIONS_EXECUTED = 0
WINDOWS_DEPENDENCY = NONE
PHASE5B_NOTIFICATION = NOT_AUTHORIZED
GOAL_NOTIFICATION_COUNT_PER_DAY = 0 (no notification path enabled)
GATEWAY_REGRESSION = NOT_RUN (H2 is offline-only)
TELEGRAM_REGRESSION = NOT_RUN (no Telegram path touched)
CRON_REGRESSION = NOT_RUN (no Cron path touched)
OPENVIKING_REGRESSION = NOT_RUN (no OpenViking path touched)
PHASE5_GOAL_PROGRESS = PASS (offline H2 scope)
GATE_PROACTIVE_CORE_SAFE_ACT_REVIEW = WAITING_FOR_CHATGPT
```

## Implementation

Added `proactive_core/goal_progress.py` with:

- bounded structured Goal/Project input normalization;
- privacy rejection for private chat bodies, credentials, tokens, and similar fields;
- deterministic active/blocked/stale/Windows-dependency/actionability prefilter;
- deterministic candidate fingerprint and required grounding fields;
- at most one `NEXT_ACTION_CANDIDATE` per run;
- unchanged-state, cooldown, rejected-candidate, and completed-candidate suppression;
- injected semantic-call seam limited to one call per UTC day and never called on health-only ticks;
- fail-closed malformed-source handling;
- no provider/model selection, notification, GOALS.md mutation, `/goal` call, or ACT adapter.

Also updated the package exports and offline README instructions, and added
`tests/test_goal_progress.py` covering the H2 acceptance cases.

## Verification

From `proactive-core-v1/`:

```text
python3 -m unittest discover -s tests -v
Ran 61 tests in 2.183s
OK
```

Focused Goal Progress suite:

```text
PYTHONPATH=. python3 -m unittest tests.test_goal_progress -v
Ran 9 tests in 0.008s
OK
```

The 52 existing Phase 1–4 tests remain passing; the H2 suite adds 9 tests.
No production canary, Telegram message, model/provider change, GOALS.md change,
`/goal` mutation, ACT execution, or `main` merge was performed.

## H2 gate

```text
STAGE_H2_COMPLETE = YES
NEXT_STAGE_AUTHORIZED = NO
USER_DECISION_REQUIRED = YES
```

H3 is technically ready for a separately authorized review/deployment because the
offline candidate/prefilter contract and tests are present. H3 was not started.
