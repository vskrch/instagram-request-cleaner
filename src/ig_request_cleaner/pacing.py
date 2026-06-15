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


def _setting_int(settings: dict[str, str], key: str) -> int:
    try:
        return max(0, int(settings.get(key, DEFAULT_SETTINGS[key])))
    except (TypeError, ValueError):
        return int(DEFAULT_SETTINGS[key])
