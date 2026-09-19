"""Private, transactional SQLite state and append-only audit records."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .model import (
    ActionAttempt,
    ActionDecision,
    ActionState,
    ActionScope,
    Decision,
    Event,
    EventStatus,
    NotificationAttempt,
    NotificationDecision,
    NotificationState,
    POLICY_VERSION,
    RecordResult,
    SCHEMA_VERSION,
    Severity,
    utc_iso,
)
from .policy import PolicyEvaluator


class StoreError(RuntimeError):
    pass


class StoreBusyError(StoreError):
    pass


class UnsafeStoreDirectory(StoreError):
    pass


class UnsupportedSchema(StoreError):
    pass


RETENTION_RESOLVED_DAYS = 30
RETENTION_METRICS_DAYS = 90
_MARKER_NAME = ".private-store-v1"


def _windows_identity() -> str:
    whoami = shutil.which("whoami.exe") or shutil.which("whoami")
    if not whoami:
        raise UnsafeStoreDirectory("cannot resolve current Windows identity")
    result = subprocess.run(
        [whoami],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    identity = result.stdout.strip()
    if not identity or any(char in identity for char in "\r\n:"):
        raise UnsafeStoreDirectory("invalid Windows identity")
    return identity


def _windows_acl(path: Path, *, directory: bool) -> None:
    icacls = shutil.which("icacls.exe") or shutil.which("icacls")
    if not icacls:
        raise UnsafeStoreDirectory("icacls is required to protect the local store")
    identity = _windows_identity()
    rights = f"{identity}:(OI)(CI)F" if directory else f"{identity}:F"
    subprocess.run(
        [icacls, str(path), "/inheritance:r", "/grant:r", rights],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    target_prefix = str(path)

    def read_entries() -> list[tuple[str, str]]:
        result = subprocess.run(
            [icacls, str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() and not line.lstrip().casefold().startswith("successfully processed")
        ]
        if not lines or not lines[0].casefold().startswith(target_prefix.casefold()):
            raise UnsafeStoreDirectory("store ACL is not private to the current user")
        entries = [lines[0][len(target_prefix):].strip(), *lines[1:]]
        parsed: list[tuple[str, str]] = []
        for entry in entries:
            principal, separator, permissions = entry.partition(":")
            if not separator or not principal.strip() or not permissions.strip():
                raise UnsafeStoreDirectory("store ACL could not be parsed")
            parsed.append((principal.strip(), permissions.strip()))
        return parsed

    entries = read_entries()
    for principal, _permissions in entries:
        if principal.casefold() != identity.casefold():
            subprocess.run(
                [icacls, str(path), "/remove", principal],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
    entries = read_entries()
    expected_permissions = f"{'(OI)(CI)' if directory else ''}(F)"
    if (
        len(entries) != 1
        or entries[0][0].casefold() != identity.casefold()
        or entries[0][1].casefold() != expected_permissions.casefold()
    ):
        raise UnsafeStoreDirectory("store ACL verification failed")


def _secure_directory(path: Path) -> None:
    if path.is_symlink():
        raise UnsafeStoreDirectory("store directory cannot be a symlink")
    if os.name == "nt":
        _windows_acl(path, directory=True)
        return
    try:
        path.chmod(0o700)
    except OSError as error:
        raise UnsafeStoreDirectory("cannot set private directory permissions") from error
    if path.stat().st_mode & 0o077:
        raise UnsafeStoreDirectory("store directory is accessible to group or others")


def _secure_file(path: Path) -> None:
    if path.is_symlink():
        raise UnsafeStoreDirectory("store file cannot be a symlink")
    if os.name == "nt":
        _windows_acl(path, directory=False)
        return
    try:
        path.chmod(0o600)
    except OSError as error:
        raise UnsafeStoreDirectory("cannot set private database permissions") from error
    if path.stat().st_mode & 0o077:
        raise UnsafeStoreDirectory("database is accessible to group or others")


class SQLiteEventStore:
    """A project-local store; directories must be created and owned by this store."""

    def __init__(
        self,
        state_dir: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        policy_version: str = POLICY_VERSION,
    ) -> None:
        if not isinstance(busy_timeout_ms, int) or not 1 <= busy_timeout_ms <= 60000:
            raise ValueError("busy timeout must be between 1 and 60000 ms")
        if not isinstance(policy_version, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,96}", policy_version):
            raise ValueError("invalid policy version")
        self.state_dir = Path(state_dir).expanduser().absolute()
        self.db_path = self.state_dir / "events.sqlite3"
        self.busy_timeout_ms = busy_timeout_ms
        self.policy_version = policy_version
        self._prepare_private_directory()
        self._initialize()

    def _prepare_private_directory(self) -> None:
        if self.state_dir.is_symlink():
            raise UnsafeStoreDirectory("store directory cannot be a symlink")
        marker = self.state_dir / _MARKER_NAME
        if not self.state_dir.exists():
            if not self.state_dir.parent.exists():
                raise UnsafeStoreDirectory("store parent must already exist")
            self.state_dir.mkdir(mode=0o700)
            _secure_directory(self.state_dir)
            descriptor = os.open(
                marker,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write("private-store-v1\n")
                handle.flush()
                os.fsync(handle.fileno())
            _secure_file(marker)
        else:
            if not self.state_dir.is_dir() or not marker.is_file() or marker.is_symlink():
                raise UnsafeStoreDirectory("existing directory is not a Proactive Core store")
            try:
                marker_value = marker.read_text(encoding="ascii").strip()
            except OSError as error:
                raise UnsafeStoreDirectory("cannot verify private store marker") from error
            if marker_value != "private-store-v1":
                raise UnsafeStoreDirectory("private store marker is invalid")
            _secure_directory(self.state_dir)
            _secure_file(marker)
        if self.db_path.is_symlink():
            raise UnsafeStoreDirectory("database cannot be a symlink")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except sqlite3.DatabaseError as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            message = str(error).casefold()
            if "locked" in message or "busy" in message:
                raise StoreBusyError("event database is busy") from None
            raise StoreError("event database operation failed") from None
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=100")
            _secure_file(self.db_path)
            connection.execute("BEGIN IMMEDIATE")
            statements = (
                """CREATE TABLE IF NOT EXISTS store_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS maintenance_control (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    retention_prune INTEGER NOT NULL CHECK (retention_prune IN (0, 1))
                )""",
                """CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    condition_digest TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    severity TEXT NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH')),
                    action_scope TEXT NOT NULL,
                    action_requested INTEGER NOT NULL CHECK (action_requested IN (0,1)),
                    notification_requested INTEGER NOT NULL CHECK (notification_requested IN (0,1)),
                    urgent INTEGER NOT NULL CHECK (urgent IN (0,1)),
                    status TEXT NOT NULL CHECK (status IN ('OPEN','RESOLVED','EXPIRED')),
                    resolved_at TEXT,
                    expired_at TEXT,
                    recovery_of TEXT REFERENCES events(event_id),
                    expires_at TEXT,
                    payload_ref TEXT,
                    escalation_level INTEGER NOT NULL DEFAULT 0,
                    occurrence_count INTEGER NOT NULL DEFAULT 1,
                    schema_version INTEGER NOT NULL,
                    policy_version TEXT NOT NULL,
                    CHECK ((status = 'RESOLVED' AND resolved_at IS NOT NULL) OR
                           (status != 'RESOLVED' AND resolved_at IS NULL)),
                    CHECK ((status = 'EXPIRED' AND expired_at IS NOT NULL) OR
                           (status != 'EXPIRED' AND expired_at IS NULL)),
                    CHECK (recovery_of IS NULL OR
                           (status = 'RESOLVED' AND resolved_at IS NOT NULL))
                )""",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_open_fingerprint ON events(fingerprint) WHERE status='OPEN'",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_recovery_parent ON events(recovery_of) WHERE recovery_of IS NOT NULL",
                "CREATE INDEX IF NOT EXISTS ix_open_event_expiry ON events(expires_at) WHERE status='OPEN' AND expires_at IS NOT NULL",
                """CREATE TABLE IF NOT EXISTS event_observations (
                    observation_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                    observation_type TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    escalation_level INTEGER NOT NULL,
                    condition_digest TEXT NOT NULL,
                    policy_version TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                    evaluated_at TEXT NOT NULL,
                    high_level TEXT NOT NULL CHECK (high_level IN ('IGNORE','SILENT','ACT','NOTIFY','ASK','URGENT')),
                    action_decision TEXT NOT NULL CHECK (action_decision IN ('NONE','ACT','ASK')),
                    notification_decision TEXT NOT NULL CHECK (notification_decision IN ('NONE','NOTIFY','URGENT')),
                    risk TEXT NOT NULL CHECK (risk IN ('LOW','MEDIUM','HIGH')),
                    reason_code TEXT NOT NULL,
                    policy_version TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS notification_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL,
                    notification_decision TEXT NOT NULL CHECK (notification_decision IN ('NOTIFY','URGENT')),
                    severity TEXT NOT NULL,
                    escalation_level INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    UNIQUE(event_id, notification_decision, escalation_level, policy_version)
                )""",
                """CREATE TABLE IF NOT EXISTS notification_transitions (
                    transition_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL REFERENCES notification_candidates(candidate_id) ON DELETE CASCADE,
                    attempt_number INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('PENDING','SENT','FAILED','SUPPRESSED')),
                    occurred_at TEXT NOT NULL,
                    detail_code TEXT NOT NULL,
                    UNIQUE(candidate_id, attempt_number, state)
                )""",
                """CREATE TABLE IF NOT EXISTS action_candidates (
                    action_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    action_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    UNIQUE(event_id, action_type, policy_version)
                )""",
                """CREATE TABLE IF NOT EXISTS action_transitions (
                    transition_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL REFERENCES action_candidates(action_id) ON DELETE CASCADE,
                    attempt_number INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('PLANNED','STARTED','SUCCEEDED','FAILED','UNKNOWN_AFTER_RESTART')),
                    occurred_at TEXT NOT NULL,
                    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                    idempotency_key TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    detail_code TEXT NOT NULL,
                    UNIQUE(action_id, attempt_number, state)
                )""",
                """CREATE TABLE IF NOT EXISTS metric_aggregates (
                    day TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    value INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(day, metric_name)
                )""",
                """CREATE TABLE IF NOT EXISTS llm_outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                    stage TEXT NOT NULL CHECK (stage IN (
                        'NEXT_ACTION_CANDIDATE','SEMANTIC_RANKING','SHORT_SUMMARY'
                    )),
                    outcome_code TEXT NOT NULL,
                    calls INTEGER NOT NULL CHECK (calls IN (0,1)),
                    occurred_at TEXT NOT NULL,
                    policy_version TEXT NOT NULL
                )""",
                "INSERT OR IGNORE INTO maintenance_control(singleton, retention_prune) VALUES (1, 0)",
                "CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions BEGIN SELECT RAISE(ABORT, 'append-only'); END",
                "CREATE TRIGGER IF NOT EXISTS observations_no_update BEFORE UPDATE ON event_observations BEGIN SELECT RAISE(ABORT, 'append-only'); END",
                "CREATE TRIGGER IF NOT EXISTS candidates_no_update BEFORE UPDATE ON notification_candidates BEGIN SELECT RAISE(ABORT, 'append-only'); END",
                "CREATE TRIGGER IF NOT EXISTS notifications_no_update BEFORE UPDATE ON notification_transitions BEGIN SELECT RAISE(ABORT, 'append-only'); END",
                "CREATE TRIGGER IF NOT EXISTS action_candidates_no_update BEFORE UPDATE ON action_candidates BEGIN SELECT RAISE(ABORT, 'append-only'); END",
                "CREATE TRIGGER IF NOT EXISTS actions_no_update BEFORE UPDATE ON action_transitions BEGIN SELECT RAISE(ABORT, 'append-only'); END",
                "CREATE TRIGGER IF NOT EXISTS llm_outcomes_no_update BEFORE UPDATE ON llm_outcomes BEGIN SELECT RAISE(ABORT, 'append-only'); END",
                """CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                    WHEN (SELECT retention_prune FROM maintenance_control WHERE singleton=1) != 1
                    BEGIN SELECT RAISE(ABORT, 'retention only'); END""",
                """CREATE TRIGGER IF NOT EXISTS observations_no_delete BEFORE DELETE ON event_observations
                    WHEN (SELECT retention_prune FROM maintenance_control WHERE singleton=1) != 1
                    BEGIN SELECT RAISE(ABORT, 'retention only'); END""",
                """CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
                    WHEN (SELECT retention_prune FROM maintenance_control WHERE singleton=1) != 1
                    BEGIN SELECT RAISE(ABORT, 'retention only'); END""",
                """CREATE TRIGGER IF NOT EXISTS candidates_no_delete BEFORE DELETE ON notification_candidates
                    WHEN (SELECT retention_prune FROM maintenance_control WHERE singleton=1) != 1
                    BEGIN SELECT RAISE(ABORT, 'retention only'); END""",
                """CREATE TRIGGER IF NOT EXISTS notifications_no_delete BEFORE DELETE ON notification_transitions
                    WHEN (SELECT retention_prune FROM maintenance_control WHERE singleton=1) != 1
                    BEGIN SELECT RAISE(ABORT, 'retention only'); END""",
                """CREATE TRIGGER IF NOT EXISTS action_candidates_no_delete BEFORE DELETE ON action_candidates
                    WHEN (SELECT retention_prune FROM maintenance_control WHERE singleton=1) != 1
                    BEGIN SELECT RAISE(ABORT, 'retention only'); END""",
                """CREATE TRIGGER IF NOT EXISTS actions_no_delete BEFORE DELETE ON action_transitions
                    WHEN (SELECT retention_prune FROM maintenance_control WHERE singleton=1) != 1
                    BEGIN SELECT RAISE(ABORT, 'retention only'); END""",
                """CREATE TRIGGER IF NOT EXISTS llm_outcomes_no_delete BEFORE DELETE ON llm_outcomes
                    WHEN (SELECT retention_prune FROM maintenance_control WHERE singleton=1) != 1
                    BEGIN SELECT RAISE(ABORT, 'retention only'); END""",
            )
            for statement in statements:
                connection.execute(statement)
            current_schema = connection.execute(
                "SELECT value FROM store_metadata WHERE key='schema_version'"
            ).fetchone()
            if current_schema is not None and int(current_schema["value"]) != SCHEMA_VERSION:
                raise UnsupportedSchema("unsupported event database schema")
            current_policy = connection.execute(
                "SELECT value FROM store_metadata WHERE key='policy_version'"
            ).fetchone()
            if current_policy is not None and current_policy["value"] != self.policy_version:
                raise UnsupportedSchema("event database policy version mismatch")
            connection.execute(
                "INSERT OR IGNORE INTO store_metadata(key,value) VALUES ('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO store_metadata(key,value) VALUES ('policy_version',?)",
                (self.policy_version,),
            )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except sqlite3.DatabaseError as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            message = str(error).casefold()
            if "locked" in message or "busy" in message:
                raise StoreBusyError("event database is busy") from None
            raise StoreError("event database initialization failed") from None
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        _secure_file(self.db_path)

    @property
    def metadata(self) -> dict[str, str]:
        with self.reader() as connection:
            rows = connection.execute("SELECT key, value FROM store_metadata").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        event: Event,
        *,
        status: EventStatus,
        resolved_at: str | None = None,
        expired_at: str | None = None,
        recovery_of: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO events(
                event_id,fingerprint,condition_digest,event_type,source,subject,
                observed_at,first_seen,last_seen,severity,action_scope,action_requested,
                notification_requested,urgent,status,resolved_at,expired_at,recovery_of,
                expires_at,payload_ref,escalation_level,occurrence_count,schema_version,policy_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event.event_id,
                event.fingerprint,
                event.condition_digest,
                event.event_type,
                event.source,
                event.subject,
                event.observed_at,
                event.first_seen,
                event.last_seen,
                event.severity.value,
                event.action_scope.value,
                int(event.action_requested),
                int(event.notification_requested),
                int(event.urgent),
                status.value,
                resolved_at,
                expired_at,
                recovery_of,
                event.expires_at,
                event.payload_ref,
                event.escalation_level,
                event.occurrence_count,
                event.schema_version,
                event.policy_version,
            ),
        )

    @staticmethod
    def _append_observation(
        connection: sqlite3.Connection,
        event: Event,
        observation_type: str,
        observed_at: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO event_observations(
                observation_id,event_id,observation_type,observed_at,severity,
                escalation_level,condition_digest,policy_version
            ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                "obs_" + uuid.uuid4().hex,
                event.event_id,
                observation_type,
                utc_iso(observed_at or event.observed_at),
                event.severity.value,
                event.escalation_level,
                event.condition_digest,
                event.policy_version,
            ),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> Event:
        return Event(
            event_id=row["event_id"],
            event_type=row["event_type"],
            source=row["source"],
            subject=row["subject"],
            observed_at=row["observed_at"],
            severity=row["severity"],
            action_scope=row["action_scope"],
            action_requested=bool(row["action_requested"]),
            notification_requested=bool(row["notification_requested"]),
            urgent=bool(row["urgent"]),
            fingerprint=row["fingerprint"],
            condition_digest=row["condition_digest"],
            status=row["status"],
            resolved_at=row["resolved_at"],
            expired_at=row["expired_at"],
            recovery_of=row["recovery_of"],
            expires_at=row["expires_at"],
            payload_ref=row["payload_ref"],
            escalation_level=row["escalation_level"],
            occurrence_count=row["occurrence_count"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            schema_version=row["schema_version"],
            policy_version=row["policy_version"],
        )

    def _expire_due_in_transaction(self, connection: sqlite3.Connection, timestamp: str) -> int:
        rows = connection.execute(
            """SELECT * FROM events
               WHERE status='OPEN' AND expires_at IS NOT NULL AND expires_at<=?""",
            (timestamp,),
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE events SET status='EXPIRED', expired_at=?, last_seen=? WHERE event_id=?",
                (timestamp, timestamp, row["event_id"]),
            )
            expired = self._event_from_row(
                connection.execute("SELECT * FROM events WHERE event_id=?", (row["event_id"],)).fetchone()
            )
            self._append_observation(connection, expired, "EXPIRED", timestamp)
        return len(rows)

    def record_event(self, event: Event) -> RecordResult:
        if event.status != EventStatus.OPEN:
            raise StoreError("only newly observed events may be recorded")
        if event.policy_version != self.policy_version:
            raise StoreError("event policy version mismatch")
        with self.transaction() as connection:
            self._expire_due_in_transaction(connection, event.observed_at)
            if event.recovery_of:
                parent = connection.execute(
                    "SELECT * FROM events WHERE event_id=?",
                    (event.recovery_of,),
                ).fetchone()
                if parent is None:
                    raise StoreError("recovery parent does not exist")
                existing_recovery = connection.execute(
                    "SELECT * FROM events WHERE recovery_of=?",
                    (event.recovery_of,),
                ).fetchone()
                if existing_recovery is not None:
                    recovered = self._event_from_row(existing_recovery)
                    self._append_observation(connection, recovered, "DUPLICATE_RECOVERY", event.observed_at)
                    return RecordResult(recovered, created=False, duplicate=True)
                if parent["status"] != EventStatus.OPEN.value:
                    raise StoreError("recovery parent is not open")
                connection.execute(
                    "UPDATE events SET status='RESOLVED', resolved_at=?, last_seen=? WHERE event_id=?",
                    (event.observed_at, event.observed_at, event.recovery_of),
                )
                parent_event = self._event_from_row(
                    connection.execute("SELECT * FROM events WHERE event_id=?", (event.recovery_of,)).fetchone()
                )
                self._append_observation(connection, parent_event, "RECOVERED", event.observed_at)
                self._insert_event(
                    connection,
                    event,
                    status=EventStatus.RESOLVED,
                    resolved_at=event.observed_at,
                    recovery_of=event.recovery_of,
                )
                recovered_row = connection.execute(
                    "SELECT * FROM events WHERE event_id=?",
                    (event.event_id,),
                ).fetchone()
                recovered = self._event_from_row(recovered_row)
                self._append_observation(connection, recovered, "RECOVERY_CREATED", event.observed_at)
                return RecordResult(recovered, created=True, duplicate=False, recovered_event_created=True)

            existing = connection.execute(
                "SELECT * FROM events WHERE fingerprint=? AND status='OPEN'",
                (event.fingerprint,),
            ).fetchone()
            if existing is not None:
                current = self._event_from_row(existing)
                now = max(current.last_seen, event.observed_at)
                severity = max(
                    (current.severity, event.severity),
                    key=lambda item: {"LOW": 0, "MEDIUM": 1, "HIGH": 2}[item.value],
                )
                action_scope = max(
                    (current.action_scope, event.action_scope),
                    key=lambda item: {
                        "NONE": 0,
                        "READ": 1,
                        "STATUS": 2,
                        "RETRY_SAFE": 3,
                        "INTERNAL_REVERSIBLE": 4,
                        "EXTERNAL_COMMUNICATION": 5,
                        "HIGH_RISK": 6,
                    }[item.value],
                )
                severity_escalated = severity != current.severity
                escalation_signal = (
                    event.escalation_level > current.escalation_level
                    or severity_escalated
                    or action_scope != current.action_scope
                    or (event.action_requested and not current.action_requested)
                    or (event.notification_requested and not current.notification_requested)
                    or (event.urgent and not current.urgent)
                )
                next_escalation_level = max(current.escalation_level, event.escalation_level)
                if escalation_signal and next_escalation_level <= current.escalation_level:
                    next_escalation_level = min(100, current.escalation_level + 1)
                connection.execute(
                    """UPDATE events SET
                        last_seen=?, severity=?, action_scope=?, action_requested=?,
                        notification_requested=?, urgent=?, escalation_level=?,
                        occurrence_count=occurrence_count+1
                       WHERE event_id=?""",
                    (
                        now,
                        severity.value,
                        action_scope.value,
                        int(current.action_requested or event.action_requested),
                        int(current.notification_requested or event.notification_requested),
                        int(current.urgent or event.urgent),
                        next_escalation_level,
                        current.event_id,
                    ),
                )
                updated = self._event_from_row(
                    connection.execute("SELECT * FROM events WHERE event_id=?", (current.event_id,)).fetchone()
                )
                self._append_observation(
                    connection,
                    updated,
                    (
                        "SEVERITY_ESCALATION"
                        if severity_escalated
                        else "DECISION_ESCALATION" if escalation_signal else "DUPLICATE"
                    ),
                    event.observed_at,
                )
                return RecordResult(updated, created=False, duplicate=True)

            expired_on_ingest = event.expires_at is not None and event.expires_at <= event.observed_at
            if expired_on_ingest:
                prior_expired = connection.execute(
                    """SELECT * FROM events
                       WHERE fingerprint=? AND status='EXPIRED' AND expires_at=?
                       ORDER BY expired_at DESC LIMIT 1""",
                    (event.fingerprint, event.expires_at),
                ).fetchone()
                if prior_expired is not None:
                    prior = self._event_from_row(prior_expired)
                    self._append_observation(connection, prior, "DUPLICATE_EXPIRED", event.observed_at)
                    return RecordResult(prior, created=False, duplicate=True)
            status = EventStatus.EXPIRED if expired_on_ingest else EventStatus.OPEN
            self._insert_event(
                connection,
                event,
                status=status,
                expired_at=event.observed_at if expired_on_ingest else None,
            )
            stored_row = connection.execute(
                "SELECT * FROM events WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
            stored = self._event_from_row(stored_row)
            self._append_observation(
                connection,
                stored,
                "EXPIRED_AT_INGEST" if expired_on_ingest else "FIRST_SEEN",
            )
            return RecordResult(stored, created=True, duplicate=False)

    def get_event(self, event_id: str) -> Event | None:
        with self.reader() as connection:
            row = connection.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        return self._event_from_row(row) if row else None

    def open_event_by_fingerprint(self, fingerprint: str) -> Event | None:
        with self.reader() as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE fingerprint=? AND status='OPEN'",
                (fingerprint,),
            ).fetchone()
        return self._event_from_row(row) if row else None

    def list_events(self) -> list[Event]:
        with self.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM events ORDER BY first_seen, event_id"
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def expire_due(self, now: str | datetime) -> int:
        timestamp = utc_iso(now)
        with self.transaction() as connection:
            return self._expire_due_in_transaction(connection, timestamp)

    def append_decision(self, decision: Decision) -> None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE event_id=?", (decision.event_id,)
            ).fetchone()
            if row is None:
                raise StoreError("decision event does not exist")
            event = self._event_from_row(row)
            if event.policy_version != decision.policy_version:
                raise StoreError("decision policy version mismatch")
            expected = PolicyEvaluator().evaluate(event, now=decision.evaluated_at)
            if (
                expected.high_level != decision.high_level
                or expected.action_decision != decision.action_decision
                or expected.notification_decision != decision.notification_decision
                or expected.risk != decision.risk
                or expected.reason_code != decision.reason_code
            ):
                raise StoreError("decision does not match deterministic policy")
            connection.execute(
                """INSERT INTO decisions(
                    decision_id,event_id,evaluated_at,high_level,action_decision,
                    notification_decision,risk,reason_code,policy_version
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    decision.decision_id,
                    decision.event_id,
                    decision.evaluated_at,
                    decision.high_level.value,
                    decision.action_decision.value,
                    decision.notification_decision.value,
                    decision.risk.value,
                    decision.reason_code,
                    decision.policy_version,
                ),
            )

    def list_decisions(self, event_id: str | None = None) -> list[dict[str, str]]:
        query = "SELECT * FROM decisions"
        values: tuple[str, ...] = ()
        if event_id:
            query += " WHERE event_id=?"
            values = (event_id,)
        query += " ORDER BY evaluated_at, decision_id"
        with self.reader() as connection:
            rows = connection.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    def add_notification_candidate(
        self,
        event: Event,
        decision: Decision,
        *,
        created_at: str | datetime | None = None,
    ) -> tuple[str, bool]:
        if decision.notification_decision == NotificationDecision.NONE:
            raise StoreError("notification candidate requires a notification decision")
        if decision.event_id != event.event_id or decision.policy_version != event.policy_version:
            raise StoreError("notification candidate decision does not match event")
        if event.policy_version != self.policy_version:
            raise StoreError("notification candidate policy version mismatch")
        if event.status != EventStatus.OPEN and not (
            event.status == EventStatus.RESOLVED and event.recovery_of is not None
        ):
            raise StoreError("notification candidate requires an open or recovery event")
        candidate_id = "ncan_" + uuid.uuid4().hex
        timestamp = utc_iso(created_at or decision.evaluated_at)
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO notification_candidates(
                    candidate_id,event_id,fingerprint,notification_decision,
                    severity,escalation_level,created_at,policy_version
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    candidate_id,
                    event.event_id,
                    event.fingerprint,
                    decision.notification_decision.value,
                    event.severity.value,
                    event.escalation_level,
                    timestamp,
                    decision.policy_version,
                ),
            )
            created = cursor.rowcount == 1
            if not created:
                row = connection.execute(
                    """SELECT candidate_id FROM notification_candidates
                       WHERE event_id=? AND notification_decision=? AND escalation_level=? AND policy_version=?""",
                    (
                        event.event_id,
                        decision.notification_decision.value,
                        event.escalation_level,
                        decision.policy_version,
                    ),
                ).fetchone()
                candidate_id = row["candidate_id"]
            return candidate_id, created

    def notification_candidate_count(self) -> int:
        with self.reader() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM notification_candidates").fetchone()
        return int(row["n"])

    def append_notification_transition(
        self,
        candidate_id: str,
        state: NotificationState,
        *,
        occurred_at: str | datetime | None = None,
        detail_code: str = "none",
    ) -> NotificationAttempt:
        state = NotificationState(state)
        timestamp = utc_iso(occurred_at)
        with self.transaction() as connection:
            candidate = connection.execute(
                "SELECT * FROM notification_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            if candidate is None:
                raise StoreError("notification candidate does not exist")
            latest = connection.execute(
                """SELECT * FROM notification_transitions WHERE candidate_id=?
                   ORDER BY attempt_number DESC, rowid DESC LIMIT 1""",
                (candidate_id,),
            ).fetchone()
            if latest is None:
                if state not in {NotificationState.PENDING, NotificationState.SUPPRESSED}:
                    raise StoreError("first notification state must be pending or suppressed")
                attempt_number = 1
            elif latest["state"] == NotificationState.PENDING.value:
                if state not in {NotificationState.SENT, NotificationState.FAILED}:
                    raise StoreError("pending notification must finish as sent or failed")
                attempt_number = int(latest["attempt_number"])
            elif latest["state"] in {NotificationState.FAILED.value, NotificationState.SUPPRESSED.value}:
                if state != NotificationState.PENDING:
                    raise StoreError("retry must begin as pending")
                attempt_number = int(latest["attempt_number"]) + 1
            else:
                raise StoreError("sent notification is terminal")
            connection.execute(
                """INSERT INTO notification_transitions(
                    transition_id,candidate_id,attempt_number,state,occurred_at,detail_code
                ) VALUES (?,?,?,?,?,?)""",
                (
                    "ntr_" + uuid.uuid4().hex,
                    candidate_id,
                    attempt_number,
                    state.value,
                    timestamp,
                    detail_code,
                ),
            )
            return NotificationAttempt(
                candidate_id=candidate_id,
                attempt_number=attempt_number,
                state=state,
                notification_decision=candidate["notification_decision"],
                occurred_at=timestamp,
                fingerprint=candidate["fingerprint"],
                severity=candidate["severity"],
                escalation_level=candidate["escalation_level"],
                detail_code=detail_code,
            )

    def notification_history(self, *, since: str | datetime | None = None) -> list[NotificationAttempt]:
        query = """SELECT t.*, c.fingerprint, c.severity, c.escalation_level,
                          c.notification_decision
                   FROM notification_transitions t
                   JOIN notification_candidates c USING(candidate_id)"""
        values: tuple[str, ...] = ()
        if since is not None:
            query += " WHERE t.occurred_at>=?"
            values = (utc_iso(since),)
        query += " ORDER BY t.occurred_at, t.transition_id"
        with self.reader() as connection:
            rows = connection.execute(query, values).fetchall()
        return [
            NotificationAttempt(
                candidate_id=row["candidate_id"],
                attempt_number=row["attempt_number"],
                state=row["state"],
                notification_decision=row["notification_decision"],
                occurred_at=row["occurred_at"],
                fingerprint=row["fingerprint"],
                severity=row["severity"],
                escalation_level=row["escalation_level"],
                detail_code=row["detail_code"],
            )
            for row in rows
        ]

    def notification_budget_history(self, *, since: str | datetime | None = None) -> list[NotificationAttempt]:
        """Include undelivered candidates as reservations without implying delivery."""
        query = """SELECT c.* FROM notification_candidates c
                   WHERE NOT EXISTS (
                     SELECT 1 FROM notification_transitions t WHERE t.candidate_id=c.candidate_id
                   )"""
        values: tuple[str, ...] = ()
        if since is not None:
            query += " AND c.created_at>=?"
            values = (utc_iso(since),)
        query += " ORDER BY c.created_at, c.candidate_id"
        with self.reader() as connection:
            rows = connection.execute(query, values).fetchall()
        untransitioned = [
            NotificationAttempt(
                candidate_id=row["candidate_id"],
                attempt_number=1,
                state=NotificationState.PENDING,
                notification_decision=row["notification_decision"],
                occurred_at=row["created_at"],
                fingerprint=row["fingerprint"],
                severity=row["severity"],
                escalation_level=row["escalation_level"],
                detail_code="candidate_reserved_no_delivery",
            )
            for row in rows
        ]
        return self.notification_history(since=since) + untransitioned

    def add_action_candidate(
        self,
        event: Event,
        decision: Decision,
        action_type: str,
        *,
        created_at: str | datetime | None = None,
    ) -> tuple[ActionAttempt, bool]:
        if event.status != EventStatus.OPEN:
            raise StoreError("action candidate requires an open event")
        if event.policy_version != self.policy_version:
            raise StoreError("action candidate policy version mismatch")
        if (
            decision.event_id != event.event_id
            or decision.policy_version != event.policy_version
            or decision.action_decision != ActionDecision.ACT
            or decision.risk.value != "LOW"
            or not event.action_requested
            or event.action_scope not in {ActionScope.READ, ActionScope.STATUS, ActionScope.RETRY_SAFE}
        ):
            raise StoreError("action candidate is not authorized by a low-risk policy decision")
        timestamp = utc_iso(created_at or event.observed_at)
        action_hash = hashlib.sha256(
            f"{event.event_id}:{action_type}:{event.policy_version}".encode("utf-8")
        ).hexdigest()
        action_id = "act_" + action_hash[:32]
        idempotency_key = action_hash
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO action_candidates(
                    action_id,event_id,idempotency_key,action_type,created_at,policy_version
                ) VALUES (?,?,?,?,?,?)""",
                (action_id, event.event_id, idempotency_key, action_type, timestamp, event.policy_version),
            )
            created = cursor.rowcount == 1
            if created:
                attempt = ActionAttempt(
                    action_id=action_id,
                    attempt_number=1,
                    state=ActionState.PLANNED,
                    occurred_at=timestamp,
                    event_id=event.event_id,
                    idempotency_key=idempotency_key,
                    action_type=action_type,
                    detail_code="shadow_candidate_only",
                )
                connection.execute(
                    """INSERT INTO action_transitions(
                        transition_id,action_id,attempt_number,state,occurred_at,event_id,
                        idempotency_key,action_type,detail_code
                    ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        "atr_" + uuid.uuid4().hex,
                        attempt.action_id,
                        attempt.attempt_number,
                        attempt.state.value,
                        attempt.occurred_at,
                        attempt.event_id,
                        attempt.idempotency_key,
                        attempt.action_type,
                        attempt.detail_code,
                    ),
                )
            else:
                row = connection.execute(
                    "SELECT * FROM action_candidates WHERE event_id=? AND action_type=? AND policy_version=?",
                    (event.event_id, action_type, event.policy_version),
                ).fetchone()
                if row is None:
                    raise StoreError("action candidate identity conflict")
                latest = connection.execute(
                    """SELECT * FROM action_transitions WHERE action_id=?
                       ORDER BY attempt_number DESC, rowid DESC LIMIT 1""",
                    (row["action_id"],),
                ).fetchone()
                if latest is None:
                    raise StoreError("action candidate has no audit transition")
                attempt = ActionAttempt(
                    action_id=latest["action_id"],
                    attempt_number=latest["attempt_number"],
                    state=latest["state"],
                    occurred_at=latest["occurred_at"],
                    event_id=latest["event_id"],
                    idempotency_key=latest["idempotency_key"],
                    action_type=latest["action_type"],
                    detail_code=latest["detail_code"],
                )
            return attempt, created

    def append_action_transition(self, attempt: ActionAttempt) -> None:
        with self.transaction() as connection:
            candidate = connection.execute(
                "SELECT * FROM action_candidates WHERE action_id=?", (attempt.action_id,)
            ).fetchone()
            if candidate is None:
                raise StoreError("action candidate does not exist")
            if (
                candidate["event_id"] != attempt.event_id
                or candidate["idempotency_key"] != attempt.idempotency_key
                or candidate["action_type"] != attempt.action_type
            ):
                raise StoreError("action identity changed during transition")
            latest = connection.execute(
                """SELECT * FROM action_transitions WHERE action_id=?
                   ORDER BY attempt_number DESC, rowid DESC LIMIT 1""",
                (attempt.action_id,),
            ).fetchone()
            if latest is None:
                if attempt.attempt_number != 1 or attempt.state != ActionState.PLANNED:
                    raise StoreError("first action transition must be planned")
            else:
                if attempt.attempt_number != int(latest["attempt_number"]):
                    raise StoreError("action attempts cannot be silently retried")
                previous = ActionState(latest["state"])
                allowed = {
                    ActionState.PLANNED: {ActionState.STARTED},
                    ActionState.STARTED: {
                        ActionState.SUCCEEDED,
                        ActionState.FAILED,
                        ActionState.UNKNOWN_AFTER_RESTART,
                    },
                    ActionState.SUCCEEDED: set(),
                    ActionState.FAILED: set(),
                    ActionState.UNKNOWN_AFTER_RESTART: set(),
                }
                if attempt.state not in allowed[previous]:
                    raise StoreError("invalid action state transition")
                if attempt.event_id != latest["event_id"] or attempt.idempotency_key != latest["idempotency_key"]:
                    raise StoreError("action identity changed during transition")
            connection.execute(
                """INSERT INTO action_transitions(
                    transition_id,action_id,attempt_number,state,occurred_at,event_id,
                    idempotency_key,action_type,detail_code
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    "atr_" + uuid.uuid4().hex,
                    attempt.action_id,
                    attempt.attempt_number,
                    attempt.state.value,
                    attempt.occurred_at,
                    attempt.event_id,
                    attempt.idempotency_key,
                    attempt.action_type,
                    attempt.detail_code,
                ),
            )

    def action_attempts(self, action_id: str) -> list[ActionAttempt]:
        with self.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM action_transitions WHERE action_id=? ORDER BY rowid",
                (action_id,),
            ).fetchall()
        return [
            ActionAttempt(
                action_id=row["action_id"],
                attempt_number=row["attempt_number"],
                state=row["state"],
                occurred_at=row["occurred_at"],
                event_id=row["event_id"],
                idempotency_key=row["idempotency_key"],
                action_type=row["action_type"],
                detail_code=row["detail_code"],
            )
            for row in rows
        ]

    def mark_interrupted_actions_unknown(self, *, now: str | datetime | None = None) -> int:
        timestamp = utc_iso(now)
        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT a.* FROM action_transitions a
                   JOIN (
                     SELECT action_id, MAX(rowid) AS last_row
                     FROM action_transitions GROUP BY action_id
                   ) latest ON latest.last_row=a.rowid
                   WHERE a.state='STARTED'"""
            ).fetchall()
            for row in rows:
                connection.execute(
                    """INSERT INTO action_transitions(
                        transition_id,action_id,attempt_number,state,occurred_at,event_id,
                        idempotency_key,action_type,detail_code
                    ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        "atr_" + uuid.uuid4().hex,
                        row["action_id"],
                        row["attempt_number"],
                        ActionState.UNKNOWN_AFTER_RESTART.value,
                        timestamp,
                        row["event_id"],
                        row["idempotency_key"],
                        row["action_type"],
                        "result_commit_missing",
                    ),
                )
            return len(rows)

    def increment_metric(self, metric_name: str, *, day: str, delta: int = 1, now: str | datetime | None = None) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", metric_name):
            raise ValueError("invalid metric name")
        try:
            parsed_day = datetime.strptime(day, "%Y-%m-%d").date().isoformat()
        except ValueError as error:
            raise ValueError("invalid metric day") from error
        if not isinstance(delta, int) or delta < 0:
            raise ValueError("metric delta must be non-negative")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO metric_aggregates(day,metric_name,value,updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(day,metric_name) DO UPDATE SET
                     value=value+excluded.value, updated_at=excluded.updated_at""",
                (parsed_day, metric_name, delta, utc_iso(now)),
            )

    def append_llm_outcome(
        self,
        event_id: str,
        stage: str,
        outcome_code: str,
        *,
        calls: int,
        occurred_at: str | datetime | None = None,
    ) -> None:
        allowed_stages = {"NEXT_ACTION_CANDIDATE", "SEMANTIC_RANKING", "SHORT_SUMMARY"}
        if stage not in allowed_stages:
            raise StoreError("unsupported LLM stage")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", outcome_code):
            raise StoreError("invalid LLM outcome code")
        if isinstance(calls, bool) or calls not in {0, 1}:
            raise StoreError("invalid LLM call count")
        with self.transaction() as connection:
            event = connection.execute(
                "SELECT policy_version FROM events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if event is None:
                raise StoreError("LLM outcome event does not exist")
            connection.execute(
                """INSERT INTO llm_outcomes(
                    outcome_id,event_id,stage,outcome_code,calls,occurred_at,policy_version
                ) VALUES (?,?,?,?,?,?,?)""",
                (
                    "llm_" + uuid.uuid4().hex,
                    event_id,
                    stage,
                    outcome_code,
                    calls,
                    utc_iso(occurred_at),
                    event["policy_version"],
                ),
            )

    def list_llm_outcomes(self, event_id: str | None = None) -> list[dict[str, str | int]]:
        query = "SELECT * FROM llm_outcomes"
        values: tuple[str, ...] = ()
        if event_id is not None:
            query += " WHERE event_id=?"
            values = (event_id,)
        query += " ORDER BY occurred_at, outcome_id"
        with self.reader() as connection:
            rows = connection.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    def metric_value(self, metric_name: str, day: str) -> int:
        with self.reader() as connection:
            row = connection.execute(
                "SELECT value FROM metric_aggregates WHERE metric_name=? AND day=?",
                (metric_name, day),
            ).fetchone()
        return int(row["value"]) if row else 0

    def prune_expired(self, *, now: str | datetime) -> dict[str, int]:
        timestamp = datetime.fromisoformat(utc_iso(now).replace("Z", "+00:00"))
        event_cutoff = utc_iso(timestamp - timedelta(days=RETENTION_RESOLVED_DAYS))
        metric_cutoff = (timestamp - timedelta(days=RETENTION_METRICS_DAYS)).date().isoformat()
        with self.transaction() as connection:
            connection.execute(
                "UPDATE maintenance_control SET retention_prune=1 WHERE singleton=1"
            )
            events = connection.execute(
                """DELETE FROM events
                   WHERE (status='RESOLVED' AND resolved_at<=?)
                      OR (status='EXPIRED' AND expired_at<=?)""",
                (event_cutoff, event_cutoff),
            ).rowcount
            metrics = connection.execute(
                "DELETE FROM metric_aggregates WHERE day<?",
                (metric_cutoff,),
            ).rowcount
            connection.execute(
                "UPDATE maintenance_control SET retention_prune=0 WHERE singleton=1"
            )
            return {"events_and_audit": int(events), "metric_days": int(metrics)}
