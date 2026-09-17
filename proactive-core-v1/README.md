# Proactive Core v1 — isolated Phase 1–2

This directory is an isolated, local implementation of the approved offline Event/Policy contracts and fixture-only Shadow detector. It is not connected to Hermes, VPS services, Telegram, the filesystem goal tracker, or any action executor. No production configuration is read or changed.

## Safety boundary

- PROACTIVE_ENABLED, PROACTIVE_NOTIFICATIONS_ENABLED, and PROACTIVE_ACTIONS_ENABLED default to false.
- The Shadow runner additionally requires --enable-shadow and PROACTIVE_ENABLED=true.
- Shadow accepts only JSON fixtures in fixtures/; it has no network, Telegram, or action adapter.
- The command-line state directory is constrained to this isolated project directory.
- Shadow renders only fixed, controlled candidate text. It never prints Event, Decision, Store, or audit JSON.
- Notification/action enable switches cannot activate delivery or action execution from the Shadow code path.
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

No VPS canary, Telegram delivery, production action, production model change, or production deployment is part of this directory.
