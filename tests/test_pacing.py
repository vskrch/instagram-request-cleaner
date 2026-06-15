from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from ig_request_cleaner.pacing import DEFAULT_SETTINGS, evaluate_pacing


class PacingTests(unittest.TestCase):
    def test_ready_when_under_limits(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        decision = evaluate_pacing(DEFAULT_SETTINGS, [], now=now)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ready")

    def test_cooldown_blocks_until_next_allowed(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        settings = dict(DEFAULT_SETTINGS)
        settings["next_allowed_at"] = (now + timedelta(minutes=5)).isoformat()

        decision = evaluate_pacing(settings, [], now=now)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "cooldown")

    def test_hourly_limit_blocks(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        settings = dict(DEFAULT_SETTINGS)
        settings["max_actions_per_hour"] = "2"
        actions = [
            (now - timedelta(minutes=10)).isoformat(),
            (now - timedelta(minutes=20)).isoformat(),
        ]

        decision = evaluate_pacing(settings, actions, now=now)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "hourly_limit")


if __name__ == "__main__":
    unittest.main()
