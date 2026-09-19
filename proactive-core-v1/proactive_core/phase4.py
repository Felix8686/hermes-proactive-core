"""Deterministic, notification-only Phase 4 integration contracts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .budget import NotificationBudget
from .model import (
    ActionScope,
    Event,
    NotificationDecision,
    NotificationState,
    Severity,
    utc_iso,
)
from .policy import PolicyEvaluator
from .renderer import NotificationRenderer
from .store import SQLiteEventStore, StoreBusyError, StoreError
from .switches import KillSwitches


PHASE4_ALLOWLIST = frozenset(
    {
        "cron.failure",
        "cron.recovered",
        "openviking.unhealthy",
        "openviking.recovered",
        "proactive.circuit_breaker_open",
    }
)
PHASE4_TEST_EVENT_TYPE = "phase4.synthetic.delivery_test"
PHASE4_TEST_TEXT = "【Proactive Core 测试】通知链路测试成功；这不是异常告警。"
PHASE4_TEST_EVENT_ID = "evt_" + hashlib.sha256(b"proactive-core-phase4a-test-v1").hexdigest()[:32]
PHASE4_JOB_NAME = "proactive-review-v1"
PHASE4_SCRIPT_NAME = "proactive_core_v1_runner.py"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONFIG_KEYS = {
    "schema_version",
    "PROACTIVE_ENABLED",
    "PROACTIVE_NOTIFICATIONS_ENABLED",
    "PROACTIVE_ACTIONS_ENABLED",
    "delivery_target_sha256",
}


class Phase4ConfigError(ValueError):
    """Raised for an invalid or unsafe Phase 4 runtime configuration."""


@dataclass(frozen=True, slots=True)
class Phase4Config:
    proactive_enabled: bool = False
    notifications_enabled: bool = False
    actions_enabled: bool = False
    delivery_target_sha256: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Phase4Config":
        if (
            set(raw) != _CONFIG_KEYS
            or type(raw.get("schema_version")) is not int
            or raw["schema_version"] != 1
        ):
            raise Phase4ConfigError("unsupported Phase 4 config shape")
        for key in (
            "PROACTIVE_ENABLED",
            "PROACTIVE_NOTIFICATIONS_ENABLED",
            "PROACTIVE_ACTIONS_ENABLED",
        ):
            if type(raw[key]) is not bool:
                raise Phase4ConfigError("invalid Phase 4 switch")
        target_hash = raw["delivery_target_sha256"]
        if not isinstance(target_hash, str) or (target_hash and not _SHA256_RE.fullmatch(target_hash)):
            raise Phase4ConfigError("invalid delivery target fingerprint")
        return cls(
            proactive_enabled=raw["PROACTIVE_ENABLED"],
            notifications_enabled=raw["PROACTIVE_NOTIFICATIONS_ENABLED"],
            actions_enabled=raw["PROACTIVE_ACTIONS_ENABLED"],
            delivery_target_sha256=target_hash,
        )

    @classmethod
    def load(cls, path: str | Path) -> "Phase4Config":
        config_path = Path(path)
        try:
            if config_path.is_symlink():
                raise Phase4ConfigError("Phase 4 config must be a regular file")
            if not config_path.exists():
                return cls()
            if not config_path.is_file():
                raise Phase4ConfigError("Phase 4 config must be a regular file")
            mode = config_path.stat().st_mode
            if os.name == "posix" and mode & 0o077:
                raise Phase4ConfigError("Phase 4 config permissions are not private")
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError) as error:
            raise Phase4ConfigError("Phase 4 config is unavailable") from error
        if not isinstance(raw, dict):
            raise Phase4ConfigError("Phase 4 config must be an object")
        return cls.from_mapping(raw)

    def switches(self, environment: Mapping[str, str] | None = None) -> KillSwitches:
        values = {
            "PROACTIVE_ENABLED": str(self.proactive_enabled).lower(),
            "PROACTIVE_NOTIFICATIONS_ENABLED": str(self.notifications_enabled).lower(),
            "PROACTIVE_ACTIONS_ENABLED": str(self.actions_enabled).lower(),
        }
        if environment is not None:
            for key in _CONFIG_KEYS - {"schema_version", "delivery_target_sha256"}:
                if key in environment:
                    values[key] = environment[key]
        return KillSwitches.from_env(values)


@dataclass(frozen=True, slots=True)
class CronDeliveryObservation:
    route_verified: bool
    last_run_at: str | None
    last_status: str | None
    delivery_error_known: bool
    delivery_failed: bool
    last_output_sha256: str | None = None
    last_output_mtime: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    sent: int = 0
    failed: int = 0
    waiting: int = 0


@dataclass(frozen=True, slots=True)
class Phase4RunResult:
    stdout_text: str = ""
    processed_events: int = 0
    notification_candidates_created: int = 0
    pending_delivery_count: int = 0
    llm_calls: int = 0
    actions_executed: int = 0
    error_code: str | None = None


def _job_list(raw: Any) -> list[dict[str, Any]]:
    jobs = raw.get("jobs", raw) if isinstance(raw, dict) else raw
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise ValueError("malformed Cron job configuration")
    return jobs


def _latest_cron_output(
    hermes_home: str | Path,
    *,
    job_id: str,
    last_run_at: str | None,
) -> tuple[str | None, str | None]:
    if (
        not isinstance(job_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", job_id)
        or job_id in {".", ".."}
        or not last_run_at
    ):
        return None, None
    try:
        completed = datetime.fromisoformat(utc_iso(last_run_at).replace("Z", "+00:00"))
        output_dir = Path(hermes_home) / "cron" / "output" / job_id
        if output_dir.is_symlink() or not output_dir.is_dir():
            return None, None
        files = [path for path in output_dir.glob("*.md") if path.is_file() and not path.is_symlink()]
        if not files:
            return None, None
        latest = max(files, key=lambda path: path.stat().st_mtime_ns)
        stat_result = latest.stat()
        if stat_result.st_size > 256 * 1024:
            return None, None
        output_time = datetime.fromtimestamp(stat_result.st_mtime, timezone.utc)
        if abs((output_time - completed).total_seconds()) > 300:
            return None, None
        output_text = latest.read_bytes().decode("utf-8")
        if output_text.startswith("# Cron Job: "):
            header, separator, body = output_text.partition("\n\n---\n\n")
            header_lines = header.splitlines()
            if (
                not separator
                or len(header_lines) != 5
                or header_lines[2] != f"**Job ID:** {job_id}"
                or not header_lines[3].startswith("**Run Time:** ")
                or header_lines[4] != "**Mode:** no_agent (script)"
                or not body.endswith("\n")
            ):
                return None, None
            header_run_at = header_lines[3].removeprefix("**Run Time:** ")
            header_time = datetime.fromisoformat(
                header_run_at.replace("Z", "+00:00")
            )
            if header_time.tzinfo is None:
                # Hermes formats this header in host-local time without an
                # offset; interpret it in the same local timezone.
                header_time = header_time.astimezone()
            if (
                abs(
                    (header_time.astimezone(timezone.utc) - completed).total_seconds()
                )
                > 300
            ):
                return None, None
            # Hermes persists a Markdown envelope for no-agent script runs,
            # but delivers the enclosed stdout verbatim. Hash that exact body,
            # excluding only the single newline added by the envelope.
            output_text = body[:-1]
        if not output_text:
            return None, None
        output_hash = hashlib.sha256(output_text.encode("utf-8")).hexdigest()
        output_time_text = output_time.isoformat(timespec="microseconds").replace("+00:00", "Z")
        return output_hash, output_time_text
    except (OSError, TypeError, ValueError):
        return None, None


def read_cron_delivery_observation(
    hermes_home: str | Path,
    *,
    trusted_target_sha256: str,
) -> CronDeliveryObservation:
    """Read the Proactive job route and its last Hermes delivery result.

    The target is sourced only from Hermes-controlled jobs.json. Event payloads
    never carry or select a Telegram destination.
    """
    if not _SHA256_RE.fullmatch(trusted_target_sha256 or ""):
        return CronDeliveryObservation(False, None, None, False, False)
    path = Path(hermes_home) / "cron" / "jobs.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        matches = [job for job in _job_list(raw) if job.get("name") == PHASE4_JOB_NAME]
    except (OSError, json.JSONDecodeError, ValueError):
        return CronDeliveryObservation(False, None, None, False, False)
    if len(matches) != 1:
        return CronDeliveryObservation(False, None, None, False, False)
    job = matches[0]
    target = job.get("deliver")
    target_hash = hashlib.sha256(target.encode("utf-8")).hexdigest() if isinstance(target, str) else ""
    last_run_at = job.get("last_run_at") if isinstance(job.get("last_run_at"), str) else None
    job_id = job.get("id") if isinstance(job.get("id"), str) else ""
    output_hash, output_mtime = _latest_cron_output(
        hermes_home,
        job_id=job_id,
        last_run_at=last_run_at,
    )
    route_verified = (
        job.get("enabled") is True
        and job.get("no_agent") is True
        and job.get("script") == PHASE4_SCRIPT_NAME
        and isinstance(target, str)
        and target.startswith("telegram:")
        and target_hash == trusted_target_sha256
    )
    delivery_value = job.get("last_delivery_error")
    return CronDeliveryObservation(
        route_verified=route_verified,
        last_run_at=last_run_at,
        last_status=job.get("last_status") if isinstance(job.get("last_status"), str) else None,
        delivery_error_known="last_delivery_error" in job,
        delivery_failed=bool(delivery_value),
        last_output_sha256=output_hash,
        last_output_mtime=output_mtime,
    )


def reconcile_pending_deliveries(
    store: SQLiteEventStore,
    observation: CronDeliveryObservation,
    *,
    now: str | datetime | None = None,
) -> ReconciliationResult:
    """Finalize stdout candidates only after Hermes reports the run result."""
    if not observation.route_verified or not observation.last_run_at:
        return ReconciliationResult()
    try:
        completed_at = utc_iso(observation.last_run_at)
    except ValueError:
        return ReconciliationResult()

    eligible: list[str] = []
    unresolved = 0
    for attempt in store.notification_history():
        if (
            attempt.state != NotificationState.PENDING
            or not attempt.detail_code.startswith("cron_stdout_pending:")
        ):
            continue
        unresolved += 1
        digest = attempt.detail_code.partition(":")[2]
        if not _SHA256_RE.fullmatch(digest):
            continue
        if attempt.occurred_at > completed_at:
            continue
        if observation.last_output_sha256 != digest or not observation.last_output_mtime:
            continue
        try:
            attempted_at = datetime.fromisoformat(attempt.occurred_at.replace("Z", "+00:00"))
            output_at = datetime.fromisoformat(observation.last_output_mtime.replace("Z", "+00:00"))
        except ValueError:
            continue
        if abs((output_at - attempted_at).total_seconds()) <= 300:
            eligible.append(attempt.candidate_id)
    if not eligible:
        return ReconciliationResult(waiting=unresolved)

    if observation.last_status == "error" or observation.delivery_failed:
        state = NotificationState.FAILED
        detail_code = "hermes_delivery_failed"
    elif observation.last_status == "ok" and observation.delivery_error_known:
        state = NotificationState.SENT
        detail_code = "hermes_delivery_success"
    else:
        return ReconciliationResult(waiting=len(eligible))

    timestamp = utc_iso(now or datetime.now(timezone.utc))
    sent = failed = 0
    for candidate_id in eligible:
        store.append_notification_transition(
            candidate_id,
            state,
            occurred_at=timestamp,
            detail_code=detail_code,
        )
        if state == NotificationState.SENT:
            sent += 1
        else:
            failed += 1
    return ReconciliationResult(sent=sent, failed=failed)


class Phase4EventNormalizer:
    """Map only allowlisted, structured probe fields into deterministic Events."""

    def normalize(
        self,
        snapshot: Mapping[str, Any],
        *,
        store: SQLiteEventStore,
        now: str | datetime,
        openviking_healthy: bool | None,
        circuit_open: bool = False,
    ) -> list[Event]:
        if not isinstance(snapshot, Mapping):
            raise ValueError("malformed Proactive probe")
        cron_jobs = snapshot.get("cron_jobs")
        if not isinstance(cron_jobs, list):
            raise ValueError("malformed Cron probe")
        timestamp = utc_iso(now)
        events: list[Event] = []

        for row in cron_jobs:
            if not isinstance(row, Mapping):
                raise ValueError("malformed Cron probe row")
            enabled = row.get("enabled")
            job_id = row.get("id")
            status = row.get("last_status")
            streak = row.get("failure_streak")
            delivery_error = row.get("has_delivery_err")
            if (
                type(enabled) is not bool
                or not isinstance(job_id, str)
                or not job_id
                or len(job_id) > 160
                or (status is not None and not isinstance(status, str))
                or isinstance(streak, bool)
                or not isinstance(streak, int)
                or streak < 0
                or type(delivery_error) is not bool
            ):
                raise ValueError("malformed Cron probe row")
            if not enabled:
                continue

            normalized_status = (status or "").strip().casefold()
            failed = normalized_status in {"error", "failed"} or streak > 0 or delivery_error
            healthy = (
                normalized_status in {"ok", "success", "completed"}
                and streak == 0
                and not delivery_error
            )
            subject = "cron-" + hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:16]
            failure = Event(
                event_type="cron.failure",
                source="vps_cron",
                subject=subject,
                observed_at=timestamp,
                severity=Severity.HIGH if streak >= 3 or delivery_error else Severity.MEDIUM,
                condition={"state": "failed"},
                notification_requested=True,
            )
            if failed:
                events.append(failure)
            elif healthy:
                parent = store.open_event_by_fingerprint(failure.fingerprint)
                if parent is not None:
                    events.append(
                        Event(
                            event_type="cron.recovered",
                            source="vps_cron",
                            subject=subject,
                            observed_at=timestamp,
                            severity=Severity.MEDIUM,
                            condition={"state": "healthy"},
                            notification_requested=True,
                            recovery_of=parent.event_id,
                        )
                    )

        if openviking_healthy is not None:
            if type(openviking_healthy) is not bool:
                raise ValueError("invalid OpenViking health result")
            unhealthy = Event(
                event_type="openviking.unhealthy",
                source="openviking",
                subject="openviking-service",
                observed_at=timestamp,
                severity=Severity.MEDIUM,
                condition={"state": "unhealthy"},
                notification_requested=True,
            )
            if not openviking_healthy:
                events.append(unhealthy)
            else:
                parent = store.open_event_by_fingerprint(unhealthy.fingerprint)
                if parent is not None:
                    events.append(
                        Event(
                            event_type="openviking.recovered",
                            source="openviking",
                            subject="openviking-service",
                            observed_at=timestamp,
                            severity=Severity.MEDIUM,
                            condition={"state": "healthy"},
                            notification_requested=True,
                            recovery_of=parent.event_id,
                        )
                    )

        if circuit_open:
            events.append(
                Event(
                    event_type="proactive.circuit_breaker_open",
                    source="proactive_core",
                    subject="notification-pipeline",
                    observed_at=timestamp,
                    severity=Severity.HIGH,
                    condition={"state": "open"},
                    notification_requested=True,
                )
            )
        return sorted(events, key=lambda event: (event.event_type, event.subject, event.fingerprint))


def synthetic_delivery_test_event(now: str | datetime) -> Event:
    observed_at = utc_iso(now)
    expires_at = utc_iso(
        datetime.fromisoformat(observed_at.replace("Z", "+00:00")) + timedelta(hours=24)
    )
    return Event(
        event_type=PHASE4_TEST_EVENT_TYPE,
        source="manual_canary",
        subject="phase4-canary-test",
        observed_at=observed_at,
        severity=Severity.LOW,
        condition={"state": "test"},
        notification_requested=True,
        expires_at=expires_at,
        event_id=PHASE4_TEST_EVENT_ID,
    )


class Phase4NotificationRunner:
    """Production-notification candidate pipeline; it has no ACT or LLM adapter."""

    def __init__(
        self,
        store: SQLiteEventStore,
        switches: KillSwitches,
        *,
        target_verified: bool,
        policy: PolicyEvaluator | None = None,
        renderer: NotificationRenderer | None = None,
        budget: NotificationBudget | None = None,
        normalizer: Phase4EventNormalizer | None = None,
    ) -> None:
        self.store = store
        self.switches = switches
        self.target_verified = target_verified
        self.policy = policy or PolicyEvaluator()
        self.renderer = renderer or NotificationRenderer()
        self.budget = budget or NotificationBudget()
        self.normalizer = normalizer or Phase4EventNormalizer()

    def run_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        openviking_healthy: bool | None,
        circuit_open: bool = False,
        now: str | datetime | None = None,
    ) -> Phase4RunResult:
        timestamp = utc_iso(now or datetime.now(timezone.utc))
        try:
            events = self.normalizer.normalize(
                snapshot,
                store=self.store,
                now=timestamp,
                openviking_healthy=openviking_healthy,
                circuit_open=circuit_open,
            )
        except (ValueError, TypeError):
            return Phase4RunResult(error_code="malformed_probe")
        return self.run_events(events, now=timestamp)

    def run_events(
        self,
        events: Iterable[Event],
        *,
        now: str | datetime | None = None,
        allow_test_event: bool = False,
    ) -> Phase4RunResult:
        if not self.switches.proactive_enabled or not self.switches.notifications_enabled:
            return Phase4RunResult()
        if self.switches.actions_enabled:
            return Phase4RunResult(error_code="actions_switch_must_remain_false")
        if not self.target_verified:
            return Phase4RunResult(error_code="delivery_target_unverified")

        timestamp = utc_iso(now or datetime.now(timezone.utc))
        allowed = PHASE4_ALLOWLIST | ({PHASE4_TEST_EVENT_TYPE} if allow_test_event else set())
        processed = candidates_created = 0
        candidates: list[tuple[str, Event]] = []
        pending: list[tuple[str, Event]] = []
        try:
            self.store.expire_due(timestamp)
            for incoming in events:
                if not isinstance(incoming, Event) or incoming.event_type not in allowed:
                    raise ValueError("event outside the Phase 4 allowlist")
                if incoming.action_requested or incoming.action_scope != ActionScope.NONE or incoming.urgent:
                    raise ValueError("Phase 4 accepts notification-only events")

                recorded = self.store.record_event(incoming)
                event = self.store.get_event(recorded.event.event_id) or recorded.event
                decision = self.policy.evaluate(event, now=timestamp)
                if decision.action_decision.value != "NONE":
                    raise ValueError("Phase 4 action decision is forbidden")
                self.store.append_decision(decision)
                processed += 1
                if decision.notification_decision != NotificationDecision.NOTIFY:
                    continue

                candidate_id, created = self.store.add_notification_candidate(
                    event,
                    decision,
                    created_at=timestamp,
                )
                if not created:
                    reserved = next(
                        (
                            item
                            for item in self.store.notification_budget_history()
                            if item.candidate_id == candidate_id
                            and item.detail_code == "candidate_reserved_no_delivery"
                        ),
                        None,
                    )
                    if reserved is not None:
                        self._suppress_unstarted(
                            [(candidate_id, event)],
                            timestamp,
                            "orphaned_candidate_no_delivery",
                        )
                    continue
                candidates_created += 1
                current = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
                protection_window = max(
                    60 * 60,
                    self.budget.config.normal_fingerprint_cooldown_seconds,
                    self.budget.config.urgent_fingerprint_cooldown_seconds,
                )
                history_start = min(
                    day_start,
                    current - timedelta(seconds=protection_window),
                )
                history = self.store.notification_budget_history(since=utc_iso(history_start))
                budget = self.budget.evaluate(
                    decision=decision.notification_decision,
                    fingerprint=event.fingerprint,
                    escalation_level=event.escalation_level,
                    now=timestamp,
                    history=(item for item in history if item.candidate_id != candidate_id),
                )
                if not budget.allowed:
                    self.store.append_notification_transition(
                        candidate_id,
                        NotificationState.SUPPRESSED,
                        occurred_at=timestamp,
                        detail_code=budget.reason_code,
                    )
                    continue
                candidates.append((candidate_id, event))

            if not candidates:
                return Phase4RunResult(
                    processed_events=processed,
                    notification_candidates_created=candidates_created,
                )
            rendered = self.renderer.render_phase4_aggregate(event for _, event in candidates)
            if not rendered:
                self._suppress_unstarted(candidates, timestamp, "renderer_empty")
                return Phase4RunResult(
                    processed_events=processed,
                    notification_candidates_created=candidates_created,
                    error_code="renderer_empty",
                )
            rendered_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            for candidate_id, event in candidates:
                self.store.append_notification_transition(
                    candidate_id,
                    NotificationState.PENDING,
                    occurred_at=timestamp,
                    detail_code=f"cron_stdout_pending:{rendered_hash}",
                )
                pending.append((candidate_id, event))
            return Phase4RunResult(
                stdout_text=rendered + "\n",
                processed_events=processed,
                notification_candidates_created=candidates_created,
                pending_delivery_count=len(pending),
            )
        except StoreBusyError:
            self._fail_pending(pending, timestamp, "event_store_busy")
            self._suppress_unstarted(candidates, timestamp, "event_store_busy")
            return Phase4RunResult(
                processed_events=processed,
                notification_candidates_created=candidates_created,
                error_code="event_store_busy",
            )
        except (StoreError, ValueError, TypeError):
            self._fail_pending(pending, timestamp, "phase4_processing_failed")
            self._suppress_unstarted(candidates, timestamp, "phase4_processing_failed")
            return Phase4RunResult(
                processed_events=processed,
                notification_candidates_created=candidates_created,
                error_code="phase4_processing_failed",
            )
        except Exception:
            self._fail_pending(pending, timestamp, "phase4_processing_failed")
            self._suppress_unstarted(candidates, timestamp, "phase4_processing_failed")
            return Phase4RunResult(
                processed_events=processed,
                notification_candidates_created=candidates_created,
                error_code="phase4_processing_failed",
            )

    def _fail_pending(
        self,
        pending: list[tuple[str, Event]],
        timestamp: str,
        detail_code: str,
    ) -> None:
        for candidate_id, _event in pending:
            try:
                self.store.append_notification_transition(
                    candidate_id,
                    NotificationState.FAILED,
                    occurred_at=timestamp,
                    detail_code=detail_code,
                )
            except StoreError:
                pass

    def _suppress_unstarted(
        self,
        candidates: list[tuple[str, Event]],
        timestamp: str,
        detail_code: str,
    ) -> None:
        for candidate_id, _event in candidates:
            try:
                history = self.store.notification_history()
                if any(
                    item.candidate_id == candidate_id
                    and item.state == NotificationState.PENDING
                    for item in history
                ):
                    continue
                if any(item.candidate_id == candidate_id for item in history):
                    continue
                self.store.append_notification_transition(
                    candidate_id,
                    NotificationState.SUPPRESSED,
                    occurred_at=timestamp,
                    detail_code=detail_code,
                )
            except StoreError:
                pass
