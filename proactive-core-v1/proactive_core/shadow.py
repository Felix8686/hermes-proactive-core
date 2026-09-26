"""Local fixture-only Shadow detector; deliberately has no send/execute path."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .budget import NotificationBudget
from .circuit import CircuitBreaker
from .model import Event, NotificationDecision, NotificationState, utc_iso
from .policy import PolicyEvaluator
from .renderer import NotificationRenderer
from .store import SQLiteEventStore, StoreBusyError, StoreError
from .switches import KillSwitches


@dataclass(frozen=True, slots=True)
class ShadowRunResult:
    stdout_text: str = ""
    processed_events: int = 0
    notification_candidates_created: int = 0
    action_candidates_created: int = 0
    notifications_sent: int = 0
    actions_executed: int = 0
    llm_calls: int = 0
    error_code: str | None = None


class ShadowDetector:
    """The only inputs are caller-supplied local Event fixtures."""

    def __init__(
        self,
        store: SQLiteEventStore | None,
        switches: KillSwitches,
        *,
        policy: PolicyEvaluator | None = None,
        renderer: NotificationRenderer | None = None,
        budget: NotificationBudget | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.store = store
        self.switches = switches
        self.policy = policy or PolicyEvaluator()
        self.renderer = renderer or NotificationRenderer()
        self.budget = budget or NotificationBudget()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

    def run(
        self,
        events: Iterable[Event],
        *,
        now: str | datetime | None = None,
    ) -> ShadowRunResult:
        if not self.switches.proactive_enabled:
            return ShadowRunResult()
        if self.store is None:
            return ShadowRunResult(error_code="shadow_store_missing")
        timestamp = utc_iso(now or datetime.now(timezone.utc))
        if not self.circuit_breaker.allow(now=timestamp):
            return ShadowRunResult(error_code="circuit_open")

        processed = 0
        created_candidates = 0
        rendered: list[str] = []
        try:
            self.store.expire_due(timestamp)
            for incoming in events:
                if not isinstance(incoming, Event):
                    raise TypeError("shadow input must be an Event")
                recorded = self.store.record_event(incoming)
                self.store.expire_due(timestamp)
                event = self.store.get_event(recorded.event.event_id) or recorded.event
                decision = self.policy.evaluate(event, now=timestamp)
                self.store.append_decision(decision)
                processed += 1
                if decision.notification_decision == NotificationDecision.NONE:
                    continue

                day = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                protection_window = max(
                    60 * 60,
                    self.budget.config.normal_fingerprint_cooldown_seconds,
                    self.budget.config.urgent_fingerprint_cooldown_seconds,
                )
                history_start = min(
                    day.replace(hour=0, minute=0, second=0, microsecond=0),
                    day - timedelta(seconds=protection_window),
                )
                budget_result = self.budget.evaluate(
                    decision=decision.notification_decision,
                    fingerprint=event.fingerprint,
                    escalation_level=event.escalation_level,
                    now=timestamp,
                    history=self.store.notification_budget_history(since=utc_iso(history_start)),
                )
                candidate_id, created = self.store.add_notification_candidate(
                    event,
                    decision,
                    created_at=timestamp,
                )
                if not created:
                    continue
                created_candidates += 1
                if not budget_result.allowed:
                    self.store.append_notification_transition(
                        candidate_id,
                        NotificationState.SUPPRESSED,
                        occurred_at=timestamp,
                        detail_code=budget_result.reason_code,
                    )
                    continue
                text = self.renderer.render(event, decision, shadow=True)
                if text:
                    rendered.append(text)
            self.circuit_breaker.record_success()
        except StoreBusyError:
            self.circuit_breaker.record_failure("event_store_busy", now=timestamp)
            return ShadowRunResult(
                processed_events=processed,
                notification_candidates_created=created_candidates,
                error_code="event_store_busy",
            )
        except (StoreError, ValueError, TypeError):
            self.circuit_breaker.record_failure("shadow_input_or_store_error", now=timestamp)
            return ShadowRunResult(
                processed_events=processed,
                notification_candidates_created=created_candidates,
                error_code="shadow_input_or_store_error",
            )
        except Exception:
            self.circuit_breaker.record_failure("shadow_processing_failed", now=timestamp)
            return ShadowRunResult(
                processed_events=processed,
                notification_candidates_created=created_candidates,
                error_code="shadow_processing_failed",
            )
        return ShadowRunResult(
            stdout_text="\n".join(rendered),
            processed_events=processed,
            notification_candidates_created=created_candidates,
            notifications_sent=0,
            actions_executed=0,
            llm_calls=0,
        )
