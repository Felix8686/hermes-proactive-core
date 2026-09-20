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


def run_goal_progress_shadow(hermes_home: Path, *, now: str | None = None, health_tick: bool = False) -> dict[str, Any]:
    """Run one real read-only observation and return audit counters only."""
    observed_at = now or _now()
    state_path = hermes_home / "proactive-core-v1" / STATE_NAME
    ledger, audit = _load_state(state_path)
    sources: list[dict[str, Any]] = []
    goals_path = hermes_home / "GOALS.md"
    if goals_path.is_file() and not goals_path.is_symlink():
        sources.extend(_read_goals(goals_path, observed_at))
    sources.extend(_read_kanban(hermes_home / "kanban.db", observed_at))
    semantic_calls: list[dict[str, Any]] = []
    engine = GoalProgressEngine(ledger=ledger, semantic_call=None)
    result = engine.run(sources, now=observed_at, health_tick=health_tick)
    candidate = result.candidate.as_dict() if result.candidate else None
    audit["runs"].append({
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
    })
    if candidate:
        audit["candidates"].append(candidate)
    _write_state(state_path, ledger, audit)
    return audit["runs"][-1]
