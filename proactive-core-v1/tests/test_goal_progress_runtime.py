from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from proactive_core.goal_progress_runtime import run_goal_progress_shadow


class GoalProgressRuntimeEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / "proactive-core-v1").mkdir()
        (self.home / "cron").mkdir()
        (self.home / "GOALS.md").write_text("# empty\n", encoding="utf-8")
        (self.home / "cron" / "jobs.json").write_text(
            json.dumps({"jobs": [{"id": "job-1", "name": "proactive-review-v1"}]}),
            encoding="utf-8",
        )
        conn = sqlite3.connect(self.home / "cron" / "executions.db")
        conn.execute(
            "CREATE TABLE executions (id TEXT PRIMARY KEY, job_id TEXT, source TEXT, status TEXT, "
            "claimed_at TEXT, started_at TEXT, finished_at TEXT, error TEXT)"
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _insert(self, execution_id: str, status: str, claimed: str) -> None:
        conn = sqlite3.connect(self.home / "cron" / "executions.db")
        conn.execute(
            "INSERT INTO executions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (execution_id, "job-1", "builtin", status, claimed, claimed, None, None),
        )
        conn.commit()
        conn.close()

    def test_scheduled_run_uses_utc_identity_and_later_terminal_reconciliation(self) -> None:
        self._insert("exec-1", "running", "2026-09-21T08:35:35.123456+08:00")
        first = run_goal_progress_shadow(self.home, now="2026-09-21T00:35:35+00:00")
        self.assertEqual(first["observation_source"], "scheduled")
        self.assertEqual(first["execution_id"], "exec-1")
        self.assertEqual(first["scheduled_at"], "2026-09-21T00:35:35Z")
        self.assertEqual(first["evidence_status"], "pending")

        conn = sqlite3.connect(self.home / "cron" / "executions.db")
        conn.execute(
            "UPDATE executions SET status='completed', finished_at=? WHERE id=?",
            ("2026-09-21T08:35:35.8+08:00", "exec-1"),
        )
        conn.commit()
        conn.close()
        self._insert("exec-2", "running", "2026-09-21T10:35:35+08:00")
        second = run_goal_progress_shadow(self.home, now="2026-09-21T02:35:35Z")
        self.assertEqual(second["execution_id"], "exec-2")
        audit = json.loads((self.home / "proactive-core-v1" / "goal-progress-shadow.json").read_text())
        self.assertEqual(audit["runs"][0]["evidence_status"], "verified_success")
        self.assertEqual(audit["runs"][0]["cron_result_status"], "completed")
        self.assertEqual(audit["runs"][0]["cron_finished_at"], "2026-09-21T00:35:35Z")

    def test_diagnostic_run_is_explicitly_not_scheduled(self) -> None:
        result = run_goal_progress_shadow(
            self.home, now="2026-09-21T00:35:35+00:00", observation_source="diagnostic"
        )
        self.assertEqual(result["observation_source"], "diagnostic")
        self.assertEqual(result["evidence_status"], "not_scheduled")
        self.assertNotIn("execution_id", result)


if __name__ == "__main__":
    unittest.main()
