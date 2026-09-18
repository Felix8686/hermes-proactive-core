from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from proactive_core.model import (
    ActionScope,
    Event,
    NotificationState,
    Severity,
)
from proactive_core.phase4 import (
    PHASE4_TEST_EVENT_ID,
    PHASE4_TEST_TEXT,
    CronDeliveryObservation,
    Phase4Config,
    Phase4ConfigError,
    Phase4NotificationRunner,
    read_cron_delivery_observation,
    reconcile_pending_deliveries,
    synthetic_delivery_test_event,
)
from proactive_core.store import SQLiteEventStore
from proactive_core.switches import KillSwitches


ROOT = Path(__file__).resolve().parents[1]
BASE_TIME = "2026-09-18T00:00:00.000000Z"
TARGET = "telegram:existing-owner-topic"


def make_event(
    event_type: str = "cron.failure",
    *,
    source: str = "vps_cron",
    subject: str = "cron-0123456789abcdef",
    state: str = "failed",
    observed_at: str = BASE_TIME,
    **overrides: object,
) -> Event:
    values: dict[str, object] = {
        "event_type": event_type,
        "source": source,
        "subject": subject,
        "observed_at": observed_at,
        "severity": Severity.MEDIUM,
        "condition": {"state": state},
        "notification_requested": True,
    }
    values.update(overrides)
    return Event(**values)  # type: ignore[arg-type]


