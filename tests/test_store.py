from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ig_request_cleaner.db import Store
from ig_request_cleaner.importer import PendingRequestCandidate


class StoreTests(unittest.TestCase):
    def test_import_mark_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            stats = store.import_candidates(
                [
                    PendingRequestCandidate(
                        username="alpha",
                        profile_url="https://www.instagram.com/alpha/",
                        source="test",
                    )
                ],
                source_path="test.json",
            )

            self.assertEqual(stats["added"], 1)
            self.assertEqual(store.summary()["counts"]["pending"], 1)

            store.mark_request("alpha", "cancelled")
            summary = store.summary()

            self.assertEqual(summary["counts"]["cancelled"], 1)
            self.assertIn("alpha", store.export_csv())
            store.mark_request("alpha", "cancelled")
            self.assertEqual(store.action_count(), 1)

    def test_snoozed_item_returns_after_time_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            store.import_candidates([PendingRequestCandidate(username="alpha")], source_path="test.json")
            store.mark_request("alpha", "snoozed", snooze_minutes=60)

            self.assertIsNone(store.next_pending())

    def test_import_fills_missing_profile_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            store.import_candidates([PendingRequestCandidate(username="alpha")], source_path="test.json")

            item = store.next_pending()

            self.assertEqual(item["profile_url"], "https://www.instagram.com/alpha/")

    def test_pacing_blocks_direct_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            store.import_candidates(
                [PendingRequestCandidate(username="alpha"), PendingRequestCandidate(username="beta")],
                source_path="test.json",
            )
            store.update_settings({"min_interval_seconds": 60, "max_interval_seconds": 60})
            store.mark_request("alpha", "cancelled")

            with self.assertRaisesRegex(ValueError, "Action blocked by pacing"):
                store.mark_request("beta", "cancelled")

    def test_settings_are_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            settings = store.update_settings(
                {
                    "min_interval_seconds": 0,
                    "max_interval_seconds": 1,
                    "max_actions_per_hour": 0,
                    "max_actions_per_day": 9999,
                }
            )

            self.assertEqual(settings["min_interval_seconds"], "60")
            self.assertEqual(settings["max_interval_seconds"], "60")
            self.assertEqual(settings["max_actions_per_hour"], "1")
            self.assertEqual(settings["max_actions_per_day"], "500")

    def test_invalid_status_filter_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")

            with self.assertRaisesRegex(ValueError, "Invalid status filter"):
                store.list_requests(status="bad")

    def test_assist_step_auto_snoozes_recent_and_returns_old_item(self) -> None:
        now = datetime.now(tz=UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            store.import_candidates(
                [
                    PendingRequestCandidate(
                        username="recent",
                        requested_at=(now - timedelta(days=2)).isoformat(),
                    ),
                    PendingRequestCandidate(
                        username="old",
                        requested_at=(now - timedelta(days=45)).isoformat(),
                    ),
                ],
                source_path="test.json",
            )

            step = store.assist_step()

            self.assertEqual(step["applied_minor_decisions"][0]["username"], "recent")
            self.assertEqual(step["item"]["username"], "old")
            self.assertEqual(step["decision"]["code"], "review_old")
            self.assertEqual(store.summary()["counts"]["snoozed"], 1)

    def test_backup_names_do_not_collide(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            store.initialize()

            first = store.backup("same")
            second = store.backup("same")

            self.assertNotEqual(first, second)
            self.assertTrue(Path(first).exists())
            self.assertTrue(Path(second).exists())


if __name__ == "__main__":
    unittest.main()
