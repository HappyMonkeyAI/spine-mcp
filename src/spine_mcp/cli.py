"""`spine` CLI: the same code path as the MCP tools, for hooks and agents without MCP."""

from __future__ import annotations

import argparse
import json
import sys

from spine_core.index import SpineIndex
from spine_core.resolve import SpinePathError, resolve_project

from . import core, writes


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="spine", description="Warm-start project context")
    parser.add_argument("--project", "-p", help="project path or registry name (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    brief = sub.add_parser("brief", help="print the session brief (SessionStart hook)")
    brief.add_argument("--focus")
    brief.add_argument("--budget", type=int, default=2000)
    brief.add_argument("--hook", action="store_true",
                       help="hook mode: read the hook's JSON on stdin, record the session start, never fail")

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

    cp = sub.add_parser("checkpoint", help="record where this session got to")
    cp.add_argument("--agent", required=True, help="who is writing: claude-code, codex, grok, hermes, ...")
    cp.add_argument("--summary", required=True)
    cp.add_argument("--done", action="append")
    cp.add_argument("--next", action="append")
    cp.add_argument("--blocker", action="append", dest="blockers")
    cp.add_argument("--decision", action="append", dest="decisions")
    cp.add_argument("--path", action="append", dest="paths")
    cp.add_argument("--verify", action="append", default=[], metavar="V0=pass")
    cp.add_argument("--task-id")

    mem = sub.add_parser("remember", help="record a durable insight, decision or lesson")
    mem.add_argument("--agent", required=True)
    mem.add_argument("--kind", required=True, choices=["insight", "decision", "lesson"])
    mem.add_argument("--title", required=True)
    mem.add_argument("--body", required=True)
    mem.add_argument("--path", action="append", dest="paths")
    mem.add_argument("--tag", action="append", dest="tags")
    mem.add_argument("--update-of")
    mem.add_argument("--force-new", action="store_true")

    dis = sub.add_parser("distill", help="journal/checkpoints not yet distilled into memories (JSON)")
    dis.add_argument("--since")
    dis.add_argument("--limit", type=int, default=15)
    dis.add_argument("--done-through", metavar="YYYY-MM-DD", help="mark the journal distilled up to this date")

    sub.add_parser("nudge", help="Stop hook: one-time checkpoint reminder (reads hook JSON on stdin)")

    args = parser.parse_args(argv)
    try:
        if args.command == "brief":
            if args.hook:
                event = _hook_input()
                if event.get("session_id"):
                    writes.start_session(args.project, event["session_id"])
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
        elif args.command == "checkpoint":
            verification = dict(v.split("=", 1) for v in args.verify if "=" in v)
            _json(writes.checkpoint(
                args.project, args.agent, args.summary, done=args.done, next=args.next,
                blockers=args.blockers, decisions=args.decisions, paths=args.paths,
                verification=verification, task_id=args.task_id,
            ))
        elif args.command == "remember":
            _json(writes.record_memory(
                args.project, args.agent, args.kind, args.title, args.body, paths=args.paths,
                tags=args.tags, update_of=args.update_of, force_new=args.force_new,
            ))
        elif args.command == "distill":
            _json(writes.distill(args.project, since=args.since, limit=args.limit, done_through=args.done_through))
        elif args.command == "nudge":
            # A Stop hook must never break the session: any failure means "no nudge".
            try:
                event = _hook_input()
                reason = writes.nudge(args.project or event.get("cwd"), event.get("session_id", ""),
                                      bool(event.get("stop_hook_active")))
            except Exception:
                reason = None
            if reason:
                _json({"decision": "block", "reason": reason})
    except Exception as exc:
        # A hook must never block session start.
        if getattr(args, "hook", False):
            return 0
        if not isinstance(exc, (SpinePathError, writes.SpineWriteError, OSError)):
            raise
        print(f"spine: {exc}", file=sys.stderr)
        return 1
    return 0


def _hook_input() -> dict:
    """The JSON a Claude Code hook receives on stdin; empty when run by hand."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
