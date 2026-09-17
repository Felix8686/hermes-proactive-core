"""Controlled text renderer. It never exposes Event data or audit JSON."""

from __future__ import annotations

from .model import ActionDecision, Decision, Event, NotificationDecision


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
