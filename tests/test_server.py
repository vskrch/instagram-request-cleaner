from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

from ig_request_cleaner.db import Store
from ig_request_cleaner.importer import PendingRequestCandidate
from ig_request_cleaner.server import _make_handler


class ServerTests(unittest.TestCase):
    def test_invalid_status_returns_400(self) -> None:
        with self._server_with_store() as base_url:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base_url}/api/items?status=bad", timeout=5)

        self.assertEqual(caught.exception.code, 400)

    def test_assist_step_applies_minor_decisions(self) -> None:
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

            with self._server_with_store(store) as base_url:
                payload = self._post_json(f"{base_url}/api/assist-step", {})

        self.assertEqual(payload["applied_minor_decisions"][0]["username"], "recent")
        self.assertEqual(payload["item"]["username"], "old")
        self.assertEqual(payload["decision"]["code"], "review_old")

    def _post_json(self, url: str, payload: dict) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _server_with_store(self, store: Store | None = None):
        case = self

        class ServerContext:
            def __enter__(self):
                self.temp_dir = tempfile.TemporaryDirectory()
                self.store = store or Store(Path(self.temp_dir.name) / "state.sqlite3")
                self.store.initialize()
                self.server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self.store))
                self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
                self.thread.start()
                host, port = self.server.server_address
                return f"http://{host}:{port}"

            def __exit__(self, exc_type, exc, tb):
                self.server.shutdown()
                self.server.server_close()
                self.thread.join(timeout=5)
                self.temp_dir.cleanup()
                case.assertFalse(self.thread.is_alive())

        return ServerContext()


if __name__ == "__main__":
    unittest.main()
