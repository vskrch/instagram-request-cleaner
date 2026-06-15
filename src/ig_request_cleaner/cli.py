from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .browser_assist import open_next_profile
from .db import Store
from .importer import ImportErrorWithContext, load_candidates
from .llm import LLMAdvisor
from .server import run_server


def default_db_path() -> Path:
    configured = os.environ.get("IGRC_DB")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".ig-request-cleaner" / "state.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ig-request-cleaner",
        description="Manage a local manual queue for pending Instagram follow requests.",
    )
    parser.add_argument("--db", default=str(default_db_path()), help="SQLite state path.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create or repair the local state database.")

    import_parser = subparsers.add_parser("import", help="Import pending requests from JSON/CSV/TXT.")
    import_parser.add_argument("path", help="Input file path.")

    serve_parser = subparsers.add_parser("serve", help="Start the localhost web console.")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--open", action="store_true", help="Open the browser automatically.")

    status_parser = subparsers.add_parser("status", help="Print queue status.")
    status_parser.add_argument("--json", action="store_true", help="Print full JSON summary.")

    export_parser = subparsers.add_parser("export", help="Export queue state.")
    export_parser.add_argument("--format", choices=["json", "csv"], default="json")
    export_parser.add_argument("--output", "-o", help="Output file. Defaults to stdout.")

    backup_parser = subparsers.add_parser("backup", help="Create a SQLite backup.")
    backup_parser.add_argument("--reason", default="manual")

    subparsers.add_parser("advice", help="Print local or configured LLM queue advice.")
    subparsers.add_parser("plan", help="Apply minor decisions and print the next major review item.")

    open_parser = subparsers.add_parser(
        "open-next",
        help="Open the next pending profile in your browser when pacing allows.",
    )
    open_parser.add_argument("--browser", help="Optional webbrowser controller name.")

    subparsers.add_parser("doctor", help="Run integrity and configuration checks.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = Store(args.db)

    try:
        if args.command == "init":
            store.initialize()
            print(f"Initialized state at {store.db_path}")
            return 0

        if args.command == "import":
            candidates = load_candidates(args.path)
            stats = store.import_candidates(candidates, source_path=args.path)
            print(
                "Imported {total} usernames: {added} added, {updated} updated, "
                "{unchanged} unchanged.".format(**stats)
            )
            return 0

        if args.command == "serve":
            store.initialize()
            run_server(
                db_path=store.db_path,
                host=args.host,
                port=args.port,
                open_browser=args.open,
            )
            return 0

        if args.command == "status":
            summary = store.summary()
            if args.json:
                print(store.export_json())
            else:
                print(f"State: {summary['db_path']}")
                print(f"Total: {summary['total']}")
                for status, count in sorted(summary["counts"].items()):
                    print(f"{status}: {count}")
                pacing = summary["pacing"]
                print(f"Pacing: {pacing['reason']}")
                if pacing["next_allowed_at"]:
                    print(f"Next allowed at: {pacing['next_allowed_at']}")
            return 0

        if args.command == "export":
            content = store.export_csv() if args.format == "csv" else store.export_json()
            if args.output:
                Path(args.output).expanduser().write_text(content, encoding="utf-8")
                print(f"Wrote {args.output}")
            else:
                print(content)
            return 0

        if args.command == "backup":
            path = store.backup(args.reason)
            print(path or "No database exists yet.")
            return 0

        if args.command == "advice":
            result = LLMAdvisor().advise(
                summary=store.summary(),
                current=store.next_pending(),
                queue=store.list_requests(status="pending", limit=20),
            )
            print(f"provider: {result.provider or 'local'}")
            if result.model:
                print(f"model: {result.model}")
            if result.error:
                print(f"llm_error: {result.error}")
            print()
            print(result.text)
            return 0

        if args.command == "plan":
            step = store.assist_step()
            applied = step["applied_minor_decisions"]
            if applied:
                print(f"Applied {len(applied)} minor decision(s):")
                for item in applied:
                    print(f"- @{item['username']}: {item['reason']}")
                print()
            if not step["item"]:
                print("No major human decision is currently needed.")
                return 0
            item = step["item"]
            decision = step["decision"]
            print(f"Next major decision: @{item['username']}")
            print(f"Profile: {item['profile_url']}")
            print(f"Reason: {decision['reason']}")
            return 0

        if args.command == "open-next":
            result = open_next_profile(store, browser=args.browser)
            if result.opened:
                print(f"Opened @{result.username}: {result.url}")
                return 0
            if result.next_allowed_at:
                print(f"Not opened: {result.reason}. Next allowed at {result.next_allowed_at}.")
            else:
                print(f"Not opened: {result.reason}.")
            return 1

        if args.command == "doctor":
            health = store.health()
            print(f"ok: {health['ok']}")
            print(f"db_path: {health['db_path']}")
            print(f"backup_dir: {health['backup_dir']}")
            print(f"total: {health['summary']['total']}")
            return 0

    except (ImportErrorWithContext, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130

    parser.error(f"Unknown command: {args.command}")
    return 2
