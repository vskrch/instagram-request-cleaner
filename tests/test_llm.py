from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ig_request_cleaner.llm import LLMAdvisor, LLMConfig, local_advice


class LLMTests(unittest.TestCase):
    def test_local_advice_when_no_provider(self) -> None:
        result = LLMAdvisor(LLMConfig(provider="local", base_url="", model="", api_key="")).advise(
            summary={
                "counts": {"pending": 1, "cancelled": 2},
                "pacing": {"allowed": True},
            },
            current={"username": "alpha"},
            queue=[],
        )

        self.assertFalse(result.used_remote_llm)
        self.assertIn("@alpha", result.text)

    def test_nim_config_from_env(self) -> None:
        env = {
            "IGRC_LLM_PROVIDER": "nim",
            "NVIDIA_API_KEY": "nvapi-test",
            "IGRC_LLM_MODEL": "nvidia/test-model",
        }
        with patch.dict(os.environ, env, clear=True):
            config = LLMConfig.from_env()

        self.assertEqual(config.provider, "nvidia-nim")
        self.assertEqual(config.base_url, "https://integrate.api.nvidia.com/v1")
        self.assertEqual(config.api_key, "nvapi-test")
        self.assertEqual(config.model, "nvidia/test-model")

    def test_scrubs_usernames_by_default(self) -> None:
        advisor = LLMAdvisor(
            LLMConfig(
                provider="local",
                base_url="",
                model="",
                api_key="",
                share_usernames=False,
            )
        )

        payload = advisor._advice_payload(
            summary={"total": 1, "counts": {}, "pacing": {}},
            current={"username": "private_user", "status": "pending"},
            queue=[{"username": "private_user", "status": "pending"}],
        )

        self.assertEqual(payload["current"]["username"], "user_000")
        self.assertEqual(payload["queue_preview"][0]["username"], "user_001")

    def test_local_advice_blocks_on_cooldown(self) -> None:
        advice = local_advice(
            summary={
                "counts": {"pending": 1},
                "pacing": {"allowed": False, "next_allowed_at": "later"},
            },
            current={"username": "alpha"},
            queue=[],
        )

        self.assertIn("Wait until later", advice)


if __name__ == "__main__":
    unittest.main()
