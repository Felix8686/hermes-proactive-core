# Proactive Core v1 — Phase 1–2 foundations and Phase 4 canary runner

This directory contains the approved offline Event/Policy contracts and fixture-only Shadow detector, plus an explicit Phase 4 notification-only Cron runner. Local tests remain offline; only a deliberate deployment of `run_phase4.py` into the active VPS Hermes scripts directory can connect it to the existing Proactive Cron path. The runner never edits Hermes configuration itself and has no ACT adapter.

## Safety boundary

- PROACTIVE_ENABLED, PROACTIVE_NOTIFICATIONS_ENABLED, and PROACTIVE_ACTIONS_ENABLED default to false.
- The Shadow runner additionally requires --enable-shadow and PROACTIVE_ENABLED=true.
- Shadow accepts only JSON fixtures in fixtures/; it has no network, Telegram, or action adapter.
- The Shadow command-line state directory is constrained to this isolated project directory.
- Shadow renders only fixed, controlled candidate text. It never prints Event, Decision, Store, or audit JSON.
- Notification/action enable switches cannot activate delivery or action execution from the Shadow code path.
- The Phase 4 runner defaults all switches to false, requires an exact hash match to the existing Hermes-owned Telegram route, and rejects `PROACTIVE_ACTIONS_ENABLED=true`.
- Phase 4 accepts only the five deterministic event types in `phase4.py`; output is one fixed-template owner notification or empty stdout. Event Store, errors, and delivery audit data never go to Cron stdout.
- Phase 4 uses the existing Cron `--no-agent` script path. Its probe subprocess receives a minimal environment, and its only network check is the local OpenViking health endpoint.
- Phase 4 has no LLM client/import path and returns `llm_calls=0` and `actions_executed=0`.
- Goal progress support returns only SILENT, NOTIFY_CANDIDATE, or ASK_CANDIDATE; it does not modify GOALS.md or launch /goal.
- SQLite is the state store. JSON is fixture/export-only. The store directory is private and database files are restricted to the current user.
- Free-form condition strings and non-identifier field names are dropped/redacted; only short state tokens are retained.

## Local checks

Run from this directory:

    python -m unittest discover -s tests -v

To run the empty health fixture with Shadow explicitly enabled, set PROACTIVE_ENABLED=true in the current process environment and run:

    python run_shadow.py --enable-shadow

An empty fixture produces empty stdout. The default run does not create a store or produce stdout.

## Scope and status

Phase 1 covers versioned contracts, deterministic policy, privacy redaction, transactional SQLite state, append-only audit transitions, budgets, circuit breaker, and fail-closed switches.

Phase 2 covers local fixture processing only. It proves deterministic fingerprints/decisions, deduplicated candidates, recovery behavior, empty output on no event, zero LLM calls on the health path, zero delivery, and zero action execution.

Phase 4 implements notification-only integration and a controlled canary; its deployed status and evidence are recorded in `PROACTIVE-CORE-PHASE4-NOTIFICATION-REPORT.md`. No production action, production model change, or merge to `main` is part of this directory.
