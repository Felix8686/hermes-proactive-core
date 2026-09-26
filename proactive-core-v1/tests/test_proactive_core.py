from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from proactive_core.budget import BudgetConfig, NotificationBudget
from proactive_core.circuit import CircuitBreaker
from proactive_core.llm import OptionalLLMEnricher
from proactive_core.model import (
    ActionAttempt,
    ActionDecision,
    ActionScope,
    ActionState,
    ContractError,
    Decision,
    Event,
    EventStatus,
    GoalCandidateDecision,
    HighLevelDecision,
    NotificationAttempt,
    NotificationDecision,
    NotificationState,
    Risk,
    Severity,
)
from proactive_core.policy import PolicyEvaluator, evaluate_goal_candidate
from proactive_core.renderer import NotificationRenderer
from proactive_core.shadow import ShadowDetector
from proactive_core.store import SQLiteEventStore, StoreBusyError, StoreError, UnsupportedSchema
from proactive_core.switches import ExecutionGate, KillSwitches, SwitchConfigurationError


BASE_TIME = "2026-02-03T04:05:06Z"
ROOT = Path(__file__).resolve().parents[1]


def make_event(**overrides: object) -> Event:
    values: dict[str, object] = {
        "event_type": "service.failed",
        "source": "local-test",
        "subject": "service-main",
        "observed_at": BASE_TIME,
        "condition": {"state": "failed", "code": "probe_timeout"},
    }
    values.update(overrides)
    return Event(**values)  # type: ignore[arg-type]


def make_attempt(
    number: int,
    *,
    decision: NotificationDecision = NotificationDecision.NOTIFY,
    state: NotificationState = NotificationState.SENT,
    at: str = "2026-02-03T10:00:00Z",
    escalation: int = 0,
    fingerprint_suffix: str | None = None,
) -> NotificationAttempt:
    suffix = fingerprint_suffix or f"{number:064x}"
    return NotificationAttempt(
        candidate_id=f"ncan_{number:032x}",
        attempt_number=1,
        state=state,
        notification_decision=decision,
        occurred_at=at,
        fingerprint=f"sha256:{suffix}",
        severity=Severity.LOW,
        escalation_level=escalation,
    )


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp.name) / "store"
        self.store = SQLiteEventStore(self.state_dir, busy_timeout_ms=500)

    def tearDown(self) -> None:
        self.temp.cleanup()


