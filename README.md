<div align="center">

# 🧹 IG Request Cleaner

**A local queue & pacing assistant for clearing old Instagram follow requests — safely, slowly, and without losing progress.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-29%20passing-brightgreen?style=for-the-badge)](tests/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=for-the-badge)](https://github.com/vskrch/instagram-request-cleaner)

[Features](#-features) · [Quick Start](#-quick-start) · [Usage Guide](#-usage-guide) · [CLI Reference](#-cli-reference) · [Safety](#-safety-first)

</div>

---

## 💡 The problem

Instagram has **no bulk-cancel** for pending follow requests. If you've sent hundreds over the years, clearing them one-by-one is tedious — and doing it too fast can trigger warnings or checkpoints.

**IG Request Cleaner** gives you a durable local queue: import your list once, work through it at a safe pace, and pick up exactly where you left off.

> 🛡️ **This tool never logs into Instagram, scrapes pages, clicks buttons, or bypasses rate limits.**  
> You review profiles and cancel requests yourself. The app handles queue, pacing, and progress.

---

## ✨ Features

| | |
|---|---|
| 📥 **Multi-format import** | Instagram export JSON, CSV, TXT, or plain username lists |
| 🖥️ **Web dashboard** | Local console at `http://127.0.0.1:8765` |
| 🤖 **Assist Mode** | Auto-opens profiles, alerts you, enforces cooldowns |
| ⏱️ **Smart pacing** | Random delays, hourly/daily caps, mandatory breaks |
| 🧠 **Decision engine** | Auto-snoozes recent requests, prioritizes old ones |
| 💬 **Optional LLM advisor** | Local heuristics or OpenAI-compatible endpoints |
| 💾 **Self-healing SQLite** | WAL mode, integrity checks, automatic backups |
| 🔒 **Privacy-first** | Runs locally; usernames masked from LLM by default |

---

## 🚀 Quick Start

### 1️⃣ Clone & install

```bash
git clone https://github.com/vskrch/instagram-request-cleaner.git
cd instagram-request-cleaner

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e .
```

### 2️⃣ Initialize & import

```bash
ig-request-cleaner init
ig-request-cleaner import path/to/your/pending_requests.json
```

Try the included sample first:

```bash
ig-request-cleaner import samples/pending_requests.sample.json
```

### 3️⃣ Launch the dashboard

```bash
ig-request-cleaner serve --open
```

Your browser opens to **http://127.0.0.1:8765** — your local control center.

---

## 📖 Usage Guide

### The recommended workflow

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
│  1. Import  │ ──▶ │ 2. Start     │ ──▶ │ 3. Assist opens │ ──▶ │ 4. You cancel│
│  your list  │     │  Assist Mode │     │  next profile   │     │  on Instagram│
└─────────────┘     └──────────────┘     └─────────────────┘     └──────────────┘
                                                                          │
                     ┌──────────────┐     ┌─────────────────┐              │
                     │ 6. Cooldown  │ ◀── │ 5. Mark         │ ◀────────────┘
                     │    & repeat  │     │  Cancelled      │
                     └──────────────┘     └─────────────────┘
```

**Step by step:**

1. **Export** your pending follow requests from Instagram (or prepare a username list).
2. **Import** the file into IG Request Cleaner.
3. **Start Assist Mode** in the dashboard — it watches the queue and pacing state.
4. When alerted (blink + beep), **open the profile** and check if the request is still pending.
5. **Cancel manually** on Instagram — the app does not click for you.
6. Return to the dashboard and click **Mark Cancelled** (or Skip / Snooze / Not Found).
7. **Wait through the cooldown** — Assist Mode opens the next profile when it's safe.
8. Repeat until your queue is clear. Progress is saved after every action.

### Dashboard actions

| Button | What it does |
|--------|--------------|
| **Open Next** | Opens the next queued profile in your browser (only when pacing allows) |
| **Mark Cancelled** | Records a cancellation and starts the cooldown timer |
| **Skip** | Moves on without cancelling — request stays pending |
| **Snooze** | Defers review to a later time |
| **Not Found** | Profile unavailable or request already gone |
| **Assist Mode** | Hands-free loop: wait → open → alert → wait for your decision |

### Getting your pending-request list

Supported input formats:

- 📦 Instagram export JSON (`relationships_follow_requests_sent`)
- 📄 JSON array of usernames or profile URLs
- 📊 CSV with a `username` column (or first-column usernames)
- 📝 TXT with one username or URL per line

```json
[
  "@person_one",
  "https://www.instagram.com/person_two/",
  { "username": "person_three", "requested_at": "2024-01-01T00:00:00Z" }
]
```

---

## 🖥️ CLI Reference

```bash
# Core
ig-request-cleaner init                          # Create / repair local state
ig-request-cleaner import <file>                 # Import pending requests
ig-request-cleaner serve [--open]                # Start web dashboard
ig-request-cleaner status [--json]               # Queue summary

# Workflow
ig-request-cleaner plan                          # Apply minor decisions, show next item
ig-request-cleaner open-next                     # Open next profile (if pacing allows)
ig-request-cleaner advice                        # Get queue advice (local or LLM)

# Data management
ig-request-cleaner export --format csv -o out.csv
ig-request-cleaner backup
ig-request-cleaner doctor                        # Integrity & config checks
```

### Custom state location

```bash
ig-request-cleaner --db ./data/state.sqlite3 serve
# or
export IGRC_DB=/absolute/path/to/state.sqlite3
```

Default state path: `~/.ig-request-cleaner/state.sqlite3`

---

## ⏱️ Pacing & presets

Default is **Aggressive** — 2–5 min delays, 20/hour, 200/day. Fully customizable in the dashboard.

| Preset | Delay | Per hour | Per day | Break |
|--------|-------|----------|---------|-------|
| **Conservative** | 7–16 min | 8 | 60 | every 12 → 45 min |
| **Balanced** | 3–7 min | 15 | 120 | every 15 → 30 min |
| **Aggressive** (default) | 2–5 min | 20 | 200 | every 20 → 15 min |
| **Max** | 1–3 min | 40 | 500 | every 30 → 10 min |

Pick a preset or tweak every value manually — min/max delay, hourly/daily caps, break schedule, and snooze window.

> ⚠️ These are **local guardrails**, not a guarantee Instagram will accept every action.  
> If you see warnings, checkpoints, or login challenges — **stop** and lower your limits.

---

## 🧠 Optional LLM advisor

The advisor suggests what to review next using local heuristics (default) or an OpenAI-compatible endpoint. Usernames are **masked by default**.

<details>
<summary><b>🔧 LLM configuration options</b></summary>

**Local-only (default):**

```bash
ig-request-cleaner advice
```

**NVIDIA NIM:**

```bash
export IGRC_LLM_PROVIDER=nim
export NVIDIA_API_KEY=nvapi-your-key
export IGRC_LLM_MODEL=nvidia/nemotron-3-nano-30b-a3b
ig-request-cleaner advice
```

**Ollama / local server:**

```bash
export IGRC_LLM_PROVIDER=ollama
export IGRC_LLM_MODEL=llama3.1
ig-request-cleaner advice
```

**Any OpenAI-compatible endpoint:**

```bash
export IGRC_LLM_PROVIDER=openai-compatible
export IGRC_LLM_BASE_URL=http://127.0.0.1:1234/v1
export IGRC_LLM_MODEL=your-model
export IGRC_LLM_API_KEY=optional-key
```

To send real usernames to the LLM (opt-in only):

```bash
export IGRC_LLM_SHARE_USERNAMES=true
```

</details>

---

## 🛡️ Safety first

| ✅ Does | ❌ Does not |
|---------|-------------|
| Import files you provide | Log into Instagram |
| Open profile URLs in your browser | Scrape or read Instagram pages |
| Track your decisions locally | Click Instagram buttons |
| Enforce cooldowns between actions | Bypass rate limits or checkpoints |
| Back up state before imports | Store Instagram credentials |

**Production tip:** Keep the server on `127.0.0.1`. Do not expose it publicly — the import endpoint can read local files your user account can access.

---

## 🔧 Self-healing & reliability

- SQLite with WAL mode and `PRAGMA integrity_check` on startup
- Corrupt databases auto-moved to `backups/` with a fresh state created
- Backup before every import; manual backups via CLI and dashboard
- Idempotent imports with case-insensitive username deduplication
- LLM timeouts fall back to local advice
- Server-side pacing blocks unsafe cancellation bursts
- Settings clamped to safe bounds (no zero-delay values)

---

## 🧪 Development

```bash
# Run tests
PYTHONPATH=src python -m unittest discover -s tests

# Lint
ruff check .

# Verify install
pip install -e .
ig-request-cleaner --version
```

---

## 📄 License

MIT — use freely, modify freely, no warranty.

---

<div align="center">

**Built for humans who sent too many follow requests and want them gone — responsibly.** 🎯

[⭐ Star this repo](https://github.com/vskrch/instagram-request-cleaner) if it saves you from scroll-fatigue.

</div>