class Phase4TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteEventStore(Path(self.temp.name) / "state")
        self.switches = KillSwitches(True, True, False)
        self.runner = Phase4NotificationRunner(
            self.store,
            self.switches,
            target_verified=True,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def transitions(self, candidate_id: str | None = None):
        items = self.store.notification_history()
        return [item for item in items if candidate_id is None or item.candidate_id == candidate_id]

    def test_silent_empty_stdout_is_hard_contract(self) -> None:
        result = self.runner.run_snapshot(
            {"cron_jobs": []},
            openviking_healthy=True,
            now=BASE_TIME,
        )
        self.assertEqual(result.stdout_text, "")
        self.assertEqual(result.notification_candidates_created, 0)
        self.assertEqual(result.llm_calls, 0)
        self.assertEqual(result.actions_executed, 0)

    def test_phase4a_exact_message_and_duplicate_suppression(self) -> None:
        test_event = synthetic_delivery_test_event(BASE_TIME)
        self.assertEqual(test_event.event_id, PHASE4_TEST_EVENT_ID)
        first = self.runner.run_events(
            [test_event],
            now=BASE_TIME,
            allow_test_event=True,
        )
        self.assertEqual(first.stdout_text, PHASE4_TEST_TEXT + "\n")
        self.assertEqual(first.notification_candidates_created, 1)
        pending = self.transitions()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].state, NotificationState.PENDING)
        expected_hash = hashlib.sha256(PHASE4_TEST_TEXT.encode("utf-8")).hexdigest()
        self.assertEqual(pending[0].detail_code, f"cron_stdout_pending:{expected_hash}")

        observation = CronDeliveryObservation(
            route_verified=True,
            last_run_at="2026-09-18T00:00:03.000000Z",
            last_status="ok",
            delivery_error_known=True,
            delivery_failed=False,
            last_output_sha256=expected_hash,
            last_output_mtime="2026-09-18T00:00:02.000000Z",
        )
        reconciled = reconcile_pending_deliveries(
            self.store,
            observation,
            now="2026-09-18T00:00:04Z",
        )
        self.assertEqual((reconciled.sent, reconciled.failed), (1, 0))
        self.assertEqual(self.transitions()[-1].state, NotificationState.SENT)

        replay_event = self.store.get_event(PHASE4_TEST_EVENT_ID)
        self.assertIsNotNone(replay_event)
        self.assertEqual(replay_event.event_id, test_event.event_id)  # type: ignore[union-attr]
        self.assertEqual(replay_event.observed_at, test_event.observed_at)  # type: ignore[union-attr]
        self.assertEqual(replay_event.expires_at, test_event.expires_at)  # type: ignore[union-attr]
        replay = self.runner.run_events(
            [replay_event],  # type: ignore[list-item]
            now="2026-09-18T00:00:05Z",
            allow_test_event=True,
        )
        self.assertEqual(replay.stdout_text, "")
        self.assertEqual(replay.notification_candidates_created, 0)
        self.assertEqual(len(self.transitions()), 2)
        self.assertEqual(self.transitions()[-1].state, NotificationState.SENT)

    def test_duplicate_pending_candidate_never_emits_twice(self) -> None:
        first = self.runner.run_events([make_event()], now=BASE_TIME)
        second = self.runner.run_events(
            [make_event(observed_at="2026-09-18T00:01:00Z")],
            now="2026-09-18T00:01:00Z",
        )
        self.assertTrue(first.stdout_text)
        self.assertEqual(second.stdout_text, "")
        self.assertEqual(len(self.transitions()), 1)

    def test_reconcile_requires_exact_output_and_delivery_success(self) -> None:
        result = self.runner.run_events([make_event()], now=BASE_TIME)
        pending = self.transitions()[0]
        digest = pending.detail_code.split(":", maxsplit=1)[1]
        wrong_output = CronDeliveryObservation(
            True,
            "2026-09-18T00:00:03Z",
            "ok",
            True,
            False,
            "0" * 64,
            "2026-09-18T00:00:02Z",
        )
        waiting = reconcile_pending_deliveries(self.store, wrong_output, now="2026-09-18T00:00:04Z")
        self.assertEqual(waiting.sent, 0)
        self.assertEqual(waiting.waiting, 1)
        self.assertEqual(self.transitions()[-1].state, NotificationState.PENDING)

        failed_delivery = CronDeliveryObservation(
            True,
            "2026-09-18T00:00:03Z",
            "ok",
            True,
            True,
            digest,
            "2026-09-18T00:00:02Z",
        )
        failed = reconcile_pending_deliveries(
            self.store,
            failed_delivery,
            now="2026-09-18T00:00:04Z",
        )
        self.assertEqual((failed.sent, failed.failed), (0, 1))
        self.assertEqual(self.transitions()[-1].state, NotificationState.FAILED)
        self.assertTrue(result.stdout_text)

    def test_delivery_output_from_later_silent_tick_cannot_mark_old_candidate_sent(self) -> None:
        self.runner.run_events([make_event()], now=BASE_TIME)
        digest = self.transitions()[0].detail_code.split(":", maxsplit=1)[1]
        later = CronDeliveryObservation(
            True,
            "2026-09-18T02:00:03Z",
            "ok",
            True,
            False,
            digest,
            "2026-09-18T02:00:02Z",
        )
        result = reconcile_pending_deliveries(
            self.store,
            later,
            now="2026-09-18T02:00:04Z",
        )
        self.assertEqual(result.sent, 0)
        self.assertEqual(result.waiting, 1)
        self.assertEqual(self.transitions()[-1].state, NotificationState.PENDING)

    def test_normal_same_fingerprint_cooldown_applies_after_recovery(self) -> None:
        original = make_event()
        first = self.runner.run_events([original], now=BASE_TIME)
        candidate_id = self.transitions()[0].candidate_id
        stored = self.store.open_event_by_fingerprint(original.fingerprint)
        self.assertIsNotNone(stored)
        self.store.append_notification_transition(
            candidate_id,
            NotificationState.SENT,
            occurred_at="2026-09-18T00:00:02Z",
            detail_code="test_adapter_success",
        )
        self.store.record_event(
            Event(
                event_type="cron.recovered",
                source=original.source,
                subject=original.subject,
                observed_at="2026-09-18T00:01:00Z",
                severity=Severity.MEDIUM,
                condition={"state": "healthy"},
                recovery_of=stored.event_id,  # type: ignore[union-attr]
            )
        )
        refailure = make_event(observed_at="2026-09-18T00:02:00Z")
        second = self.runner.run_events([refailure], now="2026-09-18T00:02:00Z")
        self.assertTrue(first.stdout_text)
        self.assertEqual(second.stdout_text, "")
        latest_by_candidate = {item.candidate_id: item for item in self.transitions()}
        suppressed = [item for item in latest_by_candidate.values() if item.state == NotificationState.SUPPRESSED]
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(suppressed[0].detail_code, "normal_fingerprint_cooldown")

    def test_normal_fingerprint_cooldown_crosses_utc_day_boundary(self) -> None:
        original = make_event(observed_at="2026-09-17T23:55:00Z")
        self.runner.run_events([original], now="2026-09-17T23:55:00Z")
        candidate_id = self.transitions()[0].candidate_id
        stored = self.store.open_event_by_fingerprint(original.fingerprint)
        self.assertIsNotNone(stored)
        self.store.append_notification_transition(
            candidate_id,
            NotificationState.SENT,
            occurred_at="2026-09-17T23:55:02Z",
            detail_code="test_adapter_success",
        )
        self.store.record_event(
            Event(
                event_type="cron.recovered",
                source=original.source,
                subject=original.subject,
                observed_at="2026-09-17T23:57:00Z",
                severity=Severity.MEDIUM,
                condition={"state": "healthy"},
                recovery_of=stored.event_id,  # type: ignore[union-attr]
            )
        )
        second = self.runner.run_events(
            [make_event(observed_at="2026-09-18T00:02:00Z")],
            now="2026-09-18T00:02:00Z",
        )
        self.assertEqual(second.stdout_text, "")
        latest_by_candidate = {item.candidate_id: item for item in self.transitions()}
        suppressed = [item for item in latest_by_candidate.values() if item.state == NotificationState.SUPPRESSED]
        self.assertEqual(len(suppressed), 1)
        self.assertEqual(suppressed[0].detail_code, "normal_fingerprint_cooldown")

    def test_circuit_breaker_open_notification_is_deduplicated(self) -> None:
        first = self.runner.run_snapshot(
            {"cron_jobs": []},
            openviking_healthy=True,
            circuit_open=True,
            now=BASE_TIME,
        )
        repeated = self.runner.run_snapshot(
            {"cron_jobs": []},
            openviking_healthy=True,
            circuit_open=True,
            now="2026-09-18T00:01:00Z",
        )
        self.assertEqual(first.stdout_text, "【主动巡检】主动巡检暂停保护1项。\n")
        self.assertEqual(repeated.stdout_text, "")
        self.assertEqual(first.notification_candidates_created, 1)
        self.assertEqual(repeated.notification_candidates_created, 0)

    def test_failure_recovery_notifies_once(self) -> None:
        failure = self.runner.run_snapshot(
            {
                "cron_jobs": [
                    {
                        "enabled": True,
                        "id": "sensitive-job-name",
                        "last_status": "error",
                        "failure_streak": 1,
                        "has_delivery_err": False,
                    }
                ]
            },
            openviking_healthy=None,
            now=BASE_TIME,
        )
        self.assertEqual(failure.stdout_text, "【主动巡检】Cron任务异常1项。\n")
        self.assertNotIn("sensitive-job-name", failure.stdout_text)

        recovered = self.runner.run_snapshot(
            {
                "cron_jobs": [
                    {
                        "enabled": True,
                        "id": "sensitive-job-name",
                        "last_status": "ok",
                        "failure_streak": 0,
                        "has_delivery_err": False,
                    }
                ]
            },
            openviking_healthy=None,
            now="2026-09-18T00:02:00Z",
        )
        self.assertEqual(recovered.stdout_text, "【主动巡检】Cron任务恢复1项。\n")
        candidate = self.transitions()[-1].candidate_id
        self.store.append_notification_transition(
            candidate,
            NotificationState.SENT,
            occurred_at="2026-09-18T00:02:03Z",
            detail_code="test_adapter_success",
        )

        recovered_again = self.runner.run_snapshot(
            {
                "cron_jobs": [
                    {
                        "enabled": True,
                        "id": "sensitive-job-name",
                        "last_status": "ok",
                        "failure_streak": 0,
                        "has_delivery_err": False,
                    }
                ]
            },
            openviking_healthy=None,
            now="2026-09-18T00:03:00Z",
        )
        self.assertEqual(recovered_again.stdout_text, "")
        self.assertEqual(sum(item.state == NotificationState.PENDING for item in self.transitions()), 2)

    def test_aggregation_uses_only_controlled_labels(self) -> None:
        result = self.runner.run_events(
            [
                make_event(subject="cron-a"),
                make_event(
                    "openviking.unhealthy",
                    source="openviking",
                    subject="openviking-service",
                    state="unhealthy",
                ),
            ],
            now=BASE_TIME,
        )
        self.assertEqual(
            result.stdout_text,
            "【主动巡检】Cron任务异常1项；OpenViking异常1项。\n",
        )
        self.assertEqual(result.stdout_text.count("\n"), 1)

    def test_ordinary_budget_three_and_failed_delivery_does_not_consume_quota(self) -> None:
        first = self.runner.run_events([make_event(subject="cron-a")], now=BASE_TIME)
        first_candidate = self.transitions()[0].candidate_id
        self.store.append_notification_transition(
            first_candidate,
            NotificationState.FAILED,
            occurred_at="2026-09-18T00:00:02Z",
            detail_code="test_delivery_failure",
        )
        self.assertTrue(first.stdout_text)

        for index in range(3):
            result = self.runner.run_events(
                [make_event(subject=f"cron-{index + 1}")],
                now=f"2026-09-18T00:0{index + 1}:00Z",
            )
            self.assertTrue(result.stdout_text)
        fourth = self.runner.run_events(
            [make_event(subject="cron-fourth")],
            now="2026-09-18T00:04:00Z",
        )
        self.assertEqual(fourth.stdout_text, "")
        latest_by_candidate = {item.candidate_id: item for item in self.transitions()}
        self.assertEqual(
            sum(item.state == NotificationState.PENDING for item in latest_by_candidate.values()),
            3,
        )
        self.assertEqual(
            sum(item.state == NotificationState.FAILED for item in self.transitions()),
            1,
        )

    def test_actions_switch_and_high_risk_event_fail_closed(self) -> None:
        actions_on = Phase4NotificationRunner(
            self.store,
            KillSwitches(True, True, True),
            target_verified=True,
        )
        blocked = actions_on.run_events([make_event()], now=BASE_TIME)
        self.assertEqual(blocked.stdout_text, "")
        self.assertEqual(blocked.error_code, "actions_switch_must_remain_false")

        high_risk = make_event(
            action_requested=True,
            action_scope=ActionScope.EXTERNAL_COMMUNICATION,
        )
        rejected = self.runner.run_events([high_risk], now=BASE_TIME)
        self.assertEqual(rejected.stdout_text, "")
        self.assertEqual(rejected.error_code, "phase4_processing_failed")
        with self.store.reader() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_privacy_redaction_and_malformed_probe(self) -> None:
        secret = "private-recipient@example.invalid"
        result = self.runner.run_snapshot(
            {
                "cron_jobs": [
                    {
                        "enabled": True,
                        "id": secret,
                        "last_status": f"error {secret}",
                        "failure_streak": 1,
                        "has_delivery_err": False,
                    }
                ],
                "private_payload": secret,
            },
            openviking_healthy=None,
            now=BASE_TIME,
        )
        self.assertNotIn(secret, result.stdout_text)
        with self.store.reader() as connection:
            text = " ".join(
                str(value)
                for row in connection.execute("SELECT event_type, source, subject FROM events")
                for value in row
            )
        self.assertNotIn(secret, text)

        malformed = self.runner.run_snapshot(
            {"cron_jobs": [{"enabled": True, "id": "bad"}]},
            openviking_healthy=None,
            now="2026-09-18T00:01:00Z",
        )
        self.assertEqual(malformed.stdout_text, "")
        self.assertEqual(malformed.error_code, "malformed_probe")

    def test_core_disabled_and_target_unverified_are_silent(self) -> None:
        disabled = Phase4NotificationRunner(
            self.store,
            KillSwitches(False, True, False),
            target_verified=True,
        ).run_events([make_event()], now=BASE_TIME)
        self.assertEqual(disabled.stdout_text, "")
        unverified = Phase4NotificationRunner(
            self.store,
            self.switches,
            target_verified=False,
        ).run_events([make_event()], now=BASE_TIME)
        self.assertEqual(unverified.stdout_text, "")
        self.assertEqual(unverified.error_code, "delivery_target_unverified")
        with self.store.reader() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_cron_route_and_output_hash_are_verified_without_exposing_target(self) -> None:
        home = Path(self.temp.name) / "hermes"
        job_id = "job-phase4-test"
        output_dir = home / "cron" / "output" / job_id
        output_dir.mkdir(parents=True)
        output = PHASE4_TEST_TEXT.encode("utf-8")
        output_path = output_dir / "2026-09-18_00-00-02.md"
        output_path.write_bytes(output)
        run_time = datetime(2026, 9, 18, 0, 0, 3, tzinfo=timezone.utc).timestamp()
        os.utime(output_path, (run_time - 1, run_time - 1))
        target_hash = hashlib.sha256(TARGET.encode("utf-8")).hexdigest()
        jobs_path = home / "cron" / "jobs.json"
        jobs_path.parent.mkdir(parents=True, exist_ok=True)
        jobs_path.write_text(
            json.dumps(
                {
                    "jobs": [
                        {
                            "id": job_id,
                            "name": "proactive-review-v1",
                            "enabled": True,
                            "no_agent": True,
                            "script": "proactive_core_v1_runner.py",
                            "deliver": TARGET,
                            "last_run_at": "2026-09-18T00:00:03Z",
                            "last_status": "ok",
                            "last_delivery_error": None,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        observation = read_cron_delivery_observation(home, trusted_target_sha256=target_hash)
        self.assertTrue(observation.route_verified)
        self.assertEqual(observation.last_output_sha256, hashlib.sha256(output).hexdigest())
        self.assertNotIn(TARGET, repr(observation))

    def test_no_agent_cron_envelope_reconciles_exact_stdout(self) -> None:
        home = Path(self.temp.name) / "hermes-envelope"
        job_id = "job-phase4-envelope"
        output_dir = home / "cron" / "output" / job_id
        output_dir.mkdir(parents=True)
        run_time = "2026-09-18T00:00:03Z"
        output_path = output_dir / "2026-09-18_00-00-02.md"
        result = self.runner.run_events([make_event()], now=BASE_TIME)
        rendered = result.stdout_text.removesuffix("\n")
        target_hash = hashlib.sha256(TARGET.encode("utf-8")).hexdigest()

        def cron_document(document_job_id: str) -> str:
            return (
                "# Cron Job: proactive-review-v1\n\n"
                f"**Job ID:** {document_job_id}\n"
                f"**Run Time:** {run_time}\n"
                "**Mode:** no_agent (script)\n\n"
                "---\n\n"
                f"{rendered}\n"
            )

        output_path.write_bytes(cron_document("another-job").encode("utf-8"))
        output_time = datetime(2026, 9, 18, 0, 0, 2, tzinfo=timezone.utc).timestamp()
        os.utime(output_path, (output_time, output_time))
        jobs_path = home / "cron" / "jobs.json"
        jobs_path.parent.mkdir(parents=True, exist_ok=True)
        jobs_path.write_text(
            json.dumps(
                {
                    "jobs": [
                        {
                            "id": job_id,
                            "name": "proactive-review-v1",
                            "enabled": True,
                            "no_agent": True,
                            "script": "proactive_core_v1_runner.py",
                            "deliver": TARGET,
                            "last_run_at": run_time,
                            "last_status": "ok",
                            "last_delivery_error": None,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        wrong_header = read_cron_delivery_observation(
            home,
            trusted_target_sha256=target_hash,
        )
        self.assertIsNone(wrong_header.last_output_sha256)

        output_path.write_bytes(cron_document(job_id).encode("utf-8"))
        os.utime(output_path, (output_time, output_time))
        observation = read_cron_delivery_observation(
            home,
            trusted_target_sha256=target_hash,
        )
        self.assertEqual(
            observation.last_output_sha256,
            hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            repr(observation),
        )
        reconciled = reconcile_pending_deliveries(
            self.store,
            observation,
            now="2026-09-18T00:00:04Z",
        )
        self.assertEqual((reconciled.sent, reconciled.failed), (1, 0))
        self.assertEqual(self.transitions()[-1].state, NotificationState.SENT)

    def test_config_rejects_bool_schema_version_and_action_enablement(self) -> None:
        base = {
            "schema_version": 1,
            "PROACTIVE_ENABLED": False,
            "PROACTIVE_NOTIFICATIONS_ENABLED": False,
            "PROACTIVE_ACTIONS_ENABLED": False,
            "delivery_target_sha256": "",
        }
        with self.assertRaises(Phase4ConfigError):
            Phase4Config.from_mapping({**base, "schema_version": True})
        enabled = Phase4Config.from_mapping(
            {**base, "PROACTIVE_ACTIONS_ENABLED": True}
        )
        self.assertTrue(enabled.actions_enabled)

    def test_disabled_cli_has_empty_stdout_and_stderr(self) -> None:
        home = Path(self.temp.name) / "empty-hermes-home"
        environment = {
            "HERMES_HOME": str(home),
            "HOME": str(Path(self.temp.name)),
            "PATH": os.environ.get("PATH", os.defpath),
            "PYTHONPATH": str(ROOT),
            "PROACTIVE_ENABLED": "false",
            "PROACTIVE_NOTIFICATIONS_ENABLED": "false",
            "PROACTIVE_ACTIONS_ENABLED": "false",
        }
        process = subprocess.run(
            [sys.executable, "-B", str(ROOT / "run_phase4.py")],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.stdout, "")
        self.assertEqual(process.stderr, "")
        self.assertFalse((home / "proactive-core-v1" / "state" / "events.sqlite3").exists())

    def test_enabled_reconcile_cli_has_empty_stdout(self) -> None:
        home = Path(self.temp.name) / "configured-hermes-home"
        runtime = home / "proactive-core-v1"
        runtime.mkdir(parents=True)
        target_hash = hashlib.sha256(TARGET.encode("utf-8")).hexdigest()
        (runtime / "config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "PROACTIVE_ENABLED": True,
                    "PROACTIVE_NOTIFICATIONS_ENABLED": True,
                    "PROACTIVE_ACTIONS_ENABLED": False,
                    "delivery_target_sha256": target_hash,
                }
            ),
            encoding="utf-8",
        )
        cron_dir = home / "cron"
        cron_dir.mkdir(parents=True)
        (cron_dir / "jobs.json").write_text(
            json.dumps(
                {
                    "jobs": [
                        {
                            "id": "job-phase4-cli",
                            "name": "proactive-review-v1",
                            "enabled": True,
                            "no_agent": True,
                            "script": "proactive_core_v1_runner.py",
                            "deliver": TARGET,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        environment = {
            "HERMES_HOME": str(home),
            "HOME": str(Path(self.temp.name)),
            "PATH": os.environ.get("PATH", os.defpath),
            "PYTHONPATH": str(ROOT),
        }
        process = subprocess.run(
            [sys.executable, "-B", str(ROOT / "run_phase4.py"), "--reconcile-only"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.stdout, "")
        self.assertEqual(process.stderr, "")
        self.assertTrue((runtime / "state" / "events.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
