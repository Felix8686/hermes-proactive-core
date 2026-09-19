"""Versioned Proactive Core contracts; integration boundaries are explicit modules."""

from .model import (
    ActionDecision,
    ActionScope,
    Decision,
    Event,
    EventStatus,
    GoalCandidateDecision,
    HighLevelDecision,
    NotificationDecision,
    NotificationState,
    Risk,
    Severity,
)
from .goal_progress import (
    GoalProgressEngine,
    GoalProgressInput,
    GoalProgressInputError,
    GoalProgressLedger,
    GoalProgressResult,
    NextActionCandidate,
    deterministic_prefilter,
    normalize_goal_inputs,
)

__all__ = [
    "ActionDecision",
    "ActionScope",
    "Decision",
    "Event",
    "EventStatus",
    "GoalCandidateDecision",
    "HighLevelDecision",
    "NotificationDecision",
    "NotificationState",
    "Risk",
    "Severity",
    "GoalProgressEngine",
    "GoalProgressInput",
    "GoalProgressInputError",
    "GoalProgressLedger",
    "GoalProgressResult",
    "NextActionCandidate",
    "deterministic_prefilter",
    "normalize_goal_inputs",
]
