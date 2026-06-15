from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ig_request_cleaner.browser_assist import open_next_profile, open_profile
from ig_request_cleaner.db import Store
from ig_request_cleaner.importer import PendingRequestCandidate


class BrowserAssistTests(unittest.TestCase):
    def test_open_next_profile_uses_browser_when_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            store.import_candidates([PendingRequestCandidate(username="alpha")], source_path="test")

            with patch("webbrowser.open_new_tab", return_value=True) as open_new_tab:
                result = open_next_profile(store)

        self.assertTrue(result.opened)
        self.assertEqual(result.username, "alpha")
        open_new_tab.assert_called_once_with("https://www.instagram.com/alpha/")

    def test_open_next_profile_respects_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "state.sqlite3")
            store.import_candidates(
                [
                    PendingRequestCandidate(username="alpha"),
                    PendingRequestCandidate(username="beta"),
                ],
                source_path="test",
            )
            store.mark_request("alpha", "cancelled")

            with patch("webbrowser.open_new_tab", return_value=True) as open_new_tab:
                result = open_next_profile(store)

        self.assertFalse(result.opened)
        self.assertEqual(result.reason, "cooldown")
        open_new_tab.assert_not_called()

    def test_browser_failure_is_controlled(self) -> None:
        with patch("webbrowser.open_new_tab", side_effect=Exception("boom")):
            result = open_profile({"username": "alpha"})

        self.assertFalse(result.opened)
        self.assertEqual(result.reason, "browser_open_failed")


if __name__ == "__main__":
    unittest.main()
