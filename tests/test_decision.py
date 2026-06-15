from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from ig_request_cleaner.decision import DecisionPolicy, decide_request, policy_from_settings


class DecisionTests(unittest.TestCase):
    def test_recent_request_is_auto_snoozed_when_minor_decisions_are_on(self) -> None:
        now = datetime(2026, 1, 20, tzinfo=UTC)
        item = {"status": "pending", "requested_at": (now - timedelta(days=2)).isoformat()}

        decision = decide_request(
            item,
            policy=DecisionPolicy(auto_minor_decisions=True, recent_request_snooze_days=14),
            now=now,
        )

        self.assertEqual(decision.code, "auto_snooze_recent")
        self.assertFalse(decision.requires_human)
        self.assertEqual(decision.suggested_status, "snoozed")

    def test_recent_request_requires_human_when_minor_decisions_are_off(self) -> None:
        now = datetime(2026, 1, 20, tzinfo=UTC)
        item = {"status": "pending", "requested_at": (now - timedelta(days=2)).isoformat()}

        decision = decide_request(
            item,
            policy=DecisionPolicy(auto_minor_decisions=False, recent_request_snooze_days=14),
            now=now,
        )

        self.assertEqual(decision.code, "review_recent")
        self.assertTrue(decision.requires_human)

    def test_old_request_is_major_human_decision(self) -> None:
        now = datetime(2026, 1, 20, tzinfo=UTC)
        item = {"status": "pending", "requested_at": (now - timedelta(days=100)).isoformat()}

        decision = decide_request(item, policy=DecisionPolicy(), now=now)

        self.assertEqual(decision.code, "review_very_old")
        self.assertTrue(decision.requires_human)

    def test_policy_from_settings_clamps_days(self) -> None:
        policy = policy_from_settings(
            {"auto_minor_decisions": "1", "recent_request_snooze_days": "999"}
        )

        self.assertTrue(policy.auto_minor_decisions)
        self.assertEqual(policy.recent_request_snooze_days, 90)


if __name__ == "__main__":
    unittest.main()
