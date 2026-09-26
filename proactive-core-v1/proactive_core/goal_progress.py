"""Offline Goal Progress normalization, prefilter, and candidate scaffolding.

This module is deliberately side-effect free: it does not read Hermes state, send
Telegram, mutate GOALS.md, invoke /goal, or execute actions. Integrations may feed
it already-authorized structured snapshots in a later, separately authorized stage.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .privacy import sanitize_text, sanitize_value
from .model import utc_iso


class GoalProgressInputError(ValueError):
    """Raised when a structured goal snapshot cannot be safely normalized."""


@dataclass(frozen=True, slots=True)
class GoalProgressInput:
    goal_id: str
    project_id: str | None
    active: bool
    state: str
    state_fingerprint: str
    evidence: str
    next_action: str | None
    why_now: str | None
    confidence: float
    user_decision_required: bool
    observed_at: str
    source: str
    blocked: bool = False
    windows_dependency: bool = False


@dataclass(frozen=True, slots=True)
class NextActionCandidate:
    goal_id: str
    project_id: str | None
    evidence: str
    proposed_next_action: str
    why_now: str
    confidence: float
    user_decision_required: bool
    fingerprint: str
    state_fingerprint: str
    decision: str = "NEXT_ACTION_CANDIDATE"

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "project_id": self.project_id,
            "evidence": self.evidence,
            "proposed_next_action": self.proposed_next_action,
            "why_now": self.why_now,
            "confidence": self.confidence,
            "user_decision_required": self.user_decision_required,
            "fingerprint": self.fingerprint,
            "state_fingerprint": self.state_fingerprint,
            "decision": self.decision,
        }


@dataclass(frozen=True, slots=True)
class GoalProgressResult:
    candidate: NextActionCandidate | None = None
    reason_code: str = "silent"
    prefiltered_count: int = 0
    semantic_calls: int = 0
    malformed: bool = False


@dataclass(slots=True)
class GoalProgressLedger:
    """Small in-memory audit seam for offline tests and future state adapters."""

    emitted: dict[str, str] = field(default_factory=dict)
    rejected: set[str] = field(default_factory=set)
    completed: set[str] = field(default_factory=set)
    last_state: dict[str, str] = field(default_factory=dict)

    def reject(self, fingerprint: str) -> None:
        self.rejected.add(fingerprint)

    def complete(self, fingerprint: str) -> None:
        self.completed.add(fingerprint)


SemanticCall = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]


def _text(value: Any, *, name: str, limit: int = 256) -> str:
    if not isinstance(value, str):
        raise GoalProgressInputError(f"invalid {name}")
    cleaned = sanitize_text(value, limit=limit)
    if not cleaned or cleaned == "[REDACTED]":
        raise GoalProgressInputError(f"invalid {name}")
    return cleaned


def _optional_text(value: Any, *, name: str, limit: int = 256) -> str | None:
    if value is None:
        return None
    return _text(value, name=name, limit=limit)


def _timestamp(value: Any) -> str:
    if value is None:
        raise GoalProgressInputError("missing observed_at")
    try:
        return utc_iso(value)
    except (TypeError, ValueError) as error:
        raise GoalProgressInputError("invalid observed_at") from error


def _row_list(raw: Any) -> list[Mapping[str, Any]]:
    if isinstance(raw, Mapping):
        for key in ("goals", "projects", "items"):
            if key in raw:
                raw = raw[key]
                break
        else:
            raw = [raw]
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise GoalProgressInputError("goal source must be a list or object")
    if len(raw) > 256 or any(not isinstance(row, Mapping) for row in raw):
        raise GoalProgressInputError("malformed goal source")
    return list(raw)


def normalize_goal_inputs(raw: Any) -> list[GoalProgressInput]:
    """Normalize only structured, bounded, read-only goal/project snapshots."""
    rows = _row_list(raw)
    normalized: list[GoalProgressInput] = []
    for row in rows:
        # Keep descriptive fields readable while applying the same privacy
        # redaction rules; private-body fields are never accepted as inputs.
        raw_row = dict(row)
        for key in raw_row:
            normalized_key = str(key).casefold().replace("-", "_")
            if any(token in normalized_key for token in ("message", "transcript", "prompt", "token", "secret", "password", "credential", "cookie")):
                raise GoalProgressInputError("private goal field is not accepted")
        cleaned = sanitize_value({key: value for key, value in raw_row.items() if key in {
            "goal_id", "id", "project_id", "project", "active", "state", "status",
            "evidence", "state_evidence", "observed_at", "state_fingerprint", "next_action",
            "proposed_next_action", "why_now", "confidence", "user_decision_required",
            "blocked", "windows_dependency",
        }})
        if not isinstance(cleaned, dict):
            raise GoalProgressInputError("malformed goal row")

        def raw_text(*names: str, limit: int = 256) -> str | None:
            for name in names:
                if name in raw_row:
                    value = raw_row[name]
                    if value is None:
                        return None
                    if not isinstance(value, str):
                        raise GoalProgressInputError(f"invalid {name}")
                    result = sanitize_text(value, limit=limit)
                    if not result or result == "[REDACTED]":
                        raise GoalProgressInputError(f"invalid {name}")
                    return result
            return None

        goal_id = _text(raw_text("goal_id", "id", limit=120), name="goal_id", limit=120)
        project_id = _optional_text(
            raw_text("project_id", "project", limit=120), name="project_id", limit=120
        )
        active = raw_row.get("active")
        if type(active) is not bool:
            raise GoalProgressInputError("invalid active flag")
        state = _text(raw_text("state", "status", limit=120), name="state", limit=120)
        evidence = _text(raw_text("evidence", "state_evidence"), name="evidence")
        observed_at = _timestamp(raw_row.get("observed_at"))
        state_fingerprint = raw_row.get("state_fingerprint")
        if state_fingerprint is None:
            state_fingerprint = hashlib.sha256(
                json.dumps(
                    {"goal_id": goal_id, "project_id": project_id, "state": state, "evidence": evidence},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        state_fingerprint = _text(state_fingerprint, name="state_fingerprint", limit=128)
        next_action = raw_text("next_action", "proposed_next_action")
        why_now = raw_text("why_now")
        confidence = raw_row.get("confidence", 0.75)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise GoalProgressInputError("invalid confidence")
        user_decision_required = cleaned.get("user_decision_required", False)
        blocked = cleaned.get("blocked", False)
        windows_dependency = cleaned.get("windows_dependency", False)
        if type(user_decision_required) is not bool or type(blocked) is not bool or type(windows_dependency) is not bool:
            raise GoalProgressInputError("invalid goal flags")
        normalized.append(
            GoalProgressInput(
                goal_id=goal_id,
                project_id=project_id,
                active=active,
                state=state,
                state_fingerprint=state_fingerprint,
                evidence=evidence,
                next_action=next_action,
                why_now=why_now,
                confidence=float(confidence),
                user_decision_required=user_decision_required,
                observed_at=observed_at,
                source="structured_goal_state",
                blocked=blocked,
                windows_dependency=windows_dependency,
            )
        )
    return normalized


def _candidate_fingerprint(item: GoalProgressInput) -> str:
    canonical = json.dumps(
        [item.goal_id, item.project_id, item.state_fingerprint, item.next_action, item.why_now],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class GoalProgressEngine:
    """Deterministic prefilter with one-candidate and semantic-call guardrails."""

    def __init__(
        self,
        *,
        ledger: GoalProgressLedger | None = None,
        semantic_call: SemanticCall | None = None,
        max_age_days: int = 7,
        cooldown_seconds: int = 24 * 60 * 60,
    ) -> None:
        if max_age_days < 1 or cooldown_seconds < 1:
            raise ValueError("Goal Progress windows must be positive")
        self.ledger = ledger or GoalProgressLedger()
        self.semantic_call = semantic_call
        self.max_age_days = max_age_days
        self.cooldown_seconds = cooldown_seconds
        self._semantic_days: set[str] = set()

    def run(
        self,
        raw: Any,
        *,
        now: str | datetime,
        health_tick: bool = False,
    ) -> GoalProgressResult:
        try:
            items = normalize_goal_inputs(raw)
            current = datetime.fromisoformat(utc_iso(now).replace("Z", "+00:00"))
        except (GoalProgressInputError, TypeError, ValueError):
            return GoalProgressResult(reason_code="malformed_source", malformed=True)
        if health_tick:
            return GoalProgressResult(reason_code="health_tick_no_llm")

        eligible: list[GoalProgressInput] = []
        for item in items:
            if not self._prefilter(item, current):
                continue
            previous = self.ledger.last_state.get(item.goal_id)
            if previous == item.state_fingerprint:
                continue
            self.ledger.last_state[item.goal_id] = item.state_fingerprint
            fingerprint = _candidate_fingerprint(item)
            if fingerprint in self.ledger.rejected or fingerprint in self.ledger.completed:
                continue
            emitted_at = self.ledger.emitted.get(fingerprint)
            if emitted_at:
                emitted_time = datetime.fromisoformat(emitted_at.replace("Z", "+00:00"))
                if (current - emitted_time).total_seconds() < self.cooldown_seconds:
                    continue
            eligible.append(item)

        if not eligible:
            return GoalProgressResult(reason_code="no_eligible_goal")
        eligible.sort(key=lambda item: (-item.confidence, item.goal_id, item.project_id or ""))
        item = eligible[0]
        fingerprint = _candidate_fingerprint(item)
        day = current.date().isoformat()
        calls = 0
        if self.semantic_call is not None and day not in self._semantic_days:
            # The callback is an injected seam only; the engine never selects a provider.
            self._semantic_days.add(day)
            calls = 1
            try:
                semantic = self.semantic_call(
                    {
                        "goal_id": item.goal_id,
                        "project_id": item.project_id,
                        "evidence": item.evidence,
                        "proposed_next_action": item.next_action,
                    }
                )
                if semantic is not None and not isinstance(semantic, Mapping):
                    return GoalProgressResult(reason_code="semantic_result_rejected", semantic_calls=calls)
            except Exception:
                return GoalProgressResult(reason_code="semantic_unavailable", semantic_calls=calls)

        candidate = NextActionCandidate(
            goal_id=item.goal_id,
            project_id=item.project_id,
            evidence=item.evidence,
            proposed_next_action=item.next_action or "",
            why_now=item.why_now or "当前状态已出现可验证变化",
            confidence=item.confidence,
            user_decision_required=item.user_decision_required,
            fingerprint=fingerprint,
            state_fingerprint=item.state_fingerprint,
            decision="NEXT_ACTION_CANDIDATE",
        )
        self.ledger.emitted[fingerprint] = utc_iso(now)
        return GoalProgressResult(
            candidate=candidate,
            reason_code="candidate_created",
            prefiltered_count=len(eligible),
            semantic_calls=calls,
        )

    def _prefilter(self, item: GoalProgressInput, current: datetime) -> bool:
        if not item.active or item.blocked or item.windows_dependency:
            return False
        if not item.next_action or len(item.next_action.split()) < 2:
            return False
        observed = datetime.fromisoformat(item.observed_at.replace("Z", "+00:00"))
        age = (current - observed).total_seconds()
        return 0 <= age <= self.max_age_days * 86400


def deterministic_prefilter(raw: Any, *, now: str | datetime) -> list[GoalProgressInput]:
    """Expose the fail-closed normalized eligible-input seam for offline tests."""
    try:
        current = datetime.fromisoformat(utc_iso(now).replace("Z", "+00:00"))
        return [
            item
            for item in normalize_goal_inputs(raw)
            if GoalProgressEngine()._prefilter(item, current)
        ]
    except (GoalProgressInputError, TypeError, ValueError):
        return []
