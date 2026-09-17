"""Deterministic policy evaluation; no model, delivery, or action adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .model import (
    ActionDecision,
    ActionScope,
    Decision,
    Event,
    EventStatus,
    GoalCandidateDecision,
    HighLevelDecision,
    NotificationDecision,
    Risk,
    utc_iso,
)


_SCOPE_RISK = {
    ActionScope.NONE: Risk.LOW,
    ActionScope.READ: Risk.LOW,
    ActionScope.STATUS: Risk.LOW,
    ActionScope.RETRY_SAFE: Risk.LOW,
    ActionScope.INTERNAL_REVERSIBLE: Risk.MEDIUM,
    ActionScope.EXTERNAL_COMMUNICATION: Risk.HIGH,
    ActionScope.HIGH_RISK: Risk.HIGH,
}


@dataclass(frozen=True, slots=True)
class GoalCandidate:
    decision: GoalCandidateDecision
    reason_code: str
    candidate_available: bool


class PolicyEvaluator:
    """Produce six-state and split-dimension decisions from a sanitized Event."""

    def evaluate(self, event: Event, *, now: str | datetime | None = None) -> Decision:
        evaluated_at = utc_iso(now or event.observed_at)
        recovery_observation = (
            event.status == EventStatus.RESOLVED
            and event.recovery_of is not None
            and "recover" in event.event_type.casefold()
        )
        if event.status != EventStatus.OPEN and not recovery_observation:
            return Decision(
                event_id=event.event_id,
                high_level=HighLevelDecision.IGNORE,
                action_decision=ActionDecision.NONE,
                notification_decision=NotificationDecision.NONE,
                risk=Risk.LOW,
                reason_code="event_not_open",
                evaluated_at=evaluated_at,
                policy_version=event.policy_version,
            )
        if event.expires_at is not None and event.expires_at <= evaluated_at:
            return Decision(
                event_id=event.event_id,
                high_level=HighLevelDecision.IGNORE,
                action_decision=ActionDecision.NONE,
                notification_decision=NotificationDecision.NONE,
                risk=Risk.LOW,
                reason_code="event_expired",
                evaluated_at=evaluated_at,
                policy_version=event.policy_version,
            )

        risk = _SCOPE_RISK[event.action_scope] if event.action_requested else Risk.LOW
        if recovery_observation:
            action = ActionDecision.NONE
            reason = "recovery_event_no_action"
        elif not event.action_requested:
            action = ActionDecision.NONE
            reason = "no_action_requested"
        elif risk == Risk.LOW:
            action = ActionDecision.ACT
            reason = "low_risk_action_candidate"
        else:
            # Standing authorization exceptions are intentionally not implemented in v1.
            action = ActionDecision.ASK
            reason = "risk_requires_user_authorization"

        if event.urgent:
            notification = NotificationDecision.URGENT
        elif event.notification_requested or action == ActionDecision.ASK:
            # ASK asks the owner; it does not send the proposed content to a third party.
            notification = NotificationDecision.NOTIFY
        else:
            notification = NotificationDecision.NONE

        if notification == NotificationDecision.URGENT:
            high_level = HighLevelDecision.URGENT
            reason = "urgent_owner_notification"
        elif action == ActionDecision.ASK:
            high_level = HighLevelDecision.ASK
        elif action == ActionDecision.ACT:
            # ACT + NOTIFY is represented without losing either dimension.
            high_level = HighLevelDecision.ACT
        elif notification == NotificationDecision.NOTIFY:
            high_level = HighLevelDecision.NOTIFY
            reason = "owner_notification_candidate"
        else:
            high_level = HighLevelDecision.SILENT
            reason = "no_user_action_or_notification"

        return Decision(
            event_id=event.event_id,
            high_level=high_level,
            action_decision=action,
            notification_decision=notification,
            risk=risk,
            reason_code=reason,
            evaluated_at=evaluated_at,
            policy_version=event.policy_version,
        )


def evaluate_goal_candidate(
    *,
    evidence_changed: bool,
    candidate_available: bool,
    requires_user_input: bool = False,
    risk: Risk = Risk.LOW,
) -> GoalCandidate:
    """Goal progress is candidate-only: it never returns ACT or starts /goal."""
    if not evidence_changed or not candidate_available:
        return GoalCandidate(
            GoalCandidateDecision.SILENT,
            "no_new_goal_evidence",
            candidate_available=False,
        )
    if requires_user_input or Risk(risk) != Risk.LOW:
        return GoalCandidate(
            GoalCandidateDecision.ASK_CANDIDATE,
            "goal_candidate_needs_user",
            candidate_available=True,
        )
    return GoalCandidate(
        GoalCandidateDecision.NOTIFY_CANDIDATE,
        "goal_progress_candidate",
        candidate_available=True,
    )
