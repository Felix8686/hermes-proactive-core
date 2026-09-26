"""Fail-closed kill switches for the future integration boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .model import ActionDecision, Decision, NotificationDecision, Risk


class SwitchConfigurationError(ValueError):
    pass


def _read_bool(environment: dict[str, str], key: str) -> bool:
    value = environment.get(key, "false").strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise SwitchConfigurationError(f"invalid value for {key}")


@dataclass(frozen=True, slots=True)
class KillSwitches:
    proactive_enabled: bool = False
    notifications_enabled: bool = False
    actions_enabled: bool = False

    @classmethod
    def from_env(cls, environment: dict[str, str] | None = None) -> "KillSwitches":
        env = dict(os.environ if environment is None else environment)
        return cls(
            proactive_enabled=_read_bool(env, "PROACTIVE_ENABLED"),
            notifications_enabled=_read_bool(env, "PROACTIVE_NOTIFICATIONS_ENABLED"),
            actions_enabled=_read_bool(env, "PROACTIVE_ACTIONS_ENABLED"),
        )


@dataclass(frozen=True, slots=True)
class GateResult:
    core_running: bool
    action_execution_allowed: bool
    notification_delivery_allowed: bool
    reason_code: str


class ExecutionGate:
    def __init__(self, switches: KillSwitches) -> None:
        self.switches = switches

    def evaluate(self, decision: Decision) -> GateResult:
        if not self.switches.proactive_enabled:
            return GateResult(False, False, False, "proactive_disabled")
        action_allowed = (
            self.switches.actions_enabled
            and decision.action_decision == ActionDecision.ACT
            and decision.risk == Risk.LOW
        )
        notification_allowed = (
            self.switches.notifications_enabled
            and decision.notification_decision != NotificationDecision.NONE
        )
        return GateResult(
            True,
            action_allowed,
            notification_allowed,
            "enabled" if action_allowed or notification_allowed else "subsystems_disabled",
        )
