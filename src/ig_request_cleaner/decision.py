from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .pacing import now_utc, parse_dt


@dataclass(frozen=True)
class DecisionPolicy:
    auto_minor_decisions: bool = True
    recent_request_snooze_days: int = 14


@dataclass(frozen=True)
class QueueDecision:
    code: str
    reason: str
    requires_human: bool
    priority: int
    suggested_status: str | None = None
    snooze_until: str | None = None


def policy_from_settings(settings: dict[str, str]) -> DecisionPolicy:
    auto_minor = settings.get("auto_minor_decisions", "1").strip().lower()
    try:
        recent_days = int(settings.get("recent_request_snooze_days", "14"))
    except ValueError:
        recent_days = 14
    return DecisionPolicy(
        auto_minor_decisions=auto_minor in {"1", "true", "yes", "on"},
        recent_request_snooze_days=min(max(recent_days, 1), 90),
    )


def decide_request(
    item: dict[str, Any],
    *,
    policy: DecisionPolicy,
    now: datetime | None = None,
) -> QueueDecision:
    current = now or now_utc()
    status = str(item.get("status") or "")
    if status == "snoozed":
        snooze_until = parse_dt(item.get("snooze_until"))
        if snooze_until and snooze_until > current:
            return QueueDecision(
                code="wait_snoozed",
                reason=f"Snoozed until {snooze_until.isoformat()}.",
                requires_human=False,
                priority=900,
            )

    requested_at = parse_dt(item.get("requested_at"))
    if requested_at is None:
        return QueueDecision(
            code="review_unknown_age",
            reason="Request age is unknown, so a human should review it.",
            requires_human=True,
            priority=250,
        )

    age = current - requested_at
    recent_window = timedelta(days=policy.recent_request_snooze_days)
    if age < recent_window:
        snooze_until = requested_at + recent_window
        if snooze_until <= current:
            snooze_until = current + timedelta(days=1)
        if not policy.auto_minor_decisions:
            return QueueDecision(
                code="review_recent",
                reason=(
                    f"Request is only {max(0, age.days)} days old; automation is off, "
                    "so ask a human before deferring or reviewing."
                ),
                requires_human=True,
                priority=300,
            )
        return QueueDecision(
            code="auto_snooze_recent",
            reason=(
                f"Request is only {max(0, age.days)} days old; defer until "
                f"{snooze_until.date().isoformat()}."
            ),
            requires_human=False,
            priority=800,
            suggested_status="snoozed",
            snooze_until=snooze_until.isoformat(),
        )

    if age >= timedelta(days=90):
        return QueueDecision(
            code="review_very_old",
            reason="Request is more than 90 days old; prioritize manual review.",
            requires_human=True,
            priority=10,
        )
    if age >= timedelta(days=30):
        return QueueDecision(
            code="review_old",
            reason="Request is more than 30 days old; review next.",
            requires_human=True,
            priority=50,
        )
    return QueueDecision(
        code="review_ready",
        reason="Request is old enough for manual review.",
        requires_human=True,
        priority=100,
    )


def decision_sort_key(item: dict[str, Any], policy: DecisionPolicy) -> tuple[int, str, str]:
    decision = decide_request(item, policy=policy)
    requested_at = str(item.get("requested_at") or item.get("imported_at") or "")
    username = str(item.get("username") or "")
    return (decision.priority, requested_at, username.lower())
