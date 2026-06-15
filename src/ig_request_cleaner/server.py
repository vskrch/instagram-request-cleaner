from __future__ import annotations

import ipaddress
import json
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .browser_assist import open_next_profile
from .db import Store
from .importer import ImportErrorWithContext, load_candidates, load_candidates_from_text
from .llm import LLMAdvisor


MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024
MAX_API_LIST_LIMIT = 1000


def run_server(
    *,
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    store = Store(db_path)
    store.initialize()
    handler = _make_handler(store)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{server.server_address[1]}"
    print(f"Serving Instagram request cleaner at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    server.serve_forever()


def _make_handler(store: Store) -> type[BaseHTTPRequestHandler]:
    class RequestCleanerHandler(BaseHTTPRequestHandler):
        server_version = "IGRequestCleaner/0.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(INDEX_HTML)
                return
            if parsed.path == "/app.css":
                self._send_bytes(APP_CSS.encode("utf-8"), "text/css; charset=utf-8")
                return
            if parsed.path == "/app.js":
                self._send_bytes(APP_JS.encode("utf-8"), "application/javascript; charset=utf-8")
                return
            if parsed.path == "/favicon.ico":
                self._send_bytes(FAVICON_SVG.encode("utf-8"), "image/svg+xml")
                return
            if parsed.path == "/api/summary":
                self._send_json(store.summary())
                return
            if parsed.path == "/api/health":
                self._send_json(store.health())
                return
            if parsed.path == "/api/next":
                self._send_json({"item": store.next_pending(), "summary": store.summary()})
                return
            if parsed.path == "/api/items":
                query = parse_qs(parsed.query)
                status = query.get("status", ["pending"])[0]
                search = query.get("search", [""])[0]
                limit = min(_int_query(query, "limit", 100), MAX_API_LIST_LIMIT)
                offset = _int_query(query, "offset", 0)
                try:
                    items = store.list_requests(
                        status=status,
                        search=search,
                        limit=limit,
                        offset=offset,
                    )
                except ValueError as exc:
                    self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._send_json({"items": items})
                return
            if parsed.path == "/api/export.json":
                self._send_bytes(
                    store.export_json().encode("utf-8"),
                    "application/json; charset=utf-8",
                    filename="ig-request-cleaner-export.json",
                )
                return
            if parsed.path == "/api/export.csv":
                self._send_bytes(
                    store.export_csv().encode("utf-8"),
                    "text/csv; charset=utf-8",
                    filename="ig-request-cleaner-export.csv",
                )
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                body = self._read_json()
                if parsed.path == "/api/import-path":
                    if not self._is_loopback_request():
                        self._send_error(
                            HTTPStatus.FORBIDDEN,
                            "Path import is only allowed from the local machine.",
                        )
                        return
                    path = str(body.get("path", "")).strip()
                    candidates = load_candidates(path)
                    stats = store.import_candidates(candidates, source_path=path)
                    self._send_json({"ok": True, "stats": stats, "summary": store.summary()})
                    return
                if parsed.path == "/api/import-text":
                    filename = str(body.get("filename", "pending_requests.json"))
                    text = str(body.get("text", ""))
                    candidates = load_candidates_from_text(filename, text)
                    stats = store.import_candidates(candidates, source_path=filename)
                    self._send_json({"ok": True, "stats": stats, "summary": store.summary()})
                    return
                if parsed.path == "/api/mark":
                    result = store.mark_request(
                        username=str(body.get("username", "")),
                        status=str(body.get("status", "")),
                        notes=str(body.get("notes", "")),
                        snooze_minutes=int(body.get("snooze_minutes") or 60),
                    )
                    self._send_json({"ok": True, "summary": result, "next": store.next_pending()})
                    return
                if parsed.path == "/api/settings":
                    self._send_json({"ok": True, "settings": store.update_settings(body)})
                    return
                if parsed.path == "/api/clear-cooldown":
                    self._send_json({"ok": True, "settings": store.clear_cooldown()})
                    return
                if parsed.path == "/api/backup":
                    self._send_json({"ok": True, "path": store.backup("web")})
                    return
                if parsed.path == "/api/apply-minor-decisions":
                    applied = store.apply_minor_decisions()
                    self._send_json(
                        {
                            "ok": True,
                            "applied_minor_decisions": applied,
                            "summary": store.summary(),
                            "next": store.next_pending(),
                        }
                    )
                    return
                if parsed.path == "/api/assist-step":
                    apply_minor = bool(body.get("apply_minor_decisions", True))
                    self._send_json({"ok": True, **store.assist_step(apply_minor_decisions=apply_minor)})
                    return
                if parsed.path == "/api/advice":
                    result = LLMAdvisor().advise(
                        summary=store.summary(),
                        current=store.next_pending(),
                        queue=store.list_requests(status="pending", limit=20),
                    )
                    self._send_json({"ok": True, **result.__dict__})
                    return
                if parsed.path == "/api/open-next":
                    if not self._is_loopback_request():
                        self._send_error(
                            HTTPStatus.FORBIDDEN,
                            "Browser assist is only allowed from the local machine.",
                        )
                        return
                    result = open_next_profile(store)
                    self._send_json({"ok": True, **result.__dict__})
                    return
            except (ImportErrorWithContext, ValueError, KeyError) as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except json.JSONDecodeError:
                self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return
            except Exception as exc:  # noqa: BLE001
                print(f"request failed: {parsed.path}: {exc}", file=sys.stderr)
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error")
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            if length > MAX_REQUEST_BODY_BYTES:
                raise ValueError(
                    f"Request body is too large. Limit is {MAX_REQUEST_BODY_BYTES // (1024 * 1024)} MB."
                )
            payload = self.rfile.read(length).decode("utf-8")
            return json.loads(payload)

        def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send_bytes(
                json.dumps(payload, sort_keys=True).encode("utf-8"),
                "application/json; charset=utf-8",
                status=status,
            )

        def _send_html(self, html: str) -> None:
            self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"ok": False, "error": message}, status=status)

        def _send_bytes(
            self,
            body: bytes,
            content_type: str | None = None,
            *,
            status: HTTPStatus = HTTPStatus.OK,
            filename: str | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if content_type and content_type.startswith("text/html"):
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'",
                )
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def _is_loopback_request(self) -> bool:
            try:
                return ipaddress.ip_address(self.client_address[0]).is_loopback
            except ValueError:
                return False

    return RequestCleanerHandler