class EventLifecycleTests(StoreTestCase):
    def test_first_duplicate_recovery_and_refailure_after_recovery(self) -> None:
        first = self.store.record_event(make_event())
        duplicate = self.store.record_event(make_event(observed_at="2026-02-03T04:06:00Z"))
        self.assertTrue(first.created)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(first.event.event_id, duplicate.event.event_id)
        self.assertEqual(duplicate.event.occurrence_count, 2)

        recovered_input = make_event(
            event_type="service.recovered",
            observed_at="2026-02-03T04:10:00Z",
            condition={"state": "healthy"},
            notification_requested=True,
            recovery_of=first.event.event_id,
        )
        recovered = self.store.record_event(recovered_input)
        self.assertTrue(recovered.recovered_event_created)
        self.assertEqual(recovered.event.status, EventStatus.RESOLVED)
        self.assertEqual(recovered.event.resolved_at, "2026-02-03T04:10:00.000000Z")
        parent = self.store.get_event(first.event.event_id)
        self.assertEqual(parent.status, EventStatus.RESOLVED)  # type: ignore[union-attr]
        self.assertEqual(parent.resolved_at, recovered.event.resolved_at)  # type: ignore[union-attr]

        duplicate_recovery = self.store.record_event(
            make_event(
                event_type="service.recovered",
                observed_at="2026-02-03T04:11:00Z",
                condition={"state": "healthy"},
                notification_requested=True,
                recovery_of=first.event.event_id,
            )
        )
        self.assertTrue(duplicate_recovery.duplicate)
        self.assertEqual(duplicate_recovery.event.event_id, recovered.event.event_id)

        refailure = self.store.record_event(
            make_event(observed_at="2026-02-03T04:15:00Z")
        )
        self.assertTrue(refailure.created)
        self.assertNotEqual(refailure.event.event_id, first.event.event_id)
        self.assertEqual(refailure.event.status, EventStatus.OPEN)
        self.assertEqual(
            self.store.open_event_by_fingerprint(first.event.fingerprint).event_id,  # type: ignore[union-attr]
            refailure.event.event_id,
        )

    def test_severity_escalation_and_expired_events(self) -> None:
        first = self.store.record_event(make_event())
        escalated = self.store.record_event(
            make_event(
                observed_at="2026-02-03T04:07:00Z",
                severity=Severity.HIGH,
                notification_requested=True,
            )
        )
        self.assertTrue(escalated.duplicate)
        self.assertEqual(escalated.event.event_id, first.event.event_id)
        self.assertEqual(escalated.event.severity, Severity.HIGH)
        self.assertEqual(escalated.event.escalation_level, 1)
        with self.store.reader() as connection:
            observation_types = [
                row["observation_type"]
                for row in connection.execute(
                    "SELECT observation_type FROM event_observations WHERE event_id=?",
                    (first.event.event_id,),
                )
            ]
        self.assertIn("SEVERITY_ESCALATION", observation_types)

        expired = self.store.record_event(
            make_event(
                event_type="maintenance.window",
                subject="service-expired",
                observed_at="2026-02-03T04:10:00Z",
                expires_at="2026-02-03T04:09:59Z",
            )
        )
        self.assertEqual(expired.event.status, EventStatus.EXPIRED)
        self.assertEqual(expired.event.expired_at, expired.event.observed_at)
        expired_duplicate = self.store.record_event(
            make_event(
                event_type="maintenance.window",
                subject="service-expired",
                observed_at="2026-02-03T04:11:00Z",
                expires_at="2026-02-03T04:09:59Z",
            )
        )
        self.assertTrue(expired_duplicate.duplicate)
        self.assertEqual(expired_duplicate.event.event_id, expired.event.event_id)

        due = self.store.record_event(
            make_event(
                event_type="maintenance.window",
                subject="service-due",
                observed_at=BASE_TIME,
                expires_at="2026-02-03T04:06:00Z",
            )
        )
        self.assertEqual(due.event.status, EventStatus.OPEN)
        self.assertEqual(self.store.expire_due("2026-02-03T04:06:00Z"), 1)
        self.assertEqual(self.store.get_event(due.event.event_id).status, EventStatus.EXPIRED)  # type: ignore[union-attr]

    def test_expired_open_event_is_closed_before_duplicate_matching(self) -> None:
        first = self.store.record_event(
            make_event(expires_at="2026-02-03T04:06:00Z")
        ).event
        later = self.store.record_event(
            make_event(observed_at="2026-02-03T04:07:00Z")
        )
        self.assertTrue(later.created)
        self.assertNotEqual(first.event_id, later.event.event_id)
        self.assertEqual(self.store.get_event(first.event_id).status, EventStatus.EXPIRED)  # type: ignore[union-attr]
        self.assertEqual(later.event.status, EventStatus.OPEN)

    def test_concurrent_tick_has_one_open_fingerprint_and_one_candidate(self) -> None:
        switches = KillSwitches(proactive_enabled=True)
        concurrent_store = SQLiteEventStore(self.state_dir, busy_timeout_ms=10000)
        detector = ShadowDetector(concurrent_store, switches)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: detector.run([make_event(notification_requested=True)]), range(24)))
        self.assertEqual(sum(result.notification_candidates_created for result in results), 1)
        self.assertEqual(self.store.notification_candidate_count(), 1)
        events = self.store.list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].occurrence_count, 24)
        with self.store.reader() as connection:
            observations = connection.execute(
                "SELECT COUNT(*) AS n FROM event_observations WHERE event_id=?",
                (events[0].event_id,),
            ).fetchone()["n"]
        self.assertEqual(observations, 24)
        self.assertEqual(sum(result.notifications_sent for result in results), 0)
        self.assertEqual(sum(result.actions_executed for result in results), 0)

    def test_crash_before_commit_rolls_back_event_and_observation(self) -> None:
        event = make_event()
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            with self.store.transaction() as connection:
                self.store._insert_event(connection, event, status=EventStatus.OPEN)
                self.store._append_observation(connection, event, "FIRST_SEEN")
                raise RuntimeError("simulated process crash")
        self.assertEqual(self.store.list_events(), [])
        with self.store.reader() as connection:
            count = connection.execute("SELECT COUNT(*) AS n FROM event_observations").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_abrupt_process_exit_before_commit_recovers_without_partial_event(self) -> None:
        script = (
            "import os,sys\n"
            "from proactive_core.model import Event,EventStatus\n"
            "from proactive_core.store import SQLiteEventStore\n"
            "store=SQLiteEventStore(sys.argv[1])\n"
            "event=Event(event_type='service.failed',source='local-test',"
            "subject='abrupt-crash',condition={'state':'failed'})\n"
            "connection=store._connect()\n"
            "connection.execute('BEGIN IMMEDIATE')\n"
            "store._insert_event(connection,event,status=EventStatus.OPEN)\n"
            "os._exit(73)\n"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.state_dir)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 73)
        reopened = SQLiteEventStore(self.state_dir, busy_timeout_ms=500)
        self.assertEqual(reopened.list_events(), [])

    def test_action_crash_after_start_is_unknown_and_not_retried(self) -> None:
        event = self.store.record_event(
            make_event(
                action_requested=True,
                action_scope=ActionScope.RETRY_SAFE,
            )
        ).event
        decision = PolicyEvaluator().evaluate(event)
        self.store.append_decision(decision)
        planned, created = self.store.add_action_candidate(event, decision, "safe_retry")
        self.assertTrue(created)
        self.assertEqual(planned.state, ActionState.PLANNED)
        started = ActionAttempt(
            action_id=planned.action_id,
            attempt_number=1,
            state=ActionState.STARTED,
            occurred_at="2026-02-03T04:06:00Z",
            event_id=event.event_id,
            idempotency_key=planned.idempotency_key,
            action_type=planned.action_type,
            detail_code="started",
        )
        self.store.append_action_transition(started)

        reopened = SQLiteEventStore(self.state_dir, busy_timeout_ms=500)
        self.assertEqual(reopened.mark_interrupted_actions_unknown(now="2026-02-03T04:07:00Z"), 1)
        self.assertEqual(reopened.mark_interrupted_actions_unknown(now="2026-02-03T04:08:00Z"), 0)
        attempts = reopened.action_attempts(planned.action_id)
        self.assertEqual(
            [item.state for item in attempts],
            [ActionState.PLANNED, ActionState.STARTED, ActionState.UNKNOWN_AFTER_RESTART],
        )
        high_event = reopened.record_event(
            make_event(
                subject="external-send",
                action_requested=True,
                action_scope=ActionScope.EXTERNAL_COMMUNICATION,
            )
        ).event
        high_decision = PolicyEvaluator().evaluate(high_event)
        with self.assertRaises(StoreError):
            reopened.add_action_candidate(high_event, high_decision, "send_email")

    def test_delivery_failure_does_not_change_event_or_decision(self) -> None:
        event = self.store.record_event(make_event(notification_requested=True)).event
        decision = PolicyEvaluator().evaluate(event)
        self.store.append_decision(decision)
        candidate_id, created = self.store.add_notification_candidate(event, decision)
        self.assertTrue(created)
        self.store.append_notification_transition(
            candidate_id, NotificationState.PENDING, occurred_at=BASE_TIME
        )
        self.store.append_notification_transition(
            candidate_id,
            NotificationState.FAILED,
            occurred_at="2026-02-03T04:06:00Z",
            detail_code="transport_unavailable",
        )
        self.assertEqual(self.store.get_event(event.event_id).status, EventStatus.OPEN)  # type: ignore[union-attr]
        saved_decision = self.store.list_decisions(event.event_id)[0]
        self.assertEqual(saved_decision["high_level"], HighLevelDecision.NOTIFY.value)
        self.assertEqual(saved_decision["notification_decision"], NotificationDecision.NOTIFY.value)
        self.assertEqual(
            [item.state for item in self.store.notification_history()],
            [NotificationState.PENDING, NotificationState.FAILED],
        )
        self.assertNotEqual(self.store.notification_history()[-1].state, NotificationState.SENT)

        sent_event = self.store.record_event(
            make_event(
                subject="service-delivered",
                notification_requested=True,
            )
        ).event
        sent_decision = PolicyEvaluator().evaluate(sent_event)
        sent_id, _ = self.store.add_notification_candidate(sent_event, sent_decision)
        self.store.append_notification_transition(
            sent_id, NotificationState.PENDING, occurred_at="2026-02-03T04:07:00Z"
        )
        self.store.append_notification_transition(
            sent_id, NotificationState.SENT, occurred_at="2026-02-03T04:08:00Z"
        )
        self.assertEqual(self.store.notification_history()[-1].state, NotificationState.SENT)

    def test_event_database_lock_fails_closed(self) -> None:
        other_store = SQLiteEventStore(self.state_dir, busy_timeout_ms=50)
        blocker = self.store._connect()
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(StoreBusyError):
                other_store.record_event(make_event())
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        self.assertEqual(self.store.list_events(), [])

    def test_schema_versions_and_private_permissions(self) -> None:
        self.assertEqual(self.store.metadata["schema_version"], "1")
        self.assertEqual(self.store.metadata["policy_version"], "pc-v1-phase1")
        with self.assertRaises(UnsupportedSchema):
            SQLiteEventStore(self.state_dir, policy_version="pc-v2")
        with self.store.reader() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].casefold(), "wal")
            self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 2)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(events)")
            }
        self.assertTrue({"status", "resolved_at", "recovery_of"} <= columns)
        self.assertNotIn("condition", columns)
        if os.name == "nt":
            icacls = shutil.which("icacls.exe") or shutil.which("icacls")
            identity = subprocess.run(
                [shutil.which("whoami.exe") or "whoami.exe"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            for path in (self.state_dir, self.store.db_path):
                output = subprocess.run(
                    [icacls, str(path)],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
                principals = [
                    line.strip()
                    for line in output.splitlines()
                    if ":" in line and not line.lstrip().casefold().startswith("successfully processed")
                ]
                self.assertEqual(len(principals), 1)
                self.assertIn(identity.casefold(), principals[0].casefold())
        else:
            self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(self.store.db_path.stat().st_mode), 0o600)

    def test_privacy_redaction_and_no_raw_payload_persistence(self) -> None:
        raw_secret = "sk-live-private-example-0123456789"
        raw_message = "private conversation body that must never be stored"
        with self.assertRaises(ContractError):
            make_event(subject=raw_message)
        event = make_event(
            condition={
                "api_key": raw_secret,
                "private_message": raw_message,
                "nested": {"text": raw_message, "state": "offline"},
                "session_token": "token-value-private",
                "error": "private body: sentence with user content",
                "private sentence phrase": "must not survive as a field name",
                "absolute_path": "/home/private-user/session.txt",
            }
        )
        self.assertEqual(event.condition["api_key"], "[REDACTED]")
        self.assertEqual(event.condition["private_message"], "[REDACTED]")
        self.assertEqual(event.condition["nested"]["text"], "[REDACTED]")
        self.assertEqual(event.condition["error"], "[REDACTED]")
        self.assertNotIn("private sentence phrase", event.condition)
        self.assertEqual(event.condition["absolute_path"], "[REDACTED]")
        with self.assertRaises(TypeError):
            event.condition["nested"]["state"] = "tampered"  # type: ignore[index]
        self.store.record_event(event)
        exported = str(event.to_fixture())
        self.assertNotIn(raw_secret, exported)
        self.assertNotIn(raw_message, exported)
        persisted = b"".join(path.read_bytes() for path in self.state_dir.iterdir() if path.is_file())
        self.assertNotIn(raw_secret.encode(), persisted)
        self.assertNotIn(raw_message.encode(), persisted)
        self.assertNotIn(b"token-value-private", persisted)

    def test_append_only_audit_records_and_retention(self) -> None:
        event = self.store.record_event(make_event()).event
        decision = PolicyEvaluator().evaluate(event)
        self.store.append_decision(decision)
        forged = Decision(
            event_id=event.event_id,
            high_level=HighLevelDecision.ACT,
            action_decision=ActionDecision.ACT,
            notification_decision=NotificationDecision.NONE,
            risk=Risk.LOW,
            reason_code="low_risk_action_candidate",
        )
        with self.assertRaises(StoreError):
            self.store.append_decision(forged)
        with self.assertRaises(StoreError):
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE decisions SET reason_code='tampered' WHERE decision_id=?",
                    (decision.decision_id,),
                )
        self.assertEqual(self.store.list_decisions(event.event_id)[0]["reason_code"], decision.reason_code)

        old_event = self.store.record_event(
            make_event(subject="resolved-old", observed_at="2025-01-01T00:00:00Z")
        ).event
        self.store.record_event(
            make_event(
                event_type="service.recovered",
                subject="resolved-old",
                observed_at="2025-01-01T00:10:00Z",
                condition={"state": "healthy"},
                recovery_of=old_event.event_id,
            )
        )
        self.store.increment_metric("shadow.events", day="2024-10-01", now="2024-10-01T00:00:00Z")
        self.store.increment_metric("shadow.events", day="2024-12-01", now="2024-12-01T00:00:00Z")
        removed = self.store.prune_expired(now="2025-02-05T00:00:00Z")
        self.assertEqual(removed["events_and_audit"], 2)
        self.assertIsNone(self.store.get_event(old_event.event_id))
        self.assertEqual(self.store.metric_value("shadow.events", "2024-10-01"), 0)
        self.assertEqual(self.store.metric_value("shadow.events", "2024-12-01"), 1)
        self.assertIsNotNone(self.store.get_event(event.event_id))


class PolicyAndSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = PolicyEvaluator()

    def test_six_high_level_states_and_split_dimensions(self) -> None:
        silent = self.policy.evaluate(make_event())
        self.assertEqual(silent.high_level, HighLevelDecision.SILENT)
        self.assertEqual(silent.action_decision, ActionDecision.NONE)
        self.assertEqual(silent.notification_decision, NotificationDecision.NONE)

        low_action = self.policy.evaluate(
            make_event(action_requested=True, action_scope=ActionScope.RETRY_SAFE)
        )
        self.assertEqual(low_action.high_level, HighLevelDecision.ACT)
        self.assertEqual(low_action.action_decision, ActionDecision.ACT)
        self.assertEqual(low_action.notification_decision, NotificationDecision.NONE)

        action_and_notice = self.policy.evaluate(
            make_event(
                action_requested=True,
                action_scope=ActionScope.RETRY_SAFE,
                notification_requested=True,
            )
        )
        self.assertEqual(action_and_notice.high_level, HighLevelDecision.ACT)
        self.assertEqual(action_and_notice.action_decision, ActionDecision.ACT)
        self.assertEqual(action_and_notice.notification_decision, NotificationDecision.NOTIFY)

        owner_notice = self.policy.evaluate(make_event(notification_requested=True))
        self.assertEqual(owner_notice.high_level, HighLevelDecision.NOTIFY)
        self.assertEqual(owner_notice.action_decision, ActionDecision.NONE)
        self.assertEqual(owner_notice.notification_decision, NotificationDecision.NOTIFY)

        high_action = self.policy.evaluate(
            make_event(
                action_requested=True,
                action_scope=ActionScope.EXTERNAL_COMMUNICATION,
            )
        )
        self.assertEqual(high_action.high_level, HighLevelDecision.ASK)
        self.assertEqual(high_action.action_decision, ActionDecision.ASK)
        self.assertEqual(high_action.risk, Risk.HIGH)
        self.assertEqual(high_action.notification_decision, NotificationDecision.NOTIFY)

        urgent = self.policy.evaluate(make_event(urgent=True))
        self.assertEqual(urgent.high_level, HighLevelDecision.URGENT)
        self.assertEqual(urgent.notification_decision, NotificationDecision.URGENT)

        expired = Event(
            event_type="maintenance.window",
            source="local-test",
            subject="expired-window",
            observed_at=BASE_TIME,
            expires_at="2026-02-03T04:04:00Z",
        )
        # Expiry is decided at evaluation time even before Store materializes EXPIRED.
        ignored = self.policy.evaluate(expired, now=BASE_TIME)
        self.assertEqual(ignored.high_level, HighLevelDecision.IGNORE)

    def test_high_risk_never_auto_authorizes_and_owner_notice_is_not_ask(self) -> None:
        external = self.policy.evaluate(
            make_event(
                action_requested=True,
                action_scope=ActionScope.EXTERNAL_COMMUNICATION,
                notification_requested=True,
            )
        )
        gate = ExecutionGate(KillSwitches(True, True, True)).evaluate(external)
        self.assertFalse(gate.action_execution_allowed)
        self.assertTrue(gate.notification_delivery_allowed)

        owner_notification = self.policy.evaluate(make_event(notification_requested=True))
        self.assertEqual(owner_notification.action_decision, ActionDecision.NONE)
        self.assertNotEqual(owner_notification.high_level, HighLevelDecision.ASK)

    def test_goal_candidate_never_returns_act(self) -> None:
        self.assertEqual(
            evaluate_goal_candidate(
                evidence_changed=False,
                candidate_available=True,
            ).decision,
            GoalCandidateDecision.SILENT,
        )
        self.assertEqual(
            evaluate_goal_candidate(
                evidence_changed=True,
                candidate_available=True,
                requires_user_input=True,
            ).decision,
            GoalCandidateDecision.ASK_CANDIDATE,
        )
        self.assertEqual(
            evaluate_goal_candidate(
                evidence_changed=True,
                candidate_available=True,
                risk=Risk.LOW,
            ).decision,
            GoalCandidateDecision.NOTIFY_CANDIDATE,
        )

    def test_switch_defaults_and_high_action_gate(self) -> None:
        switches = KillSwitches.from_env({})
        self.assertEqual(switches, KillSwitches(False, False, False))
        self.assertFalse(ExecutionGate(switches).evaluate(self.policy.evaluate(make_event())).core_running)
        with self.assertRaises(SwitchConfigurationError):
            KillSwitches.from_env({"PROACTIVE_ENABLED": "maybe"})

        high = self.policy.evaluate(
            make_event(action_requested=True, action_scope=ActionScope.HIGH_RISK)
        )
        enabled = ExecutionGate(KillSwitches(True, True, True)).evaluate(high)
        self.assertFalse(enabled.action_execution_allowed)

    def test_decision_rejects_inconsistent_dimensions(self) -> None:
        with self.assertRaises(ContractError):
            Decision(
                event_id="evt_" + "a" * 32,
                high_level=HighLevelDecision.ACT,
                action_decision=ActionDecision.ACT,
                notification_decision=NotificationDecision.NONE,
                risk=Risk.HIGH,
                reason_code="bad_combination",
            )


class PrivacyAndLLMTests(unittest.TestCase):
    def test_malformed_fixture_and_fingerprint_are_rejected(self) -> None:
        with self.assertRaises(ContractError):
            Event.from_fixture(
                {
                    "event_type": "service.failed",
                    "source": "local-test",
                    "subject": "service-main",
                    "unexpected_body": "must not pass",
                }
            )
        event = make_event()
        with self.assertRaises(ContractError):
            Event(
                event_type=event.event_type,
                source=event.source,
                subject=event.subject,
                observed_at=event.observed_at,
                condition=event.condition,
                fingerprint="sha256:" + "0" * 64,
            )
        with self.assertRaises(ContractError):
            make_event(
                event_type="service.recovered",
                recovery_of="evt_" + "a" * 32,
                action_requested=True,
                action_scope=ActionScope.RETRY_SAFE,
            )

    def test_llm_health_path_and_failures_are_non_authoritative(self) -> None:
        calls: list[dict[str, object]] = []

        def valid(context: dict[str, object]) -> str:
            calls.append(context)
            return '{"next_action_candidate":"review locally","semantic_rank":0.7,"short_summary":"brief"}'

        enricher = OptionalLLMEnricher(valid)
        healthy = enricher.enrich({"state": "failed"}, enabled=True, health_path=True)
        self.assertEqual(healthy.calls, 0)
        self.assertEqual(calls, [])

        enriched = enricher.enrich({"private_message": "private data"}, enabled=True, health_path=False)
        self.assertEqual(enriched.calls, 1)
        self.assertEqual(enriched.next_action_candidate, "review locally")
        self.assertEqual(calls[0]["private_message"], "[REDACTED]")

        before = PolicyEvaluator().evaluate(make_event(action_requested=True, action_scope=ActionScope.HIGH_RISK))
        invalid = OptionalLLMEnricher(lambda _context: '{"action":"do it"}').enrich(
            {"state": "failed"}, enabled=True, health_path=False
        )
        after = PolicyEvaluator().evaluate(make_event(action_requested=True, action_scope=ActionScope.HIGH_RISK))
        self.assertEqual(invalid.error_code, "llm_invalid_json")
        self.assertEqual(invalid.calls, 1)
        self.assertEqual(before.action_decision, after.action_decision)
        self.assertEqual(after.action_decision, ActionDecision.ASK)

        timeout = OptionalLLMEnricher(lambda _context: (_ for _ in ()).throw(TimeoutError())).enrich(
            {}, enabled=True, health_path=False
        )
        unavailable = OptionalLLMEnricher(lambda _context: (_ for _ in ()).throw(RuntimeError())).enrich(
            {}, enabled=True, health_path=False
        )
        self.assertEqual(timeout.error_code, "llm_timeout")
        self.assertEqual(unavailable.error_code, "llm_unavailable")

        with tempfile.TemporaryDirectory() as temp:
            store = SQLiteEventStore(Path(temp) / "store")
            event = store.record_event(make_event()).event
            decision = PolicyEvaluator().evaluate(event)
            store.append_decision(decision)
            logger = OptionalLLMEnricher(lambda _context: (_ for _ in ()).throw(TimeoutError()))
            logged = logger.enrich_and_log(
                {},
                store=store,
                event_id=event.event_id,
                stage="SHORT_SUMMARY",
                enabled=True,
                health_path=False,
                occurred_at=BASE_TIME,
            )
            outcomes = store.list_llm_outcomes(event.event_id)
            self.assertEqual(outcomes[0]["outcome_code"], "llm_timeout")
            self.assertEqual(outcomes[0]["calls"], 1)
            self.assertEqual(store.list_decisions(event.event_id)[0]["high_level"], "SILENT")
            self.assertEqual(logged.error_code, "llm_timeout")


class BudgetAndCircuitTests(StoreTestCase):
    def test_notification_budget_normal_urgent_cooldown_escalation_and_rates(self) -> None:
        budget = NotificationBudget()
        normal_history = [
            make_attempt(i, at=f"2026-02-03T{8 + i:02d}:00:00Z")
            for i in range(1, 4)
        ]
        normal_exhausted = budget.evaluate(
            decision=NotificationDecision.NOTIFY,
            fingerprint="sha256:" + "f" * 64,
            escalation_level=0,
            now="2026-02-03T12:00:00Z",
            history=normal_history,
        )
        self.assertFalse(normal_exhausted.allowed)
        self.assertEqual(normal_exhausted.reason_code, "normal_daily_budget_exhausted")

        urgent_with_normal_full = budget.evaluate(
            decision=NotificationDecision.URGENT,
            fingerprint="sha256:" + "e" * 64,
            escalation_level=0,
            now="2026-02-03T12:00:00Z",
            history=normal_history,
        )
        self.assertTrue(urgent_with_normal_full.allowed)

        urgent_history = [
            make_attempt(
                20,
                decision=NotificationDecision.URGENT,
                at="2026-02-03T10:00:00Z",
                escalation=0,
                fingerprint_suffix="a" * 64,
            )
        ]
        cooldown = budget.evaluate(
            decision=NotificationDecision.URGENT,
            fingerprint="sha256:" + "a" * 64,
            escalation_level=0,
            now="2026-02-03T10:05:00Z",
            history=urgent_history,
        )
        self.assertEqual(cooldown.reason_code, "urgent_fingerprint_cooldown")
        escalated = budget.evaluate(
            decision=NotificationDecision.URGENT,
            fingerprint="sha256:" + "a" * 64,
            escalation_level=1,
            now="2026-02-03T10:05:00Z",
            history=urgent_history,
        )
        self.assertTrue(escalated.allowed)

        hourly_history = [
            *urgent_history,
            make_attempt(
                21,
                decision=NotificationDecision.URGENT,
                at="2026-02-03T10:10:00Z",
                fingerprint_suffix="b" * 64,
            ),
        ]
        hourly = budget.evaluate(
            decision=NotificationDecision.URGENT,
            fingerprint="sha256:" + "c" * 64,
            escalation_level=0,
            now="2026-02-03T10:20:00Z",
            history=hourly_history,
        )
        self.assertEqual(hourly.reason_code, "urgent_hourly_rate_limited")

        daily_history = [
            make_attempt(
                30 + index,
                decision=NotificationDecision.URGENT,
                at=f"2026-02-03T{index * 3:02d}:00:00Z",
                fingerprint_suffix=f"{index + 1:064x}",
            )
            for index in range(5)
        ]
        daily = budget.evaluate(
            decision=NotificationDecision.URGENT,
            fingerprint="sha256:" + "9" * 64,
            escalation_level=0,
            now="2026-02-03T15:00:00Z",
            history=daily_history,
        )
        self.assertEqual(daily.reason_code, "urgent_daily_rate_limited")

    def test_shadow_budget_suppresses_fourth_ordinary_candidate(self) -> None:
        detector = ShadowDetector(self.store, KillSwitches(proactive_enabled=True))
        events = [
            make_event(
                subject=f"service-{number}",
                condition={"state": "degraded", "index": number},
                notification_requested=True,
                observed_at=f"2026-02-03T0{number + 1}:00:00Z",
            )
            for number in range(4)
        ]
        result = detector.run(events, now="2026-02-03T12:00:00Z")
        self.assertEqual(result.notification_candidates_created, 4)
        self.assertEqual(self.store.notification_candidate_count(), 4)
        self.assertEqual(result.stdout_text.count("Shadow通知候选"), 3)
        suppressed = [
            item for item in self.store.notification_history()
            if item.state == NotificationState.SUPPRESSED
        ]
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(suppressed[0].detail_code, "normal_daily_budget_exhausted")
        self.assertEqual(result.notifications_sent, 0)

    def test_circuit_breaker_open_half_open_and_reset(self) -> None:
        breaker = CircuitBreaker(threshold=2, reset_after_seconds=60)
        self.assertTrue(breaker.allow(now="2026-02-03T10:00:00Z"))
        breaker.record_failure("store_busy", now="2026-02-03T10:00:00Z")
        self.assertTrue(breaker.allow(now="2026-02-03T10:00:01Z"))
        opened = breaker.record_failure("store_busy", now="2026-02-03T10:00:01Z")
        self.assertEqual(opened.state, "OPEN")
        self.assertFalse(breaker.allow(now="2026-02-03T10:00:30Z"))
        self.assertTrue(breaker.allow(now="2026-02-03T10:01:02Z"))
        self.assertEqual(breaker.snapshot().state, "HALF_OPEN")
        self.assertFalse(breaker.allow(now="2026-02-03T10:01:03Z"))
        self.assertEqual(breaker.record_success().state, "CLOSED")

    def test_circuit_breaker_allows_only_one_concurrent_half_open_probe(self) -> None:
        breaker = CircuitBreaker(threshold=1, reset_after_seconds=60)
        breaker.record_failure("store_busy", now="2026-02-03T10:00:00Z")
        with ThreadPoolExecutor(max_workers=8) as pool:
            allowed = list(
                pool.map(
                    lambda _: breaker.allow(now="2026-02-03T10:02:00Z"),
                    range(16),
                )
            )
        self.assertEqual(sum(allowed), 1)
        self.assertEqual(breaker.snapshot().state, "HALF_OPEN")

    def test_shadow_store_failures_open_circuit(self) -> None:
        other_store = SQLiteEventStore(self.state_dir, busy_timeout_ms=50)
        breaker = CircuitBreaker(threshold=2, reset_after_seconds=60)
        detector = ShadowDetector(
            other_store,
            KillSwitches(proactive_enabled=True),
            circuit_breaker=breaker,
        )
        blocker = self.store._connect()
        blocker.execute("BEGIN IMMEDIATE")
        try:
            first = detector.run([make_event()], now="2026-02-03T10:00:00Z")
            second = detector.run([make_event()], now="2026-02-03T10:00:01Z")
            third = detector.run([make_event()], now="2026-02-03T10:00:02Z")
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        self.assertEqual(first.error_code, "event_store_busy")
        self.assertEqual(second.error_code, "event_store_busy")
        self.assertEqual(third.error_code, "circuit_open")
        self.assertEqual(third.stdout_text, "")


class ShadowAndRendererTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.detector = ShadowDetector(
            self.store,
            KillSwitches(proactive_enabled=True, notifications_enabled=True, actions_enabled=True),
        )

    def test_same_input_has_same_fingerprint_and_decision_and_dedupes_candidate(self) -> None:
        first_event = make_event(notification_requested=True)
        second_event = make_event(
            notification_requested=True,
            observed_at="2026-02-03T04:06:00Z",
        )
        first_decision = PolicyEvaluator().evaluate(first_event)
        second_decision = PolicyEvaluator().evaluate(second_event)
        self.assertEqual(first_event.fingerprint, second_event.fingerprint)
        self.assertEqual(
            (
                first_decision.high_level,
                first_decision.action_decision,
                first_decision.notification_decision,
                first_decision.reason_code,
            ),
            (
                second_decision.high_level,
                second_decision.action_decision,
                second_decision.notification_decision,
                second_decision.reason_code,
            ),
        )
        first = self.detector.run([first_event], now="2026-02-03T12:00:00Z")
        repeated = self.detector.run([second_event], now="2026-02-03T12:00:00Z")
        self.assertEqual(first.notification_candidates_created, 1)
        self.assertEqual(repeated.notification_candidates_created, 0)
        self.assertEqual(self.store.notification_candidate_count(), 1)
        self.assertNotEqual(first.stdout_text, "")
        self.assertEqual(repeated.stdout_text, "")
        self.assertEqual(repeated.llm_calls, 0)
        self.assertEqual(repeated.notifications_sent, 0)
        self.assertEqual(repeated.actions_executed, 0)
        self.assertEqual(repeated.action_candidates_created, 0)
        saved_decisions = self.store.list_decisions()
        for key in ("high_level", "action_decision", "notification_decision", "risk", "reason_code", "evaluated_at"):
            self.assertEqual(saved_decisions[0][key], saved_decisions[1][key])

    def test_shadow_act_notify_records_decision_but_never_action(self) -> None:
        result = self.detector.run(
            [
                make_event(
                    subject="shadow-act-notify",
                    action_requested=True,
                    action_scope=ActionScope.RETRY_SAFE,
                    notification_requested=True,
                )
            ]
        )
        decision = self.store.list_decisions()[0]
        self.assertEqual(decision["high_level"], HighLevelDecision.ACT.value)
        self.assertEqual(decision["action_decision"], ActionDecision.ACT.value)
        self.assertEqual(decision["notification_decision"], NotificationDecision.NOTIFY.value)
        self.assertIn("低风险动作仅作候选，本阶段未执行", result.stdout_text)
        self.assertEqual(result.action_candidates_created, 0)
        self.assertEqual(result.actions_executed, 0)
        self.assertEqual(result.notifications_sent, 0)
        with self.store.reader() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM action_transitions").fetchone()[0],
                0,
            )

    def test_recovery_candidate_occurs_once_and_no_event_is_silent(self) -> None:
        no_event = self.detector.run([])
        self.assertEqual(no_event.stdout_text, "")
        self.assertEqual(no_event.processed_events, 0)

        first = self.detector.run([make_event()])
        self.assertEqual(first.stdout_text, "")
        failure = self.store.list_events()[0]
        recovery_input = make_event(
            event_type="service.recovered",
            subject="service-main",
            observed_at="2026-02-03T04:15:00Z",
            condition={"state": "healthy"},
            notification_requested=True,
            recovery_of=failure.event_id,
        )
        recovered = self.detector.run([recovery_input])
        repeated = self.detector.run(
            [
                make_event(
                    event_type="service.recovered",
                    subject="service-main",
                    observed_at="2026-02-03T04:16:00Z",
                    condition={"state": "healthy"},
                    notification_requested=True,
                    recovery_of=failure.event_id,
                )
            ]
        )
        self.assertEqual(recovered.notification_candidates_created, 1)
        self.assertIn("状态恢复", recovered.stdout_text)
        self.assertEqual(repeated.notification_candidates_created, 0)
        self.assertEqual(self.store.notification_candidate_count(), 1)
        self.assertEqual(repeated.stdout_text, "")
        self.assertEqual(recovered.notifications_sent, 0)
        self.assertEqual(recovered.actions_executed, 0)

    def test_shadow_expires_event_before_policy_and_never_notifies_it(self) -> None:
        result = self.detector.run(
            [
                make_event(
                    subject="expired-notice",
                    notification_requested=True,
                    observed_at="2026-02-03T04:05:00Z",
                    expires_at="2026-02-03T04:06:00Z",
                )
            ],
            now="2026-02-03T04:07:00Z",
        )
        stored = self.store.list_events()[0]
        decision = self.store.list_decisions()[0]
        self.assertEqual(stored.status, EventStatus.EXPIRED)
        self.assertEqual(decision["high_level"], HighLevelDecision.IGNORE.value)
        self.assertEqual(result.notification_candidates_created, 0)
        self.assertEqual(result.stdout_text, "")

    def test_urgent_fingerprint_cooldown_crosses_utc_day_boundary(self) -> None:
        first = self.detector.run(
            [make_event(urgent=True, observed_at="2026-02-03T23:00:00Z")],
            now="2026-02-03T23:00:00Z",
        )
        self.assertEqual(first.notification_candidates_created, 1)
        parent = self.store.list_events()[0]
        self.detector.run(
            [
                make_event(
                    event_type="service.recovered",
                    observed_at="2026-02-03T23:15:00Z",
                    condition={"state": "healthy"},
                    recovery_of=parent.event_id,
                )
            ],
            now="2026-02-03T23:15:00Z",
        )
        refailure = self.detector.run(
            [make_event(urgent=True, observed_at="2026-02-04T02:00:00Z")],
            now="2026-02-04T02:00:00Z",
        )
        self.assertEqual(refailure.notification_candidates_created, 1)
        self.assertEqual(refailure.stdout_text, "")
        self.assertEqual(self.store.notification_candidate_count(), 2)
        latest = self.store.notification_history()[-1]
        self.assertEqual(latest.state, NotificationState.SUPPRESSED)
        self.assertEqual(latest.detail_code, "urgent_fingerprint_cooldown")

    def test_renderer_stdout_contract_for_silent_notify_ask_urgent(self) -> None:
        renderer = NotificationRenderer()
        silent_event = make_event()
        silent = PolicyEvaluator().evaluate(silent_event)
        self.assertEqual(renderer.render(silent_event, silent), "")

        scenarios = [
            make_event(subject="notify-service", notification_requested=True),
            make_event(
                subject="ask-service",
                action_requested=True,
                action_scope=ActionScope.HIGH_RISK,
            ),
            make_event(subject="urgent-service", urgent=True),
            make_event(
                subject="act-notify-service",
                action_requested=True,
                action_scope=ActionScope.RETRY_SAFE,
                notification_requested=True,
            ),
        ]
        rendered = []
        for event in scenarios:
            decision = PolicyEvaluator().evaluate(event)
            text = renderer.render(event, decision)
            self.assertTrue(text.startswith("【Shadow"))
            self.assertNotIn(event.subject, text)
            self.assertNotIn(event.source, text)
            self.assertNotIn("condition", text)
            rendered.append(text)
        self.assertIn("Shadow通知候选", rendered[0])
        self.assertIn("Shadow审批候选", rendered[1])
        self.assertIn("Shadow紧急候选", rendered[2])
        self.assertIn("低风险动作仅作候选，本阶段未执行", rendered[3])

    def test_disabled_core_does_not_consume_input_or_create_store(self) -> None:
        seen: list[str] = []

        def inputs():
            seen.append("consumed")
            yield make_event(notification_requested=True)

        disabled = ShadowDetector(None, KillSwitches())
        result = disabled.run(inputs())
        self.assertEqual(seen, [])
        self.assertEqual(result.stdout_text, "")
        self.assertEqual(result.processed_events, 0)
        self.assertEqual(result.notification_candidates_created, 0)
        self.assertEqual(result.llm_calls, 0)
        self.assertEqual(result.notifications_sent, 0)
        self.assertEqual(result.actions_executed, 0)

    def test_invalid_shadow_input_fails_closed_without_stdout(self) -> None:
        result = self.detector.run([object()])  # type: ignore[list-item]
        self.assertEqual(result.error_code, "shadow_input_or_store_error")
        self.assertEqual(result.stdout_text, "")
        self.assertEqual(result.notifications_sent, 0)
        self.assertEqual(result.actions_executed, 0)

    def test_cli_default_and_empty_health_fixture_have_empty_stdout(self) -> None:
        state_dir = Path(self.temp.name) / "cli-state"
        env = os.environ.copy()
        env["PROACTIVE_ENABLED"] = "false"
        env["PROACTIVE_NOTIFICATIONS_ENABLED"] = "false"
        env["PROACTIVE_ACTIONS_ENABLED"] = "false"
        default = subprocess.run(
            [sys.executable, str(ROOT / "run_shadow.py")],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(default.stdout, "")
        self.assertFalse(state_dir.exists())

        env["PROACTIVE_ENABLED"] = "true"
        with tempfile.TemporaryDirectory(dir=ROOT) as local_root:
            local_state_dir = Path(local_root) / "cli-state"
            enabled = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "run_shadow.py"),
                    "--enable-shadow",
                    "--fixture",
                    str(ROOT / "fixtures" / "health_empty.json"),
                    "--state-dir",
                    str(local_state_dir),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(enabled.stdout, "")
            self.assertEqual(enabled.stderr, "")
            self.assertTrue(local_state_dir.exists())

            env["PROACTIVE_NOTIFICATIONS_ENABLED"] = "true"
            env["PROACTIVE_ACTIONS_ENABLED"] = "true"
            notice_state_dir = Path(local_root) / "notice-state"
            notice = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "run_shadow.py"),
                    "--enable-shadow",
                    "--fixture",
                    str(ROOT / "fixtures" / "notify_candidate.json"),
                    "--state-dir",
                    str(notice_state_dir),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(
                notice.stdout,
                "【Shadow通知候选】检测到需要关注的系统状态变化。本阶段未发送消息。\n",
            )
            self.assertEqual(notice.stderr, "")
            audit_store = SQLiteEventStore(notice_state_dir)
            self.assertEqual(audit_store.notification_candidate_count(), 1)
            with audit_store.reader() as connection:
                delivery_count = connection.execute(
                    "SELECT COUNT(*) AS n FROM notification_transitions"
                ).fetchone()["n"]
                action_count = connection.execute(
                    "SELECT COUNT(*) AS n FROM action_transitions"
                ).fetchone()["n"]
            self.assertEqual(delivery_count, 0)
            self.assertEqual(action_count, 0)

        outside = subprocess.run(
            [
                sys.executable,
                str(ROOT / "run_shadow.py"),
                "--enable-shadow",
                "--fixture",
                str(ROOT / "fixtures" / "health_empty.json"),
                "--state-dir",
                str(state_dir),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(outside.returncode, 2)
        self.assertEqual(outside.stdout, "")
        self.assertFalse(state_dir.exists())


if __name__ == "__main__":
    unittest.main()
