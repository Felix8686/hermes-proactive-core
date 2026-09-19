"""Controlled text renderer. It never exposes Event data or audit JSON."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from .model import ActionDecision, Decision, Event, NotificationDecision


_PHASE4_LABELS = {
    "cron.failure": "Cron任务异常",
    "cron.recovered": "Cron任务恢复",
    "openviking.unhealthy": "OpenViking异常",
    "openviking.recovered": "OpenViking恢复",
    "proactive.circuit_breaker_open": "主动巡检暂停保护",
}
_PHASE4_TEST_EVENT = "phase4.synthetic.delivery_test"
_PHASE4_TEST_MESSAGE = "【Proactive Core 测试】通知链路测试成功；这不是异常告警。"


class NotificationRenderer:
    def render(self, event: Event, decision: Decision, *, shadow: bool = True) -> str:
        if event.event_id != decision.event_id:
            raise ValueError("event and decision do not match")
        if decision.notification_decision == NotificationDecision.NONE:
            return ""
        if decision.notification_decision == NotificationDecision.URGENT:
            prefix = "【Shadow紧急候选】" if shadow else "【紧急】"
            return prefix + "检测到需要优先复核的系统状态；本阶段未发送消息。"
        if decision.action_decision == ActionDecision.ASK:
            prefix = "【Shadow审批候选】" if shadow else "【待确认】"
            return prefix + "该请求需要你确认；本阶段未向第三方发送内容。"
        if decision.action_decision == ActionDecision.ACT:
            prefix = "【Shadow通知候选】" if shadow else "【状态更新】"
            return prefix + "低风险动作仅作候选，本阶段未执行；检测到系统状态变化。"
        if "recover" in event.event_type.casefold():
            message = "检测到状态恢复。"
        else:
            message = "检测到需要关注的系统状态变化。"
        prefix = "【Shadow通知候选】" if shadow else "【状态更新】"
        return prefix + message + ("本阶段未发送消息。" if shadow else "")

    def render_phase4_aggregate(self, events: Iterable[Event]) -> str:
        """Render one short, fixed-template Phase 4 owner notification."""
        items = tuple(events)
        if not items:
            return ""
        if len(items) == 1 and items[0].event_type == _PHASE4_TEST_EVENT:
            return _PHASE4_TEST_MESSAGE

        counts = Counter(item.event_type for item in items)
        if any(event_type not in _PHASE4_LABELS for event_type in counts):
            raise ValueError("unsupported Phase 4 renderer event")
        summary = "；".join(
            f"{_PHASE4_LABELS[event_type]}{count}项"
            for event_type, count in sorted(counts.items())
        )
        return f"【主动巡检】{summary}。"
