"""Versioned Event, Decision, risk, and lifecycle contracts."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .privacy import sanitize_text, sanitize_value


SCHEMA_VERSION = 1
POLICY_VERSION = "pc-v1-phase1"
_ATOM = re.compile(r"^[A-Za-z0-9_.:/-]{1,96}$")
_SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$")
_OPAQUE_REF = re.compile(r"^(?:ref|evt|doc):[A-Za-z0-9._-]{1,120}$")
_HEX_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


class ContractError(ValueError):
    """Raised for invalid or unsafe contract values; messages never echo payloads."""


class EventStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Risk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ActionScope(StrEnum):
    NONE = "NONE"
    READ = "READ"
    STATUS = "STATUS"
    RETRY_SAFE = "RETRY_SAFE"
    INTERNAL_REVERSIBLE = "INTERNAL_REVERSIBLE"
    EXTERNAL_COMMUNICATION = "EXTERNAL_COMMUNICATION"
    HIGH_RISK = "HIGH_RISK"


class HighLevelDecision(StrEnum):
    IGNORE = "IGNORE"
    SILENT = "SILENT"
    ACT = "ACT"
    NOTIFY = "NOTIFY"
    ASK = "ASK"
    URGENT = "URGENT"


class ActionDecision(StrEnum):
    NONE = "NONE"
    ACT = "ACT"
    ASK = "ASK"


class NotificationDecision(StrEnum):
    NONE = "NONE"
    NOTIFY = "NOTIFY"
    URGENT = "URGENT"


class NotificationState(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


class ActionState(StrEnum):
    PLANNED = "PLANNED"
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN_AFTER_RESTART = "UNKNOWN_AFTER_RESTART"


class GoalCandidateDecision(StrEnum):
    SILENT = "SILENT"
    NOTIFY_CANDIDATE = "NOTIFY_CANDIDATE"
    ASK_CANDIDATE = "ASK_CANDIDATE"


def utc_iso(value: str | datetime | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ContractError("invalid timestamp") from error
    else:
        raise ContractError("invalid timestamp")
    if parsed.tzinfo is None:
        raise ContractError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _safe_atom(value: str, *, name: str, max_length: int = 96) -> str:
    if not isinstance(value, str):
        raise ContractError(f"invalid {name}")
    result = sanitize_text(value, limit=max_length)
    if not result or len(result) > max_length or not _ATOM.fullmatch(result):
        raise ContractError(f"invalid {name}")
    return result


def _safe_subject(value: str) -> str:
    if not isinstance(value, str):
        raise ContractError("invalid subject")
    result = sanitize_text(value, limit=120)
    if not _SUBJECT.fullmatch(result):
        raise ContractError("invalid subject")
    return result


def _condition_digest(condition: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        sanitize_value(condition), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _freeze_condition(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_condition(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_condition(child) for child in value)
    return value


def _fingerprint(source: str, event_type: str, subject: str, condition_digest: str) -> str:
    canonical = json.dumps(
        [source, event_type, subject, condition_digest],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Event:
    event_type: str
    source: str
    subject: str
    observed_at: str = field(default_factory=utc_iso)
    severity: Severity = Severity.LOW
    condition: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    action_scope: ActionScope = ActionScope.NONE
    action_requested: bool = False
    notification_requested: bool = False
    urgent: bool = False
    event_id: str = field(default_factory=lambda: "evt_" + uuid.uuid4().hex)
    fingerprint: str = ""
    condition_digest: str = ""
    status: EventStatus = EventStatus.OPEN
    resolved_at: str | None = None
    expired_at: str | None = None
    recovery_of: str | None = None
    expires_at: str | None = None
    payload_ref: str | None = None
    escalation_level: int = 0
    occurrence_count: int = 1
    first_seen: str = ""
    last_seen: str = ""
    schema_version: int = SCHEMA_VERSION
    policy_version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        event_type = _safe_atom(self.event_type, name="event_type")
        source = _safe_atom(self.source, name="source", max_length=48)
        subject = _safe_subject(self.subject)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "subject", subject)

        try:
            object.__setattr__(self, "severity", Severity(self.severity))
            object.__setattr__(self, "action_scope", ActionScope(self.action_scope))
            object.__setattr__(self, "status", EventStatus(self.status))
        except ValueError as error:
            raise ContractError("invalid enum value") from error

        if not isinstance(self.condition, Mapping):
            raise ContractError("condition must be an object")
        cleaned = sanitize_value(self.condition)
        if not isinstance(cleaned, dict):
            raise ContractError("condition must be an object")
        object.__setattr__(self, "condition", _freeze_condition(cleaned))

        observed = utc_iso(self.observed_at)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "first_seen", utc_iso(self.first_seen or observed))
        object.__setattr__(self, "last_seen", utc_iso(self.last_seen or observed))
        for name in ("resolved_at", "expired_at", "expires_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, utc_iso(value))

        if not isinstance(self.event_id, str) or not re.fullmatch(r"evt_[0-9a-f]{32}", self.event_id):
            raise ContractError("invalid event_id")
        if self.action_requested and self.action_scope == ActionScope.NONE:
            raise ContractError("requested action requires a scope")
        if not self.action_requested and self.action_scope != ActionScope.NONE:
            raise ContractError("action scope without a requested action")
        if any(not isinstance(flag, bool) for flag in (self.action_requested, self.notification_requested, self.urgent)):
            raise ContractError("invalid decision flag")
        if isinstance(self.escalation_level, bool) or not isinstance(self.escalation_level, int) or not 0 <= self.escalation_level <= 100:
            raise ContractError("invalid escalation level")
        if isinstance(self.occurrence_count, bool) or not isinstance(self.occurrence_count, int) or self.occurrence_count < 1:
            raise ContractError("invalid occurrence count")
        if self.schema_version != SCHEMA_VERSION:
            raise ContractError("unsupported schema version")
        object.__setattr__(self, "policy_version", _safe_atom(self.policy_version, name="policy_version"))

        digest = self.condition_digest or _condition_digest(cleaned)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ContractError("invalid condition digest")
        object.__setattr__(self, "condition_digest", digest)
        computed = _fingerprint(source, event_type, subject, digest)
        if self.fingerprint:
            if not _HEX_FINGERPRINT.fullmatch(self.fingerprint) or self.fingerprint != computed:
                raise ContractError("fingerprint does not match the event")
        else:
            object.__setattr__(self, "fingerprint", computed)

        if self.status == EventStatus.RESOLVED and self.resolved_at is None:
            raise ContractError("resolved event requires resolved_at")
        if self.status != EventStatus.RESOLVED and self.resolved_at is not None:
            raise ContractError("resolved_at only applies to resolved events")
        if self.status == EventStatus.EXPIRED and self.expired_at is None:
            raise ContractError("expired event requires expired_at")
        if self.status != EventStatus.EXPIRED and self.expired_at is not None:
            raise ContractError("expired_at only applies to expired events")
        if self.recovery_of is not None:
            if not isinstance(self.recovery_of, str) or not re.fullmatch(r"evt_[0-9a-f]{32}", self.recovery_of):
                raise ContractError("invalid recovery_of")
            if "recover" not in event_type.casefold():
                raise ContractError("recovery_of requires a recovery event type")
            if self.action_requested:
                raise ContractError("recovery events cannot request actions")
        if self.payload_ref is not None:
            if (
                not isinstance(self.payload_ref, str)
                or not _OPAQUE_REF.fullmatch(self.payload_ref)
                or any(part == ".." for part in self.payload_ref.split("/"))
                or sanitize_text(self.payload_ref, limit=128) != self.payload_ref
            ):
                raise ContractError("payload_ref must be an opaque reference")

    @classmethod
    def from_fixture(cls, raw: Mapping[str, Any]) -> "Event":
        if not isinstance(raw, Mapping):
            raise ContractError("event fixture must be an object")
        allowed = {
            "action_requested",
            "action_scope",
            "condition",
            "event_id",
            "event_type",
            "expires_at",
            "notification_requested",
            "observed_at",
            "payload_ref",
            "recovery_of",
            "severity",
            "source",
            "subject",
            "urgent",
            "escalation_level",
        }
        if set(raw) - allowed:
            raise ContractError("event fixture has unexpected fields")
        try:
            return cls(**dict(raw))
        except TypeError as error:
            raise ContractError("event fixture is missing or has invalid fields") from error

    def to_fixture(self) -> dict[str, Any]:
        return {
            "action_requested": self.action_requested,
            "action_scope": self.action_scope.value,
            "condition": sanitize_value(self.condition),
            "event_type": self.event_type,
            "escalation_level": self.escalation_level,
            "expires_at": self.expires_at,
            "notification_requested": self.notification_requested,
            "observed_at": self.observed_at,
            "payload_ref": self.payload_ref,
            "recovery_of": self.recovery_of,
            "severity": self.severity.value,
            "source": self.source,
            "subject": self.subject,
            "urgent": self.urgent,
        }


@dataclass(frozen=True, slots=True)
class Decision:
    event_id: str
    high_level: HighLevelDecision
    action_decision: ActionDecision
    notification_decision: NotificationDecision
    risk: Risk
    reason_code: str
    evaluated_at: str = field(default_factory=utc_iso)
    policy_version: str = POLICY_VERSION
    decision_id: str = field(default_factory=lambda: "dec_" + uuid.uuid4().hex)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "high_level", HighLevelDecision(self.high_level))
            object.__setattr__(self, "action_decision", ActionDecision(self.action_decision))
            object.__setattr__(self, "notification_decision", NotificationDecision(self.notification_decision))
            object.__setattr__(self, "risk", Risk(self.risk))
        except ValueError as error:
            raise ContractError("invalid decision enum") from error
        object.__setattr__(self, "evaluated_at", utc_iso(self.evaluated_at))
        object.__setattr__(self, "policy_version", _safe_atom(self.policy_version, name="policy_version"))
        object.__setattr__(self, "reason_code", _safe_atom(self.reason_code, name="reason_code"))
        if not re.fullmatch(r"evt_[0-9a-f]{32}", self.event_id):
            raise ContractError("invalid event_id")
        if not re.fullmatch(r"dec_[0-9a-f]{32}", self.decision_id):
            raise ContractError("invalid decision_id")
        valid_dimensions = {
            HighLevelDecision.IGNORE: (
                self.action_decision == ActionDecision.NONE
                and self.notification_decision == NotificationDecision.NONE
            ),
            HighLevelDecision.SILENT: (
                self.action_decision == ActionDecision.NONE
                and self.notification_decision == NotificationDecision.NONE
            ),
            HighLevelDecision.ACT: (
                self.action_decision == ActionDecision.ACT
                and self.notification_decision != NotificationDecision.URGENT
                and self.risk == Risk.LOW
            ),
            HighLevelDecision.NOTIFY: (
                self.action_decision == ActionDecision.NONE
                and self.notification_decision == NotificationDecision.NOTIFY
            ),
            HighLevelDecision.ASK: (
                self.action_decision == ActionDecision.ASK
                and self.notification_decision != NotificationDecision.URGENT
            ),
            HighLevelDecision.URGENT: self.notification_decision == NotificationDecision.URGENT,
        }
        if not valid_dimensions[self.high_level]:
            raise ContractError("inconsistent decision dimensions")
        if self.action_decision == ActionDecision.ACT and self.risk != Risk.LOW:
            raise ContractError("non-low-risk action cannot be automatic")


@dataclass(frozen=True, slots=True)
class RecordResult:
    event: Event
    created: bool
    duplicate: bool
    recovered_event_created: bool = False


@dataclass(frozen=True, slots=True)
class NotificationAttempt:
    candidate_id: str
    attempt_number: int
    state: NotificationState
    notification_decision: NotificationDecision
    occurred_at: str
    fingerprint: str
    severity: Severity
    escalation_level: int
    detail_code: str = ""

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "state", NotificationState(self.state))
            object.__setattr__(
                self,
                "notification_decision",
                NotificationDecision(self.notification_decision),
            )
            object.__setattr__(self, "severity", Severity(self.severity))
        except ValueError as error:
            raise ContractError("invalid notification state") from error
        object.__setattr__(self, "occurred_at", utc_iso(self.occurred_at))
        if not re.fullmatch(r"ncan_[0-9a-f]{32}", self.candidate_id):
            raise ContractError("invalid notification candidate id")
        if isinstance(self.attempt_number, bool) or not isinstance(self.attempt_number, int) or self.attempt_number < 1:
            raise ContractError("invalid notification attempt")
        if not _HEX_FINGERPRINT.fullmatch(self.fingerprint):
            raise ContractError("invalid fingerprint")
        object.__setattr__(self, "detail_code", _safe_atom(self.detail_code or "none", name="detail_code"))


@dataclass(frozen=True, slots=True)
class ActionAttempt:
    action_id: str
    attempt_number: int
    state: ActionState
    occurred_at: str
    event_id: str
    idempotency_key: str
    action_type: str
    detail_code: str = ""

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "state", ActionState(self.state))
        except ValueError as error:
            raise ContractError("invalid action state") from error
        object.__setattr__(self, "occurred_at", utc_iso(self.occurred_at))
        if isinstance(self.attempt_number, bool) or not isinstance(self.attempt_number, int) or self.attempt_number < 1:
            raise ContractError("invalid action attempt")
        if not re.fullmatch(r"act_[0-9a-f]{32}", self.action_id):
            raise ContractError("invalid action id")
        if not re.fullmatch(r"evt_[0-9a-f]{32}", self.event_id):
            raise ContractError("invalid event_id")
        if not re.fullmatch(r"[0-9a-f]{64}", self.idempotency_key):
            raise ContractError("invalid idempotency key")
        object.__setattr__(self, "action_type", _safe_atom(self.action_type, name="action_type"))
        object.__setattr__(self, "detail_code", _safe_atom(self.detail_code or "none", name="detail_code"))
