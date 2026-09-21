"""VPS-only Phase 5A Goal Progress Shadow runtime.

Reads only structured local goal/project sources, stores concise audit data, and
never prints, sends, mutates GOALS.md, calls /goal, or executes an action.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .goal_progress import GoalProgressEngine, GoalProgressLedger

MAX_AUDIT_BYTES = 512 * 1024
STATE_NAME = "goal-progress-shadow.json"


def _canonical_iso(value: str) -> str:
    """Return an offset-aware timestamp in canonical UTC form."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _cron_execution_context(hermes_home: Path) -> dict[str, Any] | None:
    """Read the newest built-in Cron attempt without changing the ledger."""
    jobs_path = hermes_home / "cron" / "jobs.json"
    execution_path = hermes_home / "cron" / "executions.db"
    try:
        raw = json.loads(jobs_path.read_text(encoding="utf-8"))
        jobs = raw.get("jobs", raw) if isinstance(raw, dict) else raw
        job = next(row for row in jobs if isinstance(row, dict) and row.get("name") == "proactive-review-v1")
        job_id = job["id"]
        conn = sqlite3.connect(f"file:{execution_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT id, source, status, claimed_at, started_at, finished_at, error "
                "FROM executions WHERE job_id=? ORDER BY claimed_at DESC, id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row or row[1] != "builtin":
            return None
        return {
            "execution_id": row[0],
            "source": row[1],
            "status": row[2],
            "scheduled_at": _canonical_iso(row[3]),
            "started_at": _canonical_iso(row[4]) if row[4] else None,
            "finished_at": _canonical_iso(row[5]) if row[5] else None,
            "error": row[6],
        }
    except (OSError, KeyError, StopIteration, TypeError, ValueError, sqlite3.Error, json.JSONDecodeError):
        return None


def _reconcile_execution_evidence(audit: dict[str, Any], hermes_home: Path) -> None:
    """Backfill terminal result evidence for previously recorded attempts."""
    execution_path = hermes_home / "cron" / "executions.db"
    try:
        conn = sqlite3.connect(f"file:{execution_path}?mode=ro", uri=True)
        try:
            for run in audit["runs"]:
                execution_id = run.get("execution_id")
                if not isinstance(execution_id, str):
                    run.setdefault("evidence_status", "legacy_unlinked")
                    continue
                row = conn.execute(
                    "SELECT status, started_at, finished_at, error FROM executions WHERE id=?",
                    (execution_id,),
                ).fetchone()
                if not row:
                    run["evidence_status"] = "execution_missing"
                    continue
                run["cron_result_status"] = row[0]
                run["cron_started_at"] = _canonical_iso(row[1]) if row[1] else None
                run["cron_finished_at"] = _canonical_iso(row[2]) if row[2] else None
                run["cron_error"] = row[3]
                run["evidence_status"] = "verified_success" if row[0] == "completed" else (
                    "verified_failure" if row[0] == "failed" else "pending"
                )
        finally:
            conn.close()
    except (OSError, ValueError, sqlite3.Error):
        return


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_goals(path: Path, observed_at: str) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    goals: list[dict[str, Any]] = []
    current_goal: str | None = None
    current_stage = ""
    for line in text.splitlines():
        if line.startswith("### ") and ":" in line:
            current_goal = line[4:].split(":", 1)[0].strip()
            current_stage = ""
        elif current_goal and "当前阶段" in line:
            current_stage = line.split("：", 1)[-1].strip().strip("（）")
    table_started = False
    for line in text.splitlines():
        if line.startswith("| 项目名称 |"):
            table_started = True
            continue
        if not table_started or not line.startswith("|") or line.startswith("| :---"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        project, goal, stage, blocker, next_action = cells
        if not project or not goal or goal == "归属目标":
            continue
        goal_id = goal.split("/", 1)[0].strip()
        project_id = hashlib.sha256(project.encode("utf-8")).hexdigest()[:16]
        evidence = f"{stage}；阻塞/风险：{blocker}"
        state = f"{stage}|{blocker}"
        goals.append({
            "goal_id": goal_id,
            "project_id": project_id,
            "active": True,
            "state": state,
            "evidence": evidence,
            "next_action": next_action,
            "why_now": "当前项目状态包含可验证的下一步",
            "confidence": 0.82,
            "user_decision_required": True,
            "observed_at": observed_at,
            "state_fingerprint": hashlib.sha256((goal_id + project + state).encode("utf-8")).hexdigest(),
            "blocked": blocker not in {"无", "暂无", ""},
            "windows_dependency": "Windows" in (evidence + next_action) or "E:\\" in (evidence + next_action),
        })
    return goals


def _read_kanban(path: Path, observed_at: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        query = "SELECT id, title, status, project_id FROM tasks WHERE status IN ('running','ready','todo','blocked') ORDER BY id LIMIT 256"
        for task_id, title, status, project_id in conn.execute(query):
            if not isinstance(task_id, str) or not isinstance(title, str) or not isinstance(status, str):
                continue
            rows.append({
                "goal_id": "KANBAN",
                "project_id": str(project_id or "kanban"),
                "active": True,
                "state": status,
                "evidence": f"Kanban task {task_id}: {title[:180]}",
                "next_action": "review task status",
                "why_now": "current Kanban state is actionable",
                "confidence": 0.7,
                "user_decision_required": True,
                "observed_at": observed_at,
                "state_fingerprint": hashlib.sha256(f"{task_id}|{status}|{title}".encode()).hexdigest(),
                "blocked": status == "blocked",
                "windows_dependency": False,
            })
    finally:
        conn.close()
    return rows


def _load_state(path: Path) -> tuple[GoalProgressLedger, dict[str, Any]]:
    ledger = GoalProgressLedger()
    audit: dict[str, Any] = {"version": 1, "runs": [], "candidates": []}
    if not path.is_file():
        return ledger, audit
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("malformed shadow state")
    for key, target in (("emitted", ledger.emitted), ("last_state", ledger.last_state)):
        value = raw.get(key, {})
        if isinstance(value, dict):
            target.update({str(k): str(v) for k, v in value.items()})
    for key, target in (("rejected", ledger.rejected), ("completed", ledger.completed)):
        value = raw.get(key, [])
        if isinstance(value, list):
            target.update(str(v) for v in value)
    if isinstance(raw.get("runs"), list):
        audit["runs"] = raw["runs"][-99:]
    if isinstance(raw.get("candidates"), list):
        audit["candidates"] = raw["candidates"][-99:]
    return ledger, audit


def _write_state(path: Path, ledger: GoalProgressLedger, audit: dict[str, Any]) -> None:
    payload = {
        "version": 1,
        "emitted": ledger.emitted,
        "last_state": ledger.last_state,
        "rejected": sorted(ledger.rejected),
        "completed": sorted(ledger.completed),
        "runs": audit["runs"][-99:],
        "candidates": audit["candidates"][-99:],
    }
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd, temp_name = tempfile.mkstemp(prefix=".goal-progress-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def run_goal_progress_shadow(
    hermes_home: Path,
    *,
    now: str | None = None,
    health_tick: bool = False,
    observation_source: str = "scheduled",
) -> dict[str, Any]:
    """Run one read-only observation and return auditable counters."""
    if observation_source not in {"scheduled", "diagnostic"}:
        raise ValueError("invalid observation source")
    observed_at = _canonical_iso(now) if now else _now()
    state_path = hermes_home / "proactive-core-v1" / STATE_NAME
    ledger, audit = _load_state(state_path)
    _reconcile_execution_evidence(audit, hermes_home)
    execution = _cron_execution_context(hermes_home) if observation_source == "scheduled" else None
    sources: list[dict[str, Any]] = []
    goals_path = hermes_home / "GOALS.md"
    if goals_path.is_file() and not goals_path.is_symlink():
        sources.extend(_read_goals(goals_path, observed_at))
    sources.extend(_read_kanban(hermes_home / "kanban.db", observed_at))
    semantic_calls: list[dict[str, Any]] = []
    engine = GoalProgressEngine(ledger=ledger, semantic_call=None)
    result = engine.run(sources, now=observed_at, health_tick=health_tick)
    candidate = result.candidate.as_dict() if result.candidate else None
    run_record = {
        "observation_source": observation_source,
        "observed_at": observed_at,
        "source_count": len(sources),
        "prefiltered_count": result.prefiltered_count,
        "reason_code": result.reason_code,
        "semantic_calls": result.semantic_calls,
        "health_tick": health_tick,
        "malformed": result.malformed,
        "candidate_fingerprint": candidate.get("fingerprint") if candidate else None,
        "goal_notification_sent": 0,
        "actions_executed": 0,
        "evidence_status": "not_scheduled",
    }
    if execution:
        run_record.update({
            "execution_id": execution["execution_id"],
            "scheduled_at": execution["scheduled_at"],
            "cron_result_status": execution["status"],
            "cron_started_at": execution["started_at"],
            "cron_finished_at": execution["finished_at"],
            "cron_error": execution["error"],
            "evidence_status": "pending" if execution["status"] in {"claimed", "running"} else (
                "verified_success" if execution["status"] == "completed" else "verified_failure"
            ),
        })
    audit["runs"].append(run_record)
    if candidate:
        audit["candidates"].append(candidate)
    _write_state(state_path, ledger, audit)
    return audit["runs"][-1]
