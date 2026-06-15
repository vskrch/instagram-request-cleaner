from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
MAX_IMPORT_BYTES = 10 * 1024 * 1024
RELEVANT_KEYS = (
    "account",
    "accounts",
    "data",
    "follow",
    "handle",
    "items",
    "pending",
    "relationship",
    "request",
    "sent",
    "user",
    "users",
)
USERNAME_KEYS = (
    "username",
    "user_name",
    "handle",
    "account",
    "account_name",
    "title",
    "value",
    "name",
)
URL_KEYS = ("href", "url", "profile_url", "profile")
TIME_KEYS = ("requested_at", "timestamp", "time", "date", "created_at")


@dataclass(frozen=True)
class PendingRequestCandidate:
    username: str
    profile_url: str | None = None
    requested_at: str | None = None
    source: str = "unknown"


class ImportErrorWithContext(ValueError):
    pass


def load_candidates(path: str | Path) -> list[PendingRequestCandidate]:
    input_path = Path(path).expanduser()
    if not input_path.exists():
        raise ImportErrorWithContext(f"Input file does not exist: {input_path}")
    if not input_path.is_file():
        raise ImportErrorWithContext(f"Input path is not a file: {input_path}")
    if input_path.stat().st_size > MAX_IMPORT_BYTES:
        raise ImportErrorWithContext(
            f"Input file is too large. Limit is {MAX_IMPORT_BYTES // (1024 * 1024)} MB."
        )

    text = input_path.read_text(encoding="utf-8-sig")
    return load_candidates_from_text(input_path.name, text, source=str(input_path))


def load_candidates_from_text(
    filename: str,
    text: str,
    *,
    source: str | None = None,
) -> list[PendingRequestCandidate]:
    suffix = Path(filename).suffix.lower()
    source_name = source or filename
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ImportErrorWithContext(
            f"Input is too large. Limit is {MAX_IMPORT_BYTES // (1024 * 1024)} MB."
        )
    if suffix == ".json":
        return _dedupe(_load_json(text, source_name))
    if suffix == ".csv":
        return _dedupe(_load_csv(text, source_name))
    if suffix in {".txt", ".list", ""}:
        return _dedupe(_load_txt(text, source_name))
    raise ImportErrorWithContext(
        f"Unsupported file type '{suffix}'. Use .json, .csv, .txt, or .list."
    )


def _load_json(text: str, source: str) -> list[PendingRequestCandidate]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImportErrorWithContext(f"Invalid JSON: {exc}") from exc

    found: list[PendingRequestCandidate] = []
    _visit_json(data, found, source=source, relevant=False, depth=0)
    if not found:
        raise ImportErrorWithContext(
            "No usernames found. Expected Instagram export JSON, a list of usernames, "
            "or objects with username/title/value fields."
        )
    return found


def _load_csv(text: str, source: str) -> list[PendingRequestCandidate]:
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise ImportErrorWithContext("CSV is empty.")

    header = [cell.strip().lower() for cell in rows[0]]
    has_header = any(name in header for name in (*USERNAME_KEYS, *URL_KEYS, *TIME_KEYS))
    found: list[PendingRequestCandidate] = []

    if has_header:
        dict_rows = csv.DictReader(text.splitlines())
        for row in dict_rows:
            username = _first_value(row, USERNAME_KEYS)
            profile_url = _first_value(row, URL_KEYS)
            requested_at = parse_timestamp(_first_value(row, TIME_KEYS))
            candidate = _candidate_from_values(username, profile_url, requested_at, source)
            if candidate:
                found.append(candidate)
    else:
        for row in rows:
            if not row:
                continue
            candidate = _candidate_from_values(row[0], None, None, source)
            if candidate:
                found.append(candidate)

    if not found:
        raise ImportErrorWithContext("No usernames found in CSV.")
    return found


def _load_txt(text: str, source: str) -> list[PendingRequestCandidate]:
    found: list[PendingRequestCandidate] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        candidate = _candidate_from_values(line.split()[0], None, None, source)
        if candidate:
            found.append(candidate)
    if not found:
        raise ImportErrorWithContext("No usernames found in text file.")
    return found


