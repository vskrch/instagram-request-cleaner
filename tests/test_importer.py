from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from ig_request_cleaner import importer
from ig_request_cleaner.importer import ImportErrorWithContext, load_candidates_from_text, normalize_username


class ImporterTests(unittest.TestCase):
    def test_instagram_export_shape(self) -> None:
        payload = {
            "relationships_follow_requests_sent": [
                {
                    "title": "person.one",
                    "string_list_data": [
                        {
                            "href": "https://www.instagram.com/person.one/",
                            "value": "person.one",
                            "timestamp": 1717200000,
                        }
                    ],
                }
            ]
        }

        candidates = load_candidates_from_text("pending.json", json.dumps(payload))

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].username, "person.one")
        self.assertEqual(candidates[0].profile_url, "https://www.instagram.com/person.one/")
        self.assertTrue(candidates[0].requested_at)

    def test_plain_json_list_dedupes(self) -> None:
        candidates = load_candidates_from_text(
            "pending.json",
            json.dumps(["@person_one", "https://instagram.com/person_two/", "person_one"]),
        )

        self.assertEqual([candidate.username for candidate in candidates], ["person_one", "person_two"])

    def test_csv_with_header(self) -> None:
        candidates = load_candidates_from_text(
            "pending.csv",
            "username,requested_at\nalpha,2024-01-01T00:00:00Z\n",
        )

        self.assertEqual(candidates[0].username, "alpha")
        self.assertEqual(candidates[0].profile_url, "https://www.instagram.com/alpha/")

    def test_normalize_username_rejects_non_profile_paths(self) -> None:
        self.assertIsNone(normalize_username("https://instagram.com/reel/abc"))
        self.assertEqual(normalize_username("@valid.name"), "valid.name")

    def test_large_text_import_is_rejected(self) -> None:
        with patch.object(importer, "MAX_IMPORT_BYTES", 8):
            with self.assertRaisesRegex(ImportErrorWithContext, "too large"):
                load_candidates_from_text("pending.txt", "alpha\nbeta\n")


if __name__ == "__main__":
    unittest.main()
