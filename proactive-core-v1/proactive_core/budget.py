"""Daily notification budgets with a separate, bounded urgent lane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Iterable

from .model import NotificationAttempt, NotificationDecision, NotificationState, utc_iso


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    normal_per_day: int = 3
    normal_fingerprint_cooldown_seconds: int = 6 * 60 * 60
    urgent_per_hour: int = 2
    urgent_per_day: int = 5
    urgent_fingerprint_cooldown_seconds: int = 6 * 60 * 60

    def __post_init__(self) -> None:
        if min(
            self.normal_per_day,
            self.normal_fingerprint_cooldown_seconds,
            self.urgent_per_hour,
            self.urgent_per_day,
            self.urgent_fingerprint_cooldown_seconds,
        ) < 1:
            raise ValueError("notification budget values must be positive")


@dataclass(frozen=True, slots=True)
class BudgetResult:
    allowed: bool
    reason_code: str


_COUNTED_STATES = {
    NotificationState.PENDING,
    NotificationState.SENT,
}


def _logical_attempts(history: Iterable[NotificationAttempt]) -> list[NotificationAttempt]:
    latest: dict[tuple[str, int], NotificationAttempt] = {}
    for item in history:
        key = (item.candidate_id, item.attempt_number)
        previous = latest.get(key)
        if previous is None or item.occurred_at >= previous.occurred_at:
            latest[key] = item
    return [item for item in latest.values() if item.state in _COUNTED_STATES]


class NotificationBudget:
    def __init__(self, config: BudgetConfig | None = None) -> None:
        self.config = config or BudgetConfig()

    def evaluate(
        self,
        *,
        decision: NotificationDecision,
        fingerprint: str,
        escalation_level: int,
        now: str | datetime,
        history: Iterable[NotificationAttempt],
    ) -> BudgetResult:
        decision = NotificationDecision(decision)
        if decision == NotificationDecision.NONE:
            return BudgetResult(False, "no_notification_candidate")
        current = datetime.fromisoformat(utc_iso(now).replace("Z", "+00:00"))
        attempts = _logical_attempts(history)
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_start = current - timedelta(hours=1)
        todays = [
            item
            for item in attempts
            if datetime.fromisoformat(item.occurred_at.replace("Z", "+00:00")) >= day_start
        ]

        if decision == NotificationDecision.NOTIFY:
            matching = [
                item
                for item in attempts
                if item.fingerprint == fingerprint
                and item.notification_decision == NotificationDecision.NOTIFY
            ]
            matching.sort(key=lambda item: item.occurred_at, reverse=True)
            if matching:
                latest_time = datetime.fromisoformat(
                    matching[0].occurred_at.replace("Z", "+00:00")
                )
                if (current - latest_time).total_seconds() < self.config.normal_fingerprint_cooldown_seconds:
                    return BudgetResult(False, "normal_fingerprint_cooldown")
            ordinary = [
                item for item in todays
                if item.notification_decision == NotificationDecision.NOTIFY
            ]
            if len(ordinary) >= self.config.normal_per_day:
                return BudgetResult(False, "normal_daily_budget_exhausted")
            return BudgetResult(True, "normal_budget_available")

        urgent_today = [
            item for item in todays
            if item.notification_decision == NotificationDecision.URGENT
        ]
        urgent_hour = [
            item
            for item in urgent_today
            if datetime.fromisoformat(item.occurred_at.replace("Z", "+00:00")) >= hour_start
        ]
        matching = [
            item
            for item in attempts
            if item.fingerprint == fingerprint
            and item.notification_decision == NotificationDecision.URGENT
        ]
        matching.sort(key=lambda item: item.occurred_at, reverse=True)
        if matching:
            latest = matching[0]
            latest_time = datetime.fromisoformat(latest.occurred_at.replace("Z", "+00:00"))
            escalated = escalation_level > latest.escalation_level
            inside_cooldown = (
                current - latest_time
            ).total_seconds() < self.config.urgent_fingerprint_cooldown_seconds
            if inside_cooldown and not escalated:
                return BudgetResult(False, "urgent_fingerprint_cooldown")
        if len(urgent_hour) >= self.config.urgent_per_hour:
            return BudgetResult(False, "urgent_hourly_rate_limited")
        if len(urgent_today) >= self.config.urgent_per_day:
            return BudgetResult(False, "urgent_daily_rate_limited")
        return BudgetResult(True, "urgent_budget_available")
