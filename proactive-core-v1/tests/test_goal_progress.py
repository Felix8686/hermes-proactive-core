from __future__ import annotations

import unittest
from collections.abc import Mapping

from proactive_core.goal_progress import (
    GoalProgressEngine,
    GoalProgressLedger,
    deterministic_prefilter,
    normalize_goal_inputs,
)


NOW = "2026-09-19T12:00:00Z"


def row(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "goal_id": "goal-japan",
        "project_id": "visa-research",
        "active": True,
        "state": "blocked_then_unblocked",
        "evidence": "required document checklist is complete",
        "next_action": "compare two visa routes",
        "why_now": "the checklist was completed today",
        "confidence": 0.9,
        "user_decision_required": True,
        "observed_at": NOW,
        "state_fingerprint": "state-2026-09-19-a",
    }
    value.update(overrides)
    return value


class GoalProgressTests(unittest.TestCase):
    def test_no_active_goal_has_no_candidate(self) -> None:
        result = GoalProgressEngine().run([row(active=False)], now=NOW)
        self.assertIsNone(result.candidate)
        self.assertEqual(result.reason_code, "no_eligible_goal")

    def test_completed_task_unlocks_one_candidate(self) -> None:
        result = GoalProgressEngine().run([row()], now=NOW)
        self.assertIsNotNone(result.candidate)
        self.assertEqual(result.candidate.decision, "NEXT_ACTION_CANDIDATE")  # type: ignore[union-attr]
        self.assertTrue(result.candidate.user_decision_required)  # type: ignore[union-attr]

    def test_unchanged_state_and_fingerprint_are_deduped(self) -> None:
        engine = GoalProgressEngine()
        first = engine.run([row()], now=NOW)
        second = engine.run([row()], now="2026-09-19T13:00:00Z")
        self.assertIsNotNone(first.candidate)
        self.assertIsNone(second.candidate)
        self.assertEqual(second.reason_code, "no_eligible_goal")

    def test_blocked_stale_and_windows_inputs_are_filtered(self) -> None:
        inputs = [
            row(blocked=True, state_fingerprint="blocked"),
            row(windows_dependency=True, state_fingerprint="windows"),
            row(observed_at="2026-09-01T00:00:00Z", state_fingerprint="stale"),
        ]
        self.assertEqual(deterministic_prefilter(inputs, now=NOW), [])

    def test_rejected_and_completed_candidates_are_suppressed(self) -> None:
        ledger = GoalProgressLedger()
        engine = GoalProgressEngine(ledger=ledger)
        first = engine.run([row()], now=NOW)
        fingerprint = first.candidate.fingerprint  # type: ignore[union-attr]
        ledger.reject(fingerprint)
        changed = row(state_fingerprint="state-2026-09-19-b", next_action="choose an eligible route")
        second = engine.run([changed], now="2026-09-19T13:00:00Z")
        self.assertIsNotNone(second.candidate)
        ledger.complete(second.candidate.fingerprint)  # type: ignore[union-attr]
        third = engine.run(
            [row(state_fingerprint="state-2026-09-19-c", next_action="book a consultation")],
            now="2026-09-19T14:00:00Z",
        )
        self.assertIsNotNone(third.candidate)
        self.assertNotEqual(third.candidate.fingerprint, second.candidate.fingerprint)  # type: ignore[union-attr]

    def test_malformed_source_fails_closed_and_privacy_filter_rejects_private_body(self) -> None:
        malformed = GoalProgressEngine().run({"goals": [{"goal_id": "missing"}]}, now=NOW)
        self.assertTrue(malformed.malformed)
        self.assertIsNone(malformed.candidate)
        private = GoalProgressEngine().run([row(private_message="do not ingest")], now=NOW)
        self.assertTrue(private.malformed)

    def test_one_candidate_per_run_and_semantic_budget(self) -> None:
        calls: list[Mapping[str, object]] = []

        def semantic(context: Mapping[str, object]) -> dict[str, object]:
            calls.append(context)
            return {"rank": 0.8}

        engine = GoalProgressEngine(semantic_call=semantic)
        source = [
            row(goal_id="goal-b", confidence=0.8, state_fingerprint="b"),
            row(goal_id="goal-a", confidence=0.9, state_fingerprint="a"),
        ]
        first = engine.run(source, now=NOW)
        self.assertEqual(first.prefiltered_count, 2)
        self.assertEqual(first.semantic_calls, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first.candidate.goal_id, "goal-a")  # type: ignore[union-attr]
        second = engine.run([row(goal_id="goal-c", state_fingerprint="c")], now="2026-09-19T13:00:00Z")
        self.assertEqual(second.semantic_calls, 0)
        self.assertEqual(len(calls), 1)

    def test_health_tick_does_not_call_semantic(self) -> None:
        calls: list[Mapping[str, object]] = []
        engine = GoalProgressEngine(semantic_call=lambda context: calls.append(dict(context)))
        result = engine.run([row()], now=NOW, health_tick=True)
        self.assertEqual(result.reason_code, "health_tick_no_llm")
        self.assertEqual(result.semantic_calls, 0)
        self.assertEqual(calls, [])

    def test_candidate_has_required_grounding_fields(self) -> None:
        candidate = GoalProgressEngine().run([row()], now=NOW).candidate
        self.assertIsNotNone(candidate)
        data = candidate.as_dict()  # type: ignore[union-attr]
        self.assertTrue(all(data[key] for key in (
            "goal_id", "evidence", "proposed_next_action", "why_now", "fingerprint",
        )))
        self.assertGreaterEqual(data["confidence"], 0)
        self.assertLessEqual(data["confidence"], 1)


if __name__ == "__main__":
    unittest.main()