def _int_query(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return max(0, int(query.get(key, [str(default)])[0]))
    except ValueError:
        return default


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>IG Request Cleaner</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <main class="shell">
    <section class="topbar">
      <div>
        <h1>IG Request Cleaner</h1>
        <p id="dbPath"></p>
      </div>
      <div class="actions">
        <a class="button secondary" href="/api/export.csv">Export CSV</a>
        <a class="button secondary" href="/api/export.json">Export JSON</a>
        <button id="backupBtn" class="button secondary" type="button">Backup</button>
        <button id="openNextBtn" class="button" type="button">Open Next</button>
      </div>
    </section>

    <section class="stats" id="stats"></section>

    <section class="progressBarWrap" id="progressBarWrap">
      <div class="progressInfo">
        <span id="progressText" class="progressText">0 of 0 completed</span>
        <span id="etaText" class="etaText">ETA: calculating...</span>
      </div>
      <div class="progressTrack">
        <div class="progressFill" id="progressFill" style="width: 0%"></div>
      </div>
      <div class="progressSubtext" id="progressSubtext"></div>
    </section>

    <section class="grid">
      <div class="panel work">
        <div class="panelHead">
          <h2>Current Request</h2>
          <span id="paceState" class="badge">Loading</span>
        </div>
        <div id="currentItem" class="current"></div>
      </div>

      <div class="panel importPanel">
        <h2>Import</h2>
        <div class="field">
          <label for="fileInput">JSON, CSV, or TXT file</label>
          <input id="fileInput" type="file" accept=".json,.csv,.txt,.list">
        </div>
        <div class="field">
          <label for="pathInput">Local path</label>
          <div class="row">
            <input id="pathInput" type="text" placeholder="/path/to/pending_requests.json">
            <button id="pathImportBtn" class="button" type="button">Import</button>
          </div>
        </div>
        <p id="importResult" class="muted"></p>
      </div>
    </section>

    <section class="grid bottom">
      <div class="panel">
        <div class="panelHead">
          <h2>Queue</h2>
          <div class="filters">
            <select id="statusFilter">
              <option value="pending">Pending</option>
              <option value="snoozed">Snoozed</option>
              <option value="cancelled">Cancelled</option>
              <option value="skipped">Skipped</option>
              <option value="not_found">Not found</option>
              <option value="all">All</option>
            </select>
            <input id="searchInput" type="search" placeholder="Search">
          </div>
        </div>
        <div id="queue" class="queue"></div>
      </div>

      <div class="panel">
        <h2>Pacing</h2>
        <div class="settings">
          <label>Min seconds<input id="min_interval_seconds" type="number" min="0"></label>
          <label>Max seconds<input id="max_interval_seconds" type="number" min="0"></label>
          <label>Max per hour<input id="max_actions_per_hour" type="number" min="0"></label>
          <label>Max per day<input id="max_actions_per_day" type="number" min="0"></label>
          <label>Break every<input id="session_break_every" type="number" min="0"></label>
          <label>Break minutes<input id="session_break_minutes" type="number" min="0"></label>
          <label>Minor decisions<select id="auto_minor_decisions"><option value="1">Auto</option><option value="0">Manual</option></select></label>
          <label>Recent snooze days<input id="recent_request_snooze_days" type="number" min="1" max="90"></label>
        </div>
        <div class="actions left">
          <button id="saveSettingsBtn" class="button" type="button">Save</button>
          <button id="clearCooldownBtn" class="button secondary" type="button">Clear Cooldown</button>
        </div>
        <p class="muted">Use lower limits if Instagram shows warnings or friction.</p>
      </div>

      <div class="panel">
        <div class="panelHead">
          <h2>Advisor</h2>
          <button id="advisorBtn" class="button secondary" type="button">Refresh</button>
        </div>
        <p id="advisorText" class="advisorText">Ready.</p>
      </div>

      <div class="panel assistPanel" id="assistPanel">
        <div class="panelHead">
          <h2>Assist Mode</h2>
          <button id="assistToggleBtn" class="button" type="button">Start</button>
        </div>
        <div id="assistStatus" class="assistStatus">Idle</div>
        <label class="checkLine"><input id="autoOpenToggle" type="checkbox" checked> Auto-open next profile when ready</label>
        <label class="checkLine"><input id="soundToggle" type="checkbox" checked> Sound alerts</label>
        <button id="beepTestBtn" class="button secondary" type="button">Test Alert</button>
      </div>
    </section>
  </main>
  <div id="toast" class="toast"></div>
  <script src="/app.js"></script>
</body>
</html>
"""


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="7" fill="#0c6b58"/>
<path d="M9 17.5l4.2 4.2L23.5 11.4" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


APP_CSS = """
:root {
  color-scheme: light;
  --bg: #f5f7f8;
  --panel: #ffffff;
  --ink: #172026;
  --muted: #65727c;
  --line: #d9e0e5;
  --accent: #0c6b58;
  --accent-strong: #084d40;
  --warn: #9b4f00;
  --danger: #b42318;
  --shadow: 0 8px 28px rgba(23, 32, 38, 0.08);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.shell {
  max-width: 1180px;
  margin: 0 auto;
  padding: 24px;
}

.topbar, .panelHead, .actions, .row, .filters {
  display: flex;
  align-items: center;
  gap: 12px;
}

.topbar {
  justify-content: space-between;
  margin-bottom: 18px;
}

h1, h2, p { margin: 0; }
h1 { font-size: 28px; line-height: 1.1; }
h2 { font-size: 16px; line-height: 1.2; }
#dbPath, .muted { color: var(--muted); }

.stats {
  display: grid;
  grid-template-columns: repeat(6, minmax(120px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.stat, .panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--shadow);
}

.stat { padding: 14px; }
.stat strong { display: block; font-size: 24px; line-height: 1.2; }
.stat span { color: var(--muted); font-size: 12px; text-transform: uppercase; }

.grid {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.65fr);
  gap: 18px;
  align-items: start;
}

.bottom { margin-top: 18px; }
.panel { padding: 18px; }
.panelHead { justify-content: space-between; margin-bottom: 14px; }
.work { min-height: 280px; }

.button {
  border: 0;
  background: var(--accent);
  color: #fff;
  border-radius: 7px;
  padding: 9px 12px;
  min-height: 38px;
  cursor: pointer;
  text-decoration: none;
  font-weight: 650;
}

.button:hover { background: var(--accent-strong); }
.button.secondary {
  background: #edf3f1;
  color: var(--accent-strong);
}
.button.secondary:hover { background: #dce9e5; }
.button.danger { background: var(--danger); }
.button:disabled {
  cursor: not-allowed;
  opacity: 0.52;
}
.button:disabled:hover { background: var(--accent); }
.button.secondary:disabled:hover { background: #edf3f1; }
.actions.left { justify-content: flex-start; margin-top: 14px; }

.badge {
  border-radius: 999px;
  background: #e5f2ee;
  color: var(--accent-strong);
  padding: 5px 10px;
  font-size: 12px;
  font-weight: 700;
}
.badge.warn { background: #fff4df; color: var(--warn); }

.current {
  display: grid;
  gap: 16px;
  min-height: 190px;
}
.profileName {
  font-size: clamp(28px, 4vw, 46px);
  line-height: 1.05;
  overflow-wrap: anywhere;
}
.profileUrl {
  display: inline-block;
  color: var(--accent-strong);
  overflow-wrap: anywhere;
}
.currentActions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.field, .settings label {
  display: grid;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}

input, select {
  width: 100%;
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 8px 10px;
  color: var(--ink);
  background: #fff;
  font: inherit;
}

.importPanel { display: grid; gap: 14px; }
.row input { min-width: 0; }

.queue {
  display: grid;
  gap: 8px;
  max-height: 520px;
  overflow: auto;
  padding-right: 4px;
}
.queueItem {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px;
}
.queueItem a {
  color: var(--ink);
  font-weight: 700;
  overflow-wrap: anywhere;
}
.queueMeta {
  color: var(--muted);
  font-size: 12px;
}
.decisionLine {
  border-left: 3px solid var(--accent);
  padding: 8px 10px;
  background: #f3f8f6;
  color: var(--accent-strong);
  border-radius: 6px;
}
.settings {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.advisorText {
  min-height: 96px;
  white-space: pre-wrap;
  color: var(--ink);
}
.assistPanel.alerting {
  animation: attentionPulse 0.7s ease-in-out 8;
  border-color: var(--warn);
}
.assistStatus {
  min-height: 48px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 12px;
  background: #fbfcfc;
  color: var(--ink);
}
.checkLine {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  color: var(--muted);
}
.checkLine input {
  width: auto;
  min-height: auto;
}
@keyframes attentionPulse {
  0%, 100% { box-shadow: var(--shadow); }
  50% { box-shadow: 0 0 0 4px rgba(155, 79, 0, 0.24), var(--shadow); }
}
.progressBarWrap {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--shadow);
  padding: 18px;
  margin-bottom: 18px;
}
.progressInfo {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 10px;
}
.progressText {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
}
.etaText {
  font-size: 14px;
  color: var(--accent-strong);
  font-weight: 650;
}
.progressTrack {
  height: 10px;
  background: #e5e9ec;
  border-radius: 999px;
  overflow: hidden;
}
.progressFill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), #34d399);
  border-radius: 999px;
  transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
  min-width: 0%;
}
.progressFill.complete {
  background: linear-gradient(90deg, #34d399, #06b6d4);
}
.progressSubtext {
  margin-top: 8px;
  font-size: 12px;
  color: var(--muted);
}
.cooldownTimer {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: #fff4df;
  color: var(--warn);
  border: 1px solid #f0d4a8;
  border-radius: 8px;
  padding: 10px 14px;
  margin-top: 10px;
  font-size: 14px;
  font-weight: 650;
}
.cooldownTimer .timerDot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--warn);
  animation: timerPulse 1s ease-in-out infinite;
}
@keyframes timerPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.toast {
  position: fixed;
  right: 18px;
  bottom: 18px;
  max-width: min(420px, calc(100vw - 36px));
  background: var(--ink);
  color: #fff;
  border-radius: 8px;
  padding: 12px 14px;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 0.15s ease, transform 0.15s ease;
  pointer-events: none;
}
.toast.show {
  opacity: 1;
  transform: translateY(0);
}

@media (max-width: 880px) {
  .shell { padding: 16px; }
  .topbar, .grid { grid-template-columns: 1fr; display: grid; }
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .actions, .filters, .row { flex-wrap: wrap; }
  .settings { grid-template-columns: 1fr; }
}
"""


APP_JS = """
const state = {
  summary: null,
  current: null,
  timer: null,
  assistEnabled: false,
  alertedKey: null,
  openedKey: null,
  audioCtx: null,
  decision: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.add("show");
  setTimeout(() => node.classList.remove("show"), 3200);
}

function itemKey(item) {
  if (!item) return "empty";
  return `${item.username}:${item.status}:${item.updated_at || item.imported_at || ""}`;
}

function count(name) {
  return (state.summary && state.summary.counts && state.summary.counts[name]) || 0;
}

function renderStats() {
  const summary = state.summary;
  $("dbPath").textContent = summary ? summary.db_path : "";
  const stats = [
    ["Total", summary.total],
    ["Pending", count("pending")],
    ["Snoozed", count("snoozed")],
    ["Cancelled", count("cancelled")],
    ["Skipped", count("skipped")],
    ["Not found", count("not_found")],
  ];
  $("stats").innerHTML = stats.map(([label, value]) => `
    <div class="stat"><strong>${value}</strong><span>${label}</span></div>
  `).join("");
}

function renderProgress() {
  const summary = state.summary;
  if (!summary) return;
  const eta = summary.eta;
  const total = summary.total || 0;
  const completed = count("cancelled") + count("skipped") + count("not_found");
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  $("progressText").textContent = `${completed} of ${total} completed`;
  $("progressFill").style.width = `${pct}%`;
  $("progressFill").classList.toggle("complete", pct >= 100);

  if (eta) {
    const paceLabel = eta.at_current_pace ? "at current pace" : "estimated from settings";
    $("etaText").textContent = eta.remaining === 0
      ? "All done!"
      : `ETA: ${eta.estimated_human} (${paceLabel})`;
    $("progressSubtext").textContent =
      `${eta.completed_today} cancelled today · ${eta.remaining} remaining · avg ${formatInterval(eta.avg_cancellation_interval_seconds)} between cancels`;
  } else {
    $("etaText").textContent = "";
    $("progressSubtext").textContent = "";
  }
}

function formatInterval(seconds) {
  if (!seconds || seconds <= 0) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
}

function renderPacing() {
  const pacing = state.summary.pacing;
  const badge = $("paceState");
  badge.classList.toggle("warn", !pacing.allowed);
  if (pacing.allowed) {
    badge.textContent = `Ready (${pacing.actions_last_hour}/hour, ${pacing.actions_today}/day)`;
    return;
  }
  const wait = pacing.next_allowed_at ? formatCountdown(pacing.next_allowed_at) : pacing.reason;
  badge.textContent = `${labelReason(pacing.reason)}: ${wait}`;
}

function renderCooldownTimer() {
  const pacing = state.summary.pacing;
  let existing = document.getElementById("cooldownTimer");
  if (pacing.allowed) {
    if (existing) existing.remove();
    return;
  }
  if (!pacing.next_allowed_at) return;
  if (!existing) {
    existing = document.createElement("div");
    existing.id = "cooldownTimer";
    existing.className = "cooldownTimer";
    const currentItemEl = $("currentItem");
    currentItemEl.parentNode.insertBefore(existing, currentItemEl.nextSibling);
  }
  const wait = formatCountdown(pacing.next_allowed_at);
  existing.innerHTML = `<span class="timerDot"></span> Next action in <strong>${wait}</strong>`;
}

function labelReason(reason) {
  return {
    cooldown: "Cooldown",
    hourly_limit: "Hourly limit",
    daily_limit: "Daily limit",
    ready: "Ready",
  }[reason] || reason;
}

function formatCountdown(iso) {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "now";
  const total = Math.ceil(ms / 1000);
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  if (mins >= 60) {
    const hours = Math.floor(mins / 60);
    return `${hours}h ${mins % 60}m`;
  }
  return `${mins}m ${secs}s`;
}

function renderCurrent() {
  const item = state.current;
  const pacing = state.summary.pacing;
  const decision = state.decision;
  if (!item) {
    $("currentItem").innerHTML = `<p class="muted">No pending requests in the active queue.</p>`;
    return;
  }
  const disabled = pacing.allowed ? "" : "disabled";
  const openControl = pacing.allowed
    ? `<a class="button" href="${item.profile_url}" target="_blank" rel="noreferrer">Open Profile</a>`
    : `<button class="button" disabled type="button">Open Profile</button>`;
  $("currentItem").innerHTML = `
    <div>
      <div class="profileName">@${escapeHtml(item.username)}</div>
      <a class="profileUrl" href="${item.profile_url}" target="_blank" rel="noreferrer">${item.profile_url}</a>
    </div>
    <div class="queueMeta">
      Requested: ${escapeHtml(item.requested_at || "unknown")}<br>
      Source: ${escapeHtml(item.source || "unknown")}
    </div>
    ${decision ? `<div class="decisionLine">${escapeHtml(decision.reason)}</div>` : ""}
    <div class="currentActions">
      ${openControl}
      <button class="button danger" ${disabled} data-action="cancelled">Mark Cancelled</button>
      <button class="button secondary" data-action="skipped">Skip</button>
      <button class="button secondary" data-action="snoozed">Snooze</button>
      <button class="button secondary" data-action="not_found">Not Found</button>
    </div>
  `;
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => mark(item.username, button.dataset.action));
  });
}

