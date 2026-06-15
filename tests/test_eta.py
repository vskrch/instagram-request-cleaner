from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ig_request_cleaner.db import Store
from ig_request_cleaner.importer import PendingRequestCandidate
from ig_request_cleaner.pacing import DEFAULT_SETTINGS, estimate_eta_seconds, _format_duration


class FormatDurationTests(unittest.TestCase):
    def test_done(self) -> None:
        self.assertEqual(_format_duration(0), "Done")

    def test_negative(self) -> None:
        self.assertEqual(_format_duration(-10), "Done")

    def test_seconds_only(self) -> None:
        self.assertEqual(_format_duration(30), "30s")

    def test_minutes_only(self) -> None:
        self.assertEqual(_format_duration(120), "2m")

    def test_minutes_and_seconds(self) -> None:
        self.assertEqual(_format_duration(90), "1m 30s")

    def test_hours_and_minutes(self) -> None:
        self.assertEqual(_format_duration(5400), "1h 30m")

    def test_large_duration(self) -> None:
        self.assertEqual(_format_duration(36610), "10h 10m")


class EstimateETATests(unittest.TestCase):
    def test_zero_remaining(self) -> None:
        result = estimate_eta_seconds(DEFAULT_SETTINGS, [], 0)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(result["estimated_seconds"], 0)
        self.assertEqual(result["estimated_human"], "Done")
        self.assertTrue(result["at_current_pace"])

    def test_no_history_uses_settings_midpoint(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        result = estimate_eta_seconds(DEFAULT_SETTINGS, [], 10, now=now)
        # Default min=120, max=300, midpoint=210
        # 10 * 210 = 2100 seconds base
        # break_every=20, so 0 full breaks for 10 items
        self.assertEqual(result["remaining"], 10)
        self.assertEqual(result["avg_cancellation_interval_seconds"], 210)
        self.assertEqual(result["estimated_seconds"], 10 * 210)
        self.assertFalse(result["at_current_pace"])

    def test_no_history_with_breaks(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        # 24 remaining, break_every=20, break_minutes=15
        # 24 // 20 = 1 break * 15min * 60 = 900 extra
        result = estimate_eta_seconds(DEFAULT_SETTINGS, [], 24, now=now)
        expected_base = 24 * 210  # 5040
        expected_breaks = 1 * 15 * 60  # 900
        self.assertEqual(result["estimated_seconds"], expected_base + expected_breaks)
        self.assertEqual(result["remaining"], 24)

    def test_with_history_uses_actual_avg(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        # 3 actions spread over 30 minutes = avg 900s between them
        actions = [
            (now - timedelta(minutes=30)).isoformat(),
            (now - timedelta(minutes=15)).isoformat(),
            now.isoformat(),
        ]
        result = estimate_eta_seconds(DEFAULT_SETTINGS, actions, 5, now=now)
        # span = 30min = 1800s, 2 intervals, avg = 900s
        self.assertEqual(result["avg_cancellation_interval_seconds"], 900)
        self.assertEqual(result["remaining"], 5)
        self.assertTrue(result["at_current_pace"])

    def test_with_history_includes_breaks(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        actions = [
            (now - timedelta(minutes=20)).isoformat(),
            (now - timedelta(minutes=10)).isoformat(),
            now.isoformat(),
        ]
        # avg interval = 600s (10 min), 14 remaining
        # break_every=20 => 0 full breaks (14 < 20)
        result = estimate_eta_seconds(DEFAULT_SETTINGS, actions, 14, now=now)
        self.assertEqual(result["avg_cancellation_interval_seconds"], 600)
        expected_base = 14 * 600
        expected_breaks = 0
        self.assertEqual(result["estimated_seconds"], expected_base + expected_breaks)

    def test_single_action_uses_settings_midpoint(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        actions = [now.isoformat()]
        result = estimate_eta_seconds(DEFAULT_SETTINGS, actions, 5, now=now)
        # With only 1 action, fallback to midpoint = 210
        self.assertEqual(result["avg_cancellation_interval_seconds"], 210)
        self.assertEqual(result["remaining"], 5)

    def test_completed_today_count(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        actions = [
            (now - timedelta(hours=2)).isoformat(),
            (now - timedelta(minutes=30)).isoformat(),
        ]
        result = estimate_eta_seconds(DEFAULT_SETTINGS, actions, 3, now=now)
        self.assertEqual(result["completed_today"], 2)
        self.assertEqual(result["completed_total"], 2)

    def test_handles_invalid_action_times(self) -> None:
        actions = ["not-a-date", "also-bad", ""]
        result = estimate_eta_seconds(DEFAULT_SETTINGS, actions, 5)
        self.assertEqual(result["remaining"], 5)
        self.assertFalse(result["at_current_pace"])


class StoreETATests(unittest.TestCase):
    def test_summary_includes_eta(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            store.import_candidates(
                [
                    PendingRequestCandidate(username="alpha"),
                    PendingRequestCandidate(username="beta"),
                    PendingRequestCandidate(username="gamma"),
                ],
                source_path="test.json",
            )
            summary = store.summary()

            self.assertIn("eta", summary)
            self.assertEqual(summary["eta"]["remaining"], 3)
            # No cancellation history yet — uses settings-based estimate
            self.assertFalse(summary["eta"]["at_current_pace"])
            self.assertGreater(summary["eta"]["estimated_seconds"], 0)

    def test_eta_updates_after_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            store.import_candidates(
                [
                    PendingRequestCandidate(username="alpha"),
                    PendingRequestCandidate(username="beta"),
                ],
                source_path="test.json",
            )
            store.mark_request("alpha", "cancelled")
            summary = store.summary()

            self.assertEqual(summary["eta"]["remaining"], 1)
            self.assertEqual(summary["eta"]["completed_total"], 1)
            self.assertEqual(summary["counts"]["pending"], 1)

    def test_eta_shows_done_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            store.import_candidates([PendingRequestCandidate(username="alpha")], source_path="t.json")
            store.mark_request("alpha", "cancelled")
            summary = store.summary()

            self.assertEqual(summary["eta"]["remaining"], 0)
            self.assertEqual(summary["eta"]["estimated_human"], "Done")


if __name__ == "__main__":
    unittest.main()
