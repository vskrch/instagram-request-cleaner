from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


LOCAL_PROVIDERS = {"", "off", "none", "local", "disabled"}
NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float = 18.0
    max_tokens: int = 500
    share_usernames: bool = False

    @property
    def enabled(self) -> bool:
        return self.provider not in LOCAL_PROVIDERS and bool(self.base_url and self.model)

    @classmethod
    def from_env(cls) -> "LLMConfig":
        provider = os.environ.get("IGRC_LLM_PROVIDER", "local").strip().lower()
        base_url = os.environ.get("IGRC_LLM_BASE_URL", "").strip()
        api_key = os.environ.get("IGRC_LLM_API_KEY", "").strip()
        model = os.environ.get("IGRC_LLM_MODEL", "").strip()

        if provider in {"nvidia", "nim", "nvidia-nim"}:
            provider = "nvidia-nim"
            base_url = base_url or NVIDIA_NIM_BASE_URL
            api_key = api_key or os.environ.get("NVIDIA_API_KEY", "").strip()
            api_key = api_key or os.environ.get("NIM_API_KEY", "").strip()
            model = model or "nvidia/nemotron-3-nano-30b-a3b"
        elif provider in {"ollama", "local-ollama"}:
            provider = "ollama"
            base_url = base_url or OLLAMA_BASE_URL
            model = model or "llama3.1"
        elif provider in {"opencode", "openai-compatible", "compatible"}:
            provider = "openai-compatible"

        try:
            timeout = float(os.environ.get("IGRC_LLM_TIMEOUT", "18"))
        except ValueError:
            timeout = 18.0
        try:
            max_tokens = int(os.environ.get("IGRC_LLM_MAX_TOKENS", "500"))
        except ValueError:
            max_tokens = 500

        share_usernames = os.environ.get("IGRC_LLM_SHARE_USERNAMES", "").strip().lower()
        return cls(
            provider=provider,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=max(3.0, timeout),
            max_tokens=max(128, min(max_tokens, 1600)),
            share_usernames=share_usernames in {"1", "true", "yes", "on"},
        )


@dataclass(frozen=True)
class AdviceResult:
    provider: str
    model: str
    used_remote_llm: bool
    text: str
    error: str | None = None


class LLMAdvisor:
    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig.from_env()

    def advise(
        self,
        *,
        summary: dict[str, Any],
        current: dict[str, Any] | None,
        queue: list[dict[str, Any]],
    ) -> AdviceResult:
        if not self.config.enabled:
            return AdviceResult(
                provider=self.config.provider or "local",
                model=self.config.model,
                used_remote_llm=False,
                text=local_advice(summary=summary, current=current, queue=queue),
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a safety-first local workflow advisor for a user's own Instagram "
                    "pending follow-request cleanup queue. Do not suggest automation, scraping, "
                    "credential capture, DOM control, evasion, stealth, or bypassing rate limits. "
                    "Only recommend manual review, conservative pacing, stopping when Instagram "
                    "shows friction, and keeping local records. Keep the answer concise."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    self._advice_payload(summary=summary, current=current, queue=queue),
                    sort_keys=True,
                ),
            },
        ]

        try:
            return AdviceResult(
                provider=self.config.provider,
                model=self.config.model,
                used_remote_llm=True,
                text=self._chat_completion(messages),
            )
        except (urllib.error.URLError, TimeoutError, socket.timeout, KeyError, ValueError) as exc:
            return AdviceResult(
                provider=self.config.provider,
                model=self.config.model,
                used_remote_llm=False,
                text=local_advice(summary=summary, current=current, queue=queue),
                error=str(exc),
            )

    def _advice_payload(
        self,
        *,
        summary: dict[str, Any],
        current: dict[str, Any] | None,
        queue: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "goal": "Safely work through pending follow requests by manual review.",
            "hard_constraints": [
                "No automated Instagram actions.",
                "No rate-limit bypassing.",
                "Stop if Instagram shows warning, checkpoint, or account friction.",
            ],
            "summary": {
                "total": summary.get("total"),
                "counts": summary.get("counts", {}),
                "pacing": summary.get("pacing", {}),
            },
            "current": self._scrub_item(current, 0) if current else None,
            "queue_preview": [
                self._scrub_item(item, index + 1) for index, item in enumerate(queue[:20])
            ],
        }

    def _scrub_item(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        username = item.get("username") if self.config.share_usernames else f"user_{index:03d}"
        return {
            "username": username,
            "status": item.get("status"),
            "requested_at": item.get("requested_at"),
            "source": item.get("source"),
        }

    def _chat_completion(self, messages: list[dict[str, str]]) -> str:
        if self.config.provider == "nvidia-nim" and not self.config.api_key:
            raise ValueError("NVIDIA NIM requires NVIDIA_API_KEY, NIM_API_KEY, or IGRC_LLM_API_KEY.")

        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": self.config.max_tokens,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        content = response_payload["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM returned an empty response.")
        return content.strip()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers


def local_advice(
    *,
    summary: dict[str, Any],
    current: dict[str, Any] | None,
    queue: list[dict[str, Any]],
) -> str:
    counts = summary.get("counts", {})
    pacing = summary.get("pacing", {})
    pending = int(counts.get("pending", 0) or 0)
    cancelled = int(counts.get("cancelled", 0) or 0)

    if pending == 0 and not current:
        return "No pending queue items remain. Export or back up the state if you want a record."

    if not pacing.get("allowed", False):
        next_allowed = pacing.get("next_allowed_at") or "the cooldown clears"
        return (
            f"Wait until {next_allowed} before opening the next profile. "
            "If Instagram showed any warning or checkpoint, stop for the day and lower the limits."
        )

    username = current.get("username") if current else "the next queued profile"
    next_count = max(0, pending - 1)
    return (
        f"Review @{username} manually in Instagram. If it still shows Requested, cancel it there, "
        "then mark it Cancelled here so the cooldown starts. "
        f"Progress: {cancelled} cancelled, {pending} pending, about {next_count} remaining after this one."
    )