async function refresh() {
  const next = await api("/api/next");
  state.summary = next.summary;
  state.current = next.item;
  state.decision = null;
  renderStats();
  renderProgress();
  renderPacing();
  renderCooldownTimer();
  renderCurrent();
  fillSettings();
  await refreshQueue();
}

async function refreshQueue() {
  const status = $("statusFilter").value;
  const search = encodeURIComponent($("searchInput").value.trim());
  const payload = await api(`/api/items?status=${status}&search=${search}&limit=250`);
  $("queue").innerHTML = payload.items.map((item) => `
    <div class="queueItem">
      <div>
        <a href="${item.profile_url}" target="_blank" rel="noreferrer">@${escapeHtml(item.username)}</a>
        <div class="queueMeta">${escapeHtml(item.status)} ${item.requested_at ? " | " + escapeHtml(item.requested_at) : ""}</div>
      </div>
      <button class="button secondary" data-load="${escapeHtml(item.username)}">Use</button>
    </div>
  `).join("") || `<p class="muted">No matching rows.</p>`;
  document.querySelectorAll("[data-load]").forEach((button) => {
    button.addEventListener("click", () => {
      state.current = payload.items.find((item) => item.username === button.dataset.load);
      renderCurrent();
    });
  });
}

async function mark(username, status) {
  const body = { username, status };
  if (status === "snoozed") body.snooze_minutes = 120;
  await api("/api/mark", { method: "POST", body: JSON.stringify(body) });
  state.alertedKey = null;
  state.openedKey = null;
  toast(status === "cancelled" ? "Marked cancelled and cooldown started." : `Marked ${status}.`);
  await refresh();
}

