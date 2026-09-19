"""In-memory circuit breaker used by offline/shadow processing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from threading import RLock

from .model import utc_iso


_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    state: str
    consecutive_failures: int
    open_until: str | None
    last_error_code: str | None


class CircuitBreaker:
    def __init__(self, *, threshold: int = 3, reset_after_seconds: int = 300) -> None:
        if threshold < 1 or reset_after_seconds < 1:
            raise ValueError("circuit breaker bounds must be positive")
        self.threshold = threshold
        self.reset_after_seconds = reset_after_seconds
        self._failures = 0
        self._open_until: str | None = None
        self._half_open_probe = False
        self._last_error_code: str | None = None
        self._lock = RLock()

    def allow(self, *, now: str | datetime) -> bool:
        timestamp = utc_iso(now)
        with self._lock:
            if self._failures < self.threshold:
                return True
            if self._open_until is None:
                return False
            if timestamp < self._open_until:
                return False
            if self._half_open_probe:
                return False
            self._half_open_probe = True
            return True

    def record_failure(self, error_code: str, *, now: str | datetime) -> CircuitSnapshot:
        timestamp = datetime.fromisoformat(utc_iso(now).replace("Z", "+00:00"))
        with self._lock:
            self._failures += 1
            self._last_error_code = error_code if _ERROR_CODE.fullmatch(error_code) else "unknown_error"
            self._half_open_probe = False
            if self._failures >= self.threshold:
                self._open_until = utc_iso(timestamp + timedelta(seconds=self.reset_after_seconds))
            return self.snapshot()

    def record_success(self) -> CircuitSnapshot:
        with self._lock:
            self._failures = 0
            self._open_until = None
            self._half_open_probe = False
            self._last_error_code = None
            return self.snapshot()

    def snapshot(self) -> CircuitSnapshot:
        with self._lock:
            if self._failures < self.threshold:
                state = "CLOSED"
            elif self._half_open_probe:
                state = "HALF_OPEN"
            else:
                state = "OPEN"
            return CircuitSnapshot(state, self._failures, self._open_until, self._last_error_code)
