"""`spine` CLI: the same code path as the MCP tools, for hooks and agents without MCP."""

from __future__ import annotations

import argparse
import json
import sys

from spine_core.index import SpineIndex
from spine_core.resolve import SpinePathError, resolve_project

from . import core


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="spine", description="Warm-start project context")
    parser.add_argument("--project", "-p", help="project path or registry name (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    brief = sub.add_parser("brief", help="print the session brief (SessionStart hook)")
    brief.add_argument("--focus")
    brief.add_argument("--budget", type=int, default=2000)
    brief.add_argument("--hook", action="store_true", help="never fail: print nothing on error")

    section = sub.add_parser("section", help="print one ## section of a file (no heading: outline)")
    section.add_argument("path")
    section.add_argument("heading", nargs="?", default="")
    section.add_argument("--grep", help="keep only bullets/paragraphs mentioning every word")
    section.add_argument("--max-tokens", type=int, default=2000)

    rec = sub.add_parser("recall", help="search the spine")
    rec.add_argument("query")
    rec.add_argument("--kind", action="append", dest="kinds")
    rec.add_argument("--since")
    rec.add_argument("--limit", type=int, default=8)

    sub.add_parser("tasks", help="merged task view (JSON)")
    sub.add_parser("audit", help="truth audit (JSON)")
    sub.add_parser("reindex", help="drop and rebuild the index")

    args = parser.parse_args(argv)
    try:
        if args.command == "brief":
            print(core.session_brief(args.project, focus=args.focus, budget_tokens=args.budget)["brief"])
        elif args.command == "section":
            result = core.get_section(
                args.project, args.path, args.heading, grep=args.grep, max_tokens=args.max_tokens
            )
            if result["found"] and "text" in result:
                print(result["text"])
            else:
                _json(result)
        elif args.command == "recall":
            _json(core.recall(args.project, args.query, kinds=args.kinds, since=args.since, limit=args.limit))
        elif args.command == "tasks":
            _json(core.tasks(args.project))
        elif args.command == "audit":
            _json(core.audit(args.project))
        elif args.command == "reindex":
            with SpineIndex(resolve_project(args.project)) as index:
                _json(index.rebuild())
    except Exception as exc:
        # A hook must never block session start.
        if getattr(args, "hook", False):
            return 0
        if not isinstance(exc, (SpinePathError, OSError)):
            raise
        print(f"spine: {exc}", file=sys.stderr)
        return 1
    return 0


def _json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