async function importFile(file) {
  const text = await file.text();
  const payload = await api("/api/import-text", {
    method: "POST",
    body: JSON.stringify({ filename: file.name, text }),
  });
  $("importResult").textContent = formatImportStats(payload.stats);
  await refresh();
}

function formatImportStats(stats) {
  return `${stats.total} usernames: ${stats.added} added, ${stats.updated} updated, ${stats.unchanged} unchanged.`;
}

function fillSettings() {
  const settings = state.summary.settings;
  [
    "min_interval_seconds",
    "max_interval_seconds",
    "max_actions_per_hour",
    "max_actions_per_day",
    "session_break_every",
    "session_break_minutes",
    "auto_minor_decisions",
    "recent_request_snooze_days",
  ].forEach((key) => { $(key).value = settings[key] || ""; });
}

async function saveSettings() {
  const body = {};
  [
    "min_interval_seconds",
    "max_interval_seconds",
    "max_actions_per_hour",
    "max_actions_per_day",
    "session_break_every",
    "session_break_minutes",
    "auto_minor_decisions",
    "recent_request_snooze_days",
  ].forEach((key) => { body[key] = $(key).value; });
  await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
  toast("Settings saved.");
  await refresh();
}

async function refreshAdvice() {
  $("advisorText").textContent = "Working...";
  const payload = await api("/api/advice", { method: "POST", body: "{}" });
  $("advisorText").textContent = payload.text;
  if (payload.error) toast("LLM unavailable; used local advice.");
}

