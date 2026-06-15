from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable


DEFAULT_SETTINGS: dict[str, str] = {
    "min_interval_seconds": "420",
    "max_interval_seconds": "960",
    "max_actions_per_hour": "8",
    "max_actions_per_day": "60",
    "session_break_every": "12",
    "session_break_minutes": "45",
    "auto_minor_decisions": "1",
    "recent_request_snooze_days": "14",
    "next_allowed_at": "",
}


@dataclass(frozen=True)
class PacingDecision:
    allowed: bool
    reason: str
    next_allowed_at: str | None
    actions_last_hour: int
    actions_today: int


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def evaluate_pacing(
    settings: dict[str, str],
    action_times: Iterable[str],
    *,
    now: datetime | None = None,
) -> PacingDecision:
    current = now or now_utc()
    parsed_actions = [dt for value in action_times if (dt := parse_dt(value))]
    parsed_actions.sort()

    last_hour_cutoff = current - timedelta(hours=1)
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    actions_last_hour = sum(1 for dt in parsed_actions if dt >= last_hour_cutoff)
    actions_today = sum(1 for dt in parsed_actions if dt >= day_start)

    next_allowed_setting = parse_dt(settings.get("next_allowed_at"))
    if next_allowed_setting and next_allowed_setting > current:
        return PacingDecision(
            allowed=False,
            reason="cooldown",
            next_allowed_at=next_allowed_setting.isoformat(),
            actions_last_hour=actions_last_hour,
            actions_today=actions_today,
        )

    max_hour = _setting_int(settings, "max_actions_per_hour")
    if actions_last_hour >= max_hour:
        next_allowed = min(dt for dt in parsed_actions if dt >= last_hour_cutoff) + timedelta(hours=1)
        return PacingDecision(
            allowed=False,
            reason="hourly_limit",
            next_allowed_at=next_allowed.isoformat(),
            actions_last_hour=actions_last_hour,
            actions_today=actions_today,
        )

    max_day = _setting_int(settings, "max_actions_per_day")
    if actions_today >= max_day:
        next_allowed = day_start + timedelta(days=1)
        return PacingDecision(
            allowed=False,
            reason="daily_limit",
            next_allowed_at=next_allowed.isoformat(),
            actions_last_hour=actions_last_hour,
            actions_today=actions_today,
        )

    return PacingDecision(
        allowed=True,
        reason="ready",
        next_allowed_at=None,
        actions_last_hour=actions_last_hour,
        actions_today=actions_today,
    )


def next_cooldown_seconds(settings: dict[str, str], completed_action_count: int) -> int:
    min_seconds = _setting_int(settings, "min_interval_seconds")
    max_seconds = _setting_int(settings, "max_interval_seconds")
    if max_seconds < min_seconds:
        max_seconds = min_seconds
    cooldown = random.SystemRandom().randint(min_seconds, max_seconds)

    break_every = _setting_int(settings, "session_break_every")
    if break_every > 0 and completed_action_count > 0 and completed_action_count % break_every == 0:
        cooldown += _setting_int(settings, "session_break_minutes") * 60
    return cooldown


def estimate_eta_seconds(
    settings: dict[str, str],
    action_times: Iterable[str],
    remaining: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Estimate time to clear remaining queue items.

    Returns a dict with:
      - avg_cancellation_interval_seconds: average seconds between recent cancellations
      - remaining: number of pending items
      - estimated_seconds: total estimated seconds to finish (0 if nothing left)
      - estimated_human: human-readable string like '3h 42m'
      - completed_today: how many done today
      - completed_total: total cancelled ever
      - at_current_pace: whether we can estimate (need at least 1 past action)
    """
    current = now or now_utc()
    parsed_actions = [dt for value in action_times if (dt := parse_dt(value))]
    parsed_actions.sort()

    remaining = max(0, remaining)
    if remaining == 0:
        return {
            "avg_cancellation_interval_seconds": 0,
            "remaining": 0,
            "estimated_seconds": 0,
            "estimated_human": "Done",
            "completed_today": 0,
            "completed_total": len(parsed_actions),
            "at_current_pace": True,
        }

    completed_total = len(parsed_actions)
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    completed_today = sum(1 for dt in parsed_actions if dt >= day_start)

    if completed_total == 0:
        # No history — use configured average of min/max interval
        min_sec = _setting_int(settings, "min_interval_seconds")
        max_sec = _setting_int(settings, "max_interval_seconds")
        avg_interval = (min_sec + max_sec) / 2
        break_minutes = _setting_int(settings, "session_break_minutes")
        break_every = _setting_int(settings, "session_break_every")
        estimated = remaining * avg_interval
        if break_every > 0:
            full_breaks = remaining // break_every
            estimated += full_breaks * break_minutes * 60
        return {
            "avg_cancellation_interval_seconds": int(avg_interval),
            "remaining": remaining,
            "estimated_seconds": int(estimated),
            "estimated_human": _format_duration(int(estimated)),
            "completed_today": completed_today,
            "completed_total": completed_total,
            "at_current_pace": False,
        }

    # Compute average interval between recent actions (up to last 20)
    recent = parsed_actions[-20:] if len(parsed_actions) > 20 else parsed_actions
    if len(recent) >= 2:
        span = (recent[-1] - recent[0]).total_seconds()
        avg_interval = span / (len(recent) - 1)
    else:
        # Only one action — use midpoint of configured range
        min_sec = _setting_int(settings, "min_interval_seconds")
        max_sec = _setting_int(settings, "max_interval_seconds")
        avg_interval = (min_sec + max_sec) / 2

    avg_interval = max(avg_interval, 1.0)  # avoid division by zero

    # Account for session breaks
    break_minutes = _setting_int(settings, "session_break_minutes")
    break_every = _setting_int(settings, "session_break_every")
    estimated = remaining * avg_interval
    if break_every > 0:
        # Estimate how many breaks will trigger for remaining items
        full_breaks = remaining // break_every
        estimated += full_breaks * break_minutes * 60

    return {
        "avg_cancellation_interval_seconds": int(avg_interval),
        "remaining": remaining,
        "estimated_seconds": int(estimated),
        "estimated_human": _format_duration(int(estimated)),
        "completed_today": completed_today,
        "completed_total": completed_total,
        "at_current_pace": True,
    }


def _format_duration(total_seconds: int) -> str:
    """Format seconds into human-readable string like '2h 15m' or '45m' or '30s'."""
    if total_seconds <= 0:
        return "Done"
    total_minutes = total_seconds // 60
    hours = total_minutes // 60
    minutes = total_minutes % 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {seconds}s" if seconds > 0 else f"{minutes}m"
    return f"{seconds}s"


def _setting_int(settings: dict[str, str], key: str) -> int:
    try:
        return max(0, int(settings.get(key, DEFAULT_SETTINGS[key])))
    except (TypeError, ValueError):
        return int(DEFAULT_SETTINGS[key])