def _visit_json(
    obj: Any,
    found: list[PendingRequestCandidate],
    *,
    source: str,
    relevant: bool,
    depth: int,
) -> None:
    if depth > 30:
        return

    if isinstance(obj, dict):
        # Instagram exports commonly use objects like:
        # {"title": "...", "string_list_data": [{"href": "...", "value": "...", "timestamp": ...}]}
        if isinstance(obj.get("string_list_data"), list):
            title = obj.get("title")
            for entry in obj["string_list_data"]:
                if isinstance(entry, dict):
                    username = entry.get("value") or title or entry.get("href")
                    profile_url = entry.get("href")
                    requested_at = parse_timestamp(entry.get("timestamp"))
                    candidate = _candidate_from_values(
                        username, profile_url, requested_at, source
                    )
                    if candidate:
                        found.append(candidate)

        username = _first_value(obj, USERNAME_KEYS)
        profile_url = _first_value(obj, URL_KEYS)
        requested_at = parse_timestamp(_first_value(obj, TIME_KEYS))
        candidate = _candidate_from_values(username, profile_url, requested_at, source)
        if candidate:
            found.append(candidate)

        for key, value in obj.items():
            key_relevant = relevant or _is_relevant_key(key)
            if key_relevant or isinstance(value, (dict, list)):
                _visit_json(value, found, source=source, relevant=key_relevant, depth=depth + 1)
        return

    if isinstance(obj, list):
        if obj and all(isinstance(item, str) for item in obj):
            for item in obj:
                candidate = _candidate_from_values(item, None, None, source)
                if candidate:
                    found.append(candidate)
            return
        for item in obj:
            _visit_json(item, found, source=source, relevant=relevant, depth=depth + 1)
        return

    if relevant and isinstance(obj, str):
        candidate = _candidate_from_values(obj, None, None, source)
        if candidate:
            found.append(candidate)


def _is_relevant_key(key: Any) -> bool:
    key_text = str(key).lower()
    return any(token in key_text for token in RELEVANT_KEYS)


def _first_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    normalized = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        if key in normalized and normalized[key] not in (None, ""):
            return normalized[key]
    return None


def _candidate_from_values(
    username_value: Any,
    profile_url_value: Any,
    requested_at: str | None,
    source: str,
) -> PendingRequestCandidate | None:
    username = normalize_username(username_value)
    profile_url = normalize_profile_url(profile_url_value or username_value)
    if not username and profile_url:
        username = normalize_username(profile_url)
    if not username:
        return None
    return PendingRequestCandidate(
        username=username,
        profile_url=profile_url or f"https://www.instagram.com/{username}/",
        requested_at=requested_at,
        source=source,
    )


def normalize_username(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip("\"'")
    if not text:
        return None
    if "instagram.com" in text.lower():
        parsed = urlparse(text if "://" in text else f"https://{text}")
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            text = parts[0]
    if text.startswith("@"):
        text = text[1:]
    text = text.strip().strip("/")
    if not USERNAME_RE.match(text):
        return None
    if text in {"explore", "accounts", "p", "reel", "stories"}:
        return None
    return text


def normalize_profile_url(value: Any) -> str | None:
    username = normalize_username(value)
    if username:
        return f"https://www.instagram.com/{username}/"
    if value is None:
        return None
    text = str(value).strip()
    if "instagram.com" not in text.lower():
        return None
    parsed = urlparse(text if "://" in text else f"https://{text}")
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    username = normalize_username(parts[0])
    if not username:
        return None
    return f"https://www.instagram.com/{username}/"


def parse_timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds = seconds / 1000
        try:
            return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_timestamp(int(text))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except ValueError:
        return text


def _dedupe(candidates: list[PendingRequestCandidate]) -> list[PendingRequestCandidate]:
    deduped: dict[str, PendingRequestCandidate] = {}
    for candidate in candidates:
        key = candidate.username.lower()
        if key not in deduped:
            deduped[key] = candidate
            continue
        previous = deduped[key]
        deduped[key] = PendingRequestCandidate(
            username=previous.username,
            profile_url=previous.profile_url or candidate.profile_url,
            requested_at=previous.requested_at or candidate.requested_at,
            source=previous.source,
        )
    return list(deduped.values())
