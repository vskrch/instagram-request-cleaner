from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from typing import Any

from .db import Store


@dataclass(frozen=True)
class BrowserAssistResult:
    opened: bool
    reason: str
    username: str | None = None
    url: str | None = None
    next_allowed_at: str | None = None


def open_next_profile(store: Store, *, browser: str | None = None) -> BrowserAssistResult:
    summary = store.summary()
    pacing = summary["pacing"]
    if not pacing["allowed"]:
        return BrowserAssistResult(
            opened=False,
            reason=pacing["reason"],
            next_allowed_at=pacing["next_allowed_at"],
        )

    item = store.next_pending()
    if not item:
        return BrowserAssistResult(opened=False, reason="empty")
    return open_profile(item, browser=browser)


def open_profile(item: dict[str, Any], *, browser: str | None = None) -> BrowserAssistResult:
    username = str(item.get("username") or "")
    url = str(item.get("profile_url") or f"https://www.instagram.com/{username}/")
    if not username or not url.startswith("https://www.instagram.com/"):
        return BrowserAssistResult(opened=False, reason="invalid_profile", username=username, url=url)

    try:
        if browser:
            opened = webbrowser.get(browser).open_new_tab(url)
        else:
            opened = webbrowser.open_new_tab(url)
    except Exception:  # noqa: BLE001
        opened = False
    return BrowserAssistResult(
        opened=bool(opened),
        reason="opened" if opened else "browser_open_failed",
        username=username,
        url=url,
    )
