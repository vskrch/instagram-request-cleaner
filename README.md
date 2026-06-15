# IG Request Cleaner

Local utility for reviewing pending Instagram follow requests and tracking which ones you manually cancel.

This tool does not log in to Instagram, scrape Instagram, click buttons, or bypass rate limits. It imports a file you provide, opens profile links for manual review, records your decisions, and enforces conservative pacing between manual "cancelled" confirmations.

## Why it exists

Instagram does not provide a bulk cancel flow for old pending follow requests. This app gives you a durable local queue so you can work through an exported list without losing progress.

## Install

Requires Python 3.11 or newer.

```bash
cd /Users/venkatasai/Desktop/codex/instagram-request-cleaner
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## Quick start

```bash
ig-request-cleaner init
ig-request-cleaner import samples/pending_requests.sample.json
ig-request-cleaner serve --open
```

The web console defaults to:

```text
http://127.0.0.1:8765
```

## Assist Mode

Assist Mode automates the local workflow:

- watches the queue and pacing state
- applies reversible minor decisions locally
- auto-snoozes recent requests so you are not interrupted too early
- prioritizes old or unknown-age requests for review
- waits through cooldowns
- optionally opens the next Instagram profile when it is safe to review
- refreshes local/LLM advice
- blinks and plays a short beep when human action is needed
- waits for you to mark `Cancelled`, `Skip`, `Snooze`, or `Not Found`

The remaining human step is intentional: you inspect Instagram and make the actual relationship change yourself. The tool handles queue decisions and pacing, but it never clicks Instagram buttons, reads your Instagram session, or tries to work around platform friction.

## Input formats

Supported:

- Instagram export JSON using `relationships_follow_requests_sent`
- JSON list of usernames or profile URLs
- JSON objects with fields like `username`, `handle`, `title`, `value`, `href`, or `timestamp`
- CSV with a `username` column, or first-column usernames
- TXT with one username or profile URL per line

Example:

```json
[
  "@person_one",
  "https://www.instagram.com/person_two/",
  { "username": "person_three", "requested_at": "2024-01-01T00:00:00Z" }
]
```

## CLI

```bash
ig-request-cleaner status
ig-request-cleaner plan
ig-request-cleaner advice
ig-request-cleaner open-next
ig-request-cleaner export --format csv --output export.csv
ig-request-cleaner backup
ig-request-cleaner doctor
```

Use a custom state file:

```bash
ig-request-cleaner --db ./data/state.sqlite3 serve
```

Or:

```bash
export IGRC_DB=/absolute/path/to/state.sqlite3
```

## Browser assist

The `open-next` command and the dashboard `Open Next` button open the next queued profile in your default browser only when pacing allows it. They do not inspect, scrape, or control Instagram.

After you manually cancel a request in Instagram, return to the dashboard and click `Mark Cancelled`. That starts the cooldown and blocks the next open/cancel workflow until the next allowed time.

## Decision engine

The app makes minor local decisions by default:

- requests newer than `recent_request_snooze_days` are auto-snoozed
- old requests are prioritized
- unknown-age requests are surfaced for human review
- duplicate confirmations do not create duplicate action events

Inspect the next decision from the CLI:

```bash
ig-request-cleaner plan
```

Disable local minor decisions by setting `Minor decisions` to `Manual` in the dashboard settings.

## Optional LLM advisor

The advisor can use a local heuristic or an OpenAI-compatible chat-completions endpoint. It never receives credentials from this app, and usernames are masked by default unless you opt in.

Local-only mode is the default:

```bash
ig-request-cleaner advice
```

NVIDIA NIM mode:

```bash
export IGRC_LLM_PROVIDER=nim
export NVIDIA_API_KEY=nvapi-your-key
export IGRC_LLM_MODEL=nvidia/nemotron-3-nano-30b-a3b
ig-request-cleaner advice
```

NVIDIA documents NIM LLM as OpenAI-compatible and exposes chat completions at `https://integrate.api.nvidia.com/v1/chat/completions`.

Ollama or another local OpenAI-compatible server:

```bash
export IGRC_LLM_PROVIDER=ollama
export IGRC_LLM_MODEL=llama3.1
ig-request-cleaner advice
```

Any OpenAI-compatible endpoint:

```bash
export IGRC_LLM_PROVIDER=openai-compatible
export IGRC_LLM_BASE_URL=http://127.0.0.1:1234/v1
export IGRC_LLM_MODEL=your-model
export IGRC_LLM_API_KEY=optional-key
```

Send actual usernames to the configured LLM only if you intentionally enable it:

```bash
export IGRC_LLM_SHARE_USERNAMES=true
```

## Pacing defaults

The defaults are intentionally conservative:

- 7 to 16 minutes between manual cancellation confirmations
- 8 cancellations per hour
- 60 cancellations per day
- 45 minute break after every 12 cancellations

These are local guardrails, not a guarantee that Instagram will accept every action. If Instagram shows warnings, checkpoints, login challenges, or other friction, stop and lower the limits.

## Self-healing behavior

- SQLite state is created automatically.
- WAL mode is enabled for safer local writes.
- The database is checked with `PRAGMA integrity_check` on startup.
- Corrupt state files are moved into `backups/` and a clean state file is created.
- A SQLite backup is created before every import.
- Manual backups are available from the CLI and web UI.
- Imports are idempotent and dedupe usernames case-insensitively.
- LLM failures time out and fall back to local advice.
- Browser-assist refuses to open the next profile while cooldown is active.
- Direct status changes are also blocked server-side when pacing disallows a cancellation.
- Minor queue decisions are persisted as events.
- Imports and request bodies are capped at 10 MB.
- Settings are clamped to conservative bounds instead of accepting unsafe zero-delay values.

## Production notes

Run the web server on `127.0.0.1` unless you intentionally want another machine to reach it. The import-by-path endpoint reads local files that your user account can read, so do not expose the server publicly.

The safest workflow is:

1. Import the pending request file.
2. Start Assist Mode.
3. Let it wait, open the next profile, and alert you.
4. Cancel the request manually in Instagram if it is still pending.
5. Return to the app and click `Mark Cancelled`.
6. Let Assist Mode wait for the cooldown and repeat.

## Verify

```bash
PYTHONPATH=src python -m unittest discover -s tests
python -m compileall src tests
ruff check .
python -m pip install -e .
ig-request-cleaner --version
```