function setAssistStatus(message, isAlerting = false) {
  $("assistStatus").textContent = message;
  $("assistPanel").classList.toggle("alerting", isAlerting);
}

function setAssistEnabled(enabled) {
  state.assistEnabled = enabled;
  $("assistToggleBtn").textContent = enabled ? "Stop" : "Start";
  $("assistToggleBtn").classList.toggle("secondary", enabled);
  if (!enabled) {
    setAssistStatus("Idle");
    return;
  }
  state.alertedKey = null;
  state.openedKey = null;
  assistTick().catch((error) => toast(error.message));
}

function playAlertSound() {
  if (!$("soundToggle").checked) return;
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return;
  state.audioCtx = state.audioCtx || new AudioContext();
  const ctx = state.audioCtx;
  const start = ctx.currentTime;
  [0, 0.18, 0.36].forEach((offset) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, start + offset);
    gain.gain.setValueAtTime(0.0001, start + offset);
    gain.gain.exponentialRampToValueAtTime(0.12, start + offset + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.13);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(start + offset);
    osc.stop(start + offset + 0.14);
  });
}

async function assistTick() {
  if (!state.assistEnabled || !state.summary) return;
  const step = await api("/api/assist-step", {
    method: "POST",
    body: JSON.stringify({ apply_minor_decisions: true }),
  });
  state.summary = step.summary;
  state.current = step.item;
  state.decision = step.decision;
  if (step.applied_minor_decisions && step.applied_minor_decisions.length) {
    toast(`Handled ${step.applied_minor_decisions.length} minor queue decision(s).`);
    await refreshQueue();
  }
  renderStats();
  renderPacing();
  renderCurrent();

  const pacing = state.summary.pacing;
  const item = state.current;
  if (!item) {
    setAssistStatus("Queue is empty. Import pending requests or switch filters.");
    return;
  }
  if (!pacing.allowed) {
    const wait = pacing.next_allowed_at ? formatCountdown(pacing.next_allowed_at) : pacing.reason;
    setAssistStatus(`Waiting: ${labelReason(pacing.reason)} (${wait})`);
    return;
  }

  const key = itemKey(item);
  const shouldOpen = $("autoOpenToggle").checked && state.openedKey !== key;
  if (shouldOpen) {
    const payload = await api("/api/open-next", { method: "POST", body: "{}" });
    state.openedKey = key;
    if (!payload.opened) {
      toast(`Auto-open skipped: ${payload.reason}.`);
    }
  }

  const reason = state.decision ? ` ${state.decision.reason}` : "";
  setAssistStatus(`Major decision needed: review @${item.username}, cancel if still Requested, then mark the result here.${reason}`, true);
  if (state.alertedKey !== key) {
    state.alertedKey = key;
    playAlertSound();
    refreshAdvice().catch(() => {});
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function wireEvents() {
  $("fileInput").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (file) importFile(file).catch((error) => toast(error.message));
  });
  $("pathImportBtn").addEventListener("click", async () => {
    try {
      const payload = await api("/api/import-path", {
        method: "POST",
        body: JSON.stringify({ path: $("pathInput").value }),
      });
      $("importResult").textContent = formatImportStats(payload.stats);
      await refresh();
    } catch (error) {
      toast(error.message);
    }
  });
  $("statusFilter").addEventListener("change", () => refreshQueue().catch((error) => toast(error.message)));
  $("searchInput").addEventListener("input", () => refreshQueue().catch((error) => toast(error.message)));
  $("saveSettingsBtn").addEventListener("click", () => saveSettings().catch((error) => toast(error.message)));
  $("clearCooldownBtn").addEventListener("click", async () => {
    await api("/api/clear-cooldown", { method: "POST", body: "{}" });
    toast("Cooldown cleared.");
    await refresh();
  });
  $("backupBtn").addEventListener("click", async () => {
    const payload = await api("/api/backup", { method: "POST", body: "{}" });
    toast(payload.path ? `Backup: ${payload.path}` : "No database to back up.");
  });
  $("openNextBtn").addEventListener("click", async () => {
    const payload = await api("/api/open-next", { method: "POST", body: "{}" });
    if (payload.opened) {
      toast(`Opened @${payload.username}.`);
    } else if (payload.next_allowed_at) {
      toast(`Wait until ${payload.next_allowed_at}.`);
    } else {
      toast(`Not opened: ${payload.reason}.`);
    }
  });
  $("advisorBtn").addEventListener("click", () => refreshAdvice().catch((error) => {
    $("advisorText").textContent = "Advisor unavailable.";
    toast(error.message);
  }));
  $("assistToggleBtn").addEventListener("click", () => setAssistEnabled(!state.assistEnabled));
  $("beepTestBtn").addEventListener("click", () => {
    playAlertSound();
    setAssistStatus("Alert test: sound and visual pulse triggered.", true);
    setTimeout(() => {
      if (!state.assistEnabled) setAssistStatus("Idle");
    }, 2500);
  });
}

wireEvents();
refresh().catch((error) => toast(error.message));
state.timer = setInterval(() => {
  if (!state.summary) return;
  renderPacing();
  renderCooldownTimer();
  renderProgress();
  const pacing = state.summary.pacing;
  if (!pacing.allowed && pacing.next_allowed_at && new Date(pacing.next_allowed_at).getTime() <= Date.now()) {
    refresh().catch((error) => toast(error.message));
  }
  assistTick().catch((error) => toast(error.message));
}, 1000);
"""
