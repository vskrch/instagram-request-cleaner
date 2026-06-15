from __future__ import annotations

import csv
import io
import json
import shutil
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .decision import decision_sort_key, decide_request, policy_from_settings
from .importer import PendingRequestCandidate
from .pacing import DEFAULT_SETTINGS, estimate_eta_seconds, evaluate_pacing, next_cooldown_seconds, now_utc


SCHEMA_VERSION = 1
ACTION_EVENT_TYPES = {"cancelled"}
VALID_STATUSES = {"pending", "cancelled", "skipped", "not_found", "snoozed", "archived"}
MAX_EXPORT_ROWS = 250_000
SETTING_BOUNDS: dict[str, tuple[int, int]] = {
    "min_interval_seconds": (60, 86_400),
    "max_interval_seconds": (60, 86_400),
    "max_actions_per_hour": (1, 120),
    "max_actions_per_day": (1, 500),
    "session_break_every": (0, 500),
    "session_break_minutes": (0, 1_440),
    "auto_minor_decisions": (0, 1),
    "recent_request_snooze_days": (1, 90),
}


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.backup_dir = self.db_path.parent / "backups"
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            if self.db_path.exists() and not self._integrity_ok():
                self._move_corrupt_db()
            with self._connect() as conn:
                conn.executescript(
                    """
                    PRAGMA journal_mode=WAL;
                    PRAGMA foreign_keys=ON;

                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS requests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL UNIQUE,
                        profile_url TEXT,
                        requested_at TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        source TEXT,
                        notes TEXT NOT NULL DEFAULT '',
                        snooze_until TEXT,
                        imported_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_action_at TEXT,
                        action_count INTEGER NOT NULL DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT,
                        event_type TEXT NOT NULL,
                        payload TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
                    CREATE INDEX IF NOT EXISTS idx_requests_updated_at ON requests(updated_at);
                    CREATE INDEX IF NOT EXISTS idx_events_type_created ON events(event_type, created_at);
                    """
                )
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                for key, value in DEFAULT_SETTINGS.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                        (key, value),
                    )

    def import_candidates(
        self,
        candidates: list[PendingRequestCandidate],
        *,
        source_path: str,
    ) -> dict[str, int]:
        with self._lock:
            self.initialize()
            if self.db_path.exists():
                self.backup(f"pre-import-{_safe_name(Path(source_path).stem)}")

            now = now_utc().isoformat()
            added = 0
            updated = 0
            unchanged = 0
            with self._connect() as conn:
                for candidate in candidates:
                    profile_url_candidate = (
                        candidate.profile_url
                        or f"https://www.instagram.com/{candidate.username}/"
                    )
                    existing = conn.execute(
                        "SELECT username, profile_url, requested_at, source FROM requests WHERE lower(username)=lower(?)",
                        (candidate.username,),
                    ).fetchone()
                    if existing is None:
                        conn.execute(
                            """
                            INSERT INTO requests(
                                username, profile_url, requested_at, status, source, imported_at, updated_at
                            )
                            VALUES (?, ?, ?, 'pending', ?, ?, ?)
                            """,
                                (
                                    candidate.username,
                                    profile_url_candidate,
                                    candidate.requested_at,
                                    candidate.source,
                                    now,
                                now,
                            ),
                        )
                        added += 1
                        continue

                    profile_url = existing["profile_url"] or profile_url_candidate
                    requested_at = existing["requested_at"] or candidate.requested_at
                    source = existing["source"] or candidate.source
                    changed = (
                        profile_url != existing["profile_url"]
                        or requested_at != existing["requested_at"]
                        or source != existing["source"]
                    )
                    if changed:
                        conn.execute(
                            """
                            UPDATE requests
                            SET profile_url=?, requested_at=?, source=?, updated_at=?
                            WHERE lower(username)=lower(?)
                            """,
                            (profile_url, requested_at, source, now, candidate.username),
                        )
                        updated += 1
                    else:
                        unchanged += 1

                conn.execute(
                    "INSERT INTO events(username, event_type, payload, created_at) VALUES(NULL, ?, ?, ?)",
                    (
                        "imported",
                        json.dumps({"source_path": source_path, "count": len(candidates)}),
                        now,
                    ),
                )
            return {"added": added, "updated": updated, "unchanged": unchanged, "total": len(candidates)}

    def list_requests(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self.initialize()
        if status and status != "all" and status not in VALID_STATUSES:
            raise ValueError(f"Invalid status filter: {status}")
        limit = min(max(1, int(limit)), MAX_EXPORT_ROWS)
        offset = max(0, int(offset))
        clauses: list[str] = []
        params: list[Any] = []
        if status and status != "all":
            clauses.append("status=?")
            params.append(status)
        if search:
            clauses.append("lower(username) LIKE ?")
            params.append(f"%{search.lower()}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM requests
                {where}
                ORDER BY
                    CASE status
                        WHEN 'pending' THEN 0
                        WHEN 'snoozed' THEN 1
                        ELSE 2
                    END,
                    COALESCE(requested_at, imported_at) ASC,
                    username ASC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def next_pending(self) -> dict[str, Any] | None:
        self.initialize()
        now = now_utc().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM requests
                WHERE status='pending'
                   OR (status='snoozed' AND (snooze_until IS NULL OR snooze_until <= ?))
                ORDER BY COALESCE(requested_at, imported_at) ASC, username ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
        return dict(row) if row else None

    def assist_step(self, *, apply_minor_decisions: bool = True) -> dict[str, Any]:
        with self._lock:
            self.initialize()
            applied = self.apply_minor_decisions() if apply_minor_decisions else []
            summary = self.summary()
            settings = summary["settings"]
            policy = policy_from_settings(settings)
            item = self._next_decision_item(policy)
            decision = decide_request(item, policy=policy).__dict__ if item else None
            return {
                "applied_minor_decisions": applied,
                "item": item,
                "decision": decision,
                "summary": summary,
            }

    def apply_minor_decisions(self) -> list[dict[str, Any]]:
        with self._lock:
            self.initialize()
            settings = self.settings()
            policy = policy_from_settings(settings)
            if not policy.auto_minor_decisions:
                return []

            applied: list[dict[str, Any]] = []
            current = now_utc()
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM requests WHERE status='pending' ORDER BY COALESCE(requested_at, imported_at), username"
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    decision = decide_request(item, policy=policy, now=current)
                    if decision.code != "auto_snooze_recent" or not decision.snooze_until:
                        continue
                    now = current.isoformat()
                    note = f"Auto-snoozed by minor-decision policy: {decision.reason}"
                    conn.execute(
                        """
                        UPDATE requests
                        SET status='snoozed', notes=?, snooze_until=?, updated_at=?
                        WHERE id=?
                        """,
                        (note, decision.snooze_until, now, row["id"]),
                    )
                    payload = {
                        "decision": decision.code,
                        "reason": decision.reason,
                        "snooze_until": decision.snooze_until,
                    }
                    conn.execute(
                        "INSERT INTO events(username, event_type, payload, created_at) VALUES(?, ?, ?, ?)",
                        (row["username"], "auto_snoozed_recent", json.dumps(payload), now),
                    )
                    applied.append({"username": row["username"], **payload})
            return applied

    def mark_request(
        self,
        username: str,
        status: str,
        *,
        notes: str = "",
        snooze_minutes: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if status not in VALID_STATUSES:
                raise ValueError(f"Invalid status: {status}")
            self.initialize()
            current = now_utc()
            now = current.isoformat()
            snooze_until = None
            if status == "snoozed":
                minutes = max(1, int(snooze_minutes or 60))
                snooze_until = (current + timedelta(minutes=minutes)).isoformat()

            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM requests WHERE lower(username)=lower(?)",
                    (username,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Unknown username: {username}")
                if row["status"] == status:
                    return self.summary()

                action_count = int(row["action_count"] or 0)
                payload: dict[str, Any] = {"status": status, "notes": notes}
                if snooze_until:
                    payload["snooze_until"] = snooze_until

                if status in ACTION_EVENT_TYPES:
                    self._ensure_action_allowed(conn)
                    action_count += 1
                    completed_actions = self.action_count(conn=conn) + 1
                    cooldown = next_cooldown_seconds(self.settings(conn=conn), completed_actions)
                    next_allowed_at = (current + timedelta(seconds=cooldown)).isoformat()
                    conn.execute(
                        "INSERT OR REPLACE INTO settings(key, value) VALUES('next_allowed_at', ?)",
                        (next_allowed_at,),
                    )
                    payload["cooldown_seconds"] = cooldown
                    payload["next_allowed_at"] = next_allowed_at

                conn.execute(
                    """
                    UPDATE requests
                    SET status=?, notes=?, snooze_until=?, updated_at=?, last_action_at=?, action_count=?
                    WHERE lower(username)=lower(?)
                    """,
                    (status, notes, snooze_until, now, now, action_count, username),
                )
                conn.execute(
                    "INSERT INTO events(username, event_type, payload, created_at) VALUES(?, ?, ?, ?)",
                    (row["username"], status, json.dumps(payload), now),
                )
            return self.summary()

    def summary(self) -> dict[str, Any]:
        self.initialize()
        with self._connect() as conn:
            counts = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM requests GROUP BY status"
                ).fetchall()
            }
            total = conn.execute("SELECT COUNT(*) AS count FROM requests").fetchone()["count"]
            action_times = [
                row["created_at"]
                for row in conn.execute(
                    "SELECT created_at FROM events WHERE event_type IN ('cancelled')"
                ).fetchall()
            ]
            settings = self.settings(conn=conn)
            pacing = evaluate_pacing(settings, action_times)
            pending = int(counts.get("pending", 0))
            snoozed = int(counts.get("snoozed", 0))
            eta = estimate_eta_seconds(
                settings, action_times, pending + snoozed,
            )
        return {
            "total": total,
            "counts": counts,
            "settings": settings,
            "pacing": pacing.__dict__,
            "eta": eta,
            "db_path": str(self.db_path),
        }

    def settings(self, *, conn: sqlite3.Connection | None = None) -> dict[str, str]:
        self.initialize_if_needed()
        close = False
        if conn is None:
            conn = self._connect()
            close = True
        try:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            settings = dict(DEFAULT_SETTINGS)
            settings.update({row["key"]: row["value"] for row in rows})
            return settings
        finally:
            if close:
                conn.close()

    def update_settings(self, updates: dict[str, Any]) -> dict[str, str]:
        with self._lock:
            self.initialize()
            allowed = set(DEFAULT_SETTINGS)
            numeric = allowed - {"next_allowed_at"}
            with self._connect() as conn:
                for key, value in updates.items():
                    if key not in allowed:
                        continue
                    if key in numeric:
                        try:
                            value = str(_bounded_setting(key, int(value)))
                        except (TypeError, ValueError):
                            continue
                    else:
                        value = str(value or "")
                    conn.execute(
                        "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                        (key, value),
                    )
                settings = self.settings(conn=conn)
                min_interval = int(settings["min_interval_seconds"])
                max_interval = int(settings["max_interval_seconds"])
                if max_interval < min_interval:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings(key, value) VALUES('max_interval_seconds', ?)",
                        (str(min_interval),),
                    )
            return self.settings()

    def clear_cooldown(self) -> dict[str, str]:
        with self._lock:
            self.initialize()
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings(key, value) VALUES('next_allowed_at', '')"
                )
            return self.settings()

    def action_count(self, *, conn: sqlite3.Connection | None = None) -> int:
        close = False
        if conn is None:
            conn = self._connect()
            close = True
        try:
            return int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM events WHERE event_type IN ('cancelled')"
                ).fetchone()["count"]
            )
        finally:
            if close:
                conn.close()

    def backup(self, reason: str = "manual") -> str:
        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            if not self.db_path.exists():
                return ""
            stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
            target = self.backup_dir / f"{self.db_path.stem}-{stamp}-{_safe_name(reason)}.sqlite3"
            with self._connect() as source, sqlite3.connect(target) as destination:
                source.backup(destination)
            return str(target)

    def export_json(self) -> str:
        payload = {
            "summary": self.summary(),
            "requests": self.list_requests(status="all", limit=MAX_EXPORT_ROWS),
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def export_csv(self) -> str:
        rows = self.list_requests(status="all", limit=MAX_EXPORT_ROWS)
        output = io.StringIO()
        fieldnames = [
            "username",
            "profile_url",
            "requested_at",
            "status",
            "notes",
            "snooze_until",
            "imported_at",
            "updated_at",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})
        return output.getvalue()

    def health(self) -> dict[str, Any]:
        self.initialize()
        return {
            "ok": self._integrity_ok(),
            "db_path": str(self.db_path),
            "backup_dir": str(self.backup_dir),
            "summary": self.summary(),
        }

    def initialize_if_needed(self) -> None:
        if not self.db_path.exists():
            self.initialize()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_action_allowed(self, conn: sqlite3.Connection) -> None:
        action_times = [
            row["created_at"]
            for row in conn.execute(
                "SELECT created_at FROM events WHERE event_type IN ('cancelled')"
            ).fetchall()
        ]
        pacing = evaluate_pacing(self.settings(conn=conn), action_times)
        if pacing.allowed:
            return
        suffix = f" Next allowed at {pacing.next_allowed_at}." if pacing.next_allowed_at else ""
        raise ValueError(f"Action blocked by pacing: {pacing.reason}.{suffix}")

    def _next_decision_item(self, policy) -> dict[str, Any] | None:
        now = now_utc().isoformat()
        with self._connect() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM requests
                    WHERE status='pending'
                       OR (status='snoozed' AND (snooze_until IS NULL OR snooze_until <= ?))
                    """,
                    (now,),
                ).fetchall()
            ]
        if not rows:
            return None
        rows.sort(key=lambda item: decision_sort_key(item, policy))
        return rows[0]

    def _integrity_ok(self) -> bool:
        try:
            with self._connect() as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            return result == "ok"
        except sqlite3.DatabaseError:
            return False

    def _move_corrupt_db(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.backup_dir / f"{self.db_path.name}.corrupt-{stamp}"
        shutil.move(str(self.db_path), target)


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.lower())
    return cleaned.strip("-") or "state"


def _bounded_setting(key: str, value: int) -> int:
    lower, upper = SETTING_BOUNDS[key]
    return min(max(value, lower), upper)
