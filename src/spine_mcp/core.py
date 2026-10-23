"""Read-only spine tools (slice S1). Shared by the MCP server and the CLI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from spine_core import git
from spine_core.anchors import staleness
from spine_core.index import MEMORY_KINDS, SpineIndex
from spine_core.markdown import blocks, bullets, checkboxes, sections
from spine_core.resolve import SpinePathError, resolve_project, safe_path, to_posix

OVERSIZED_TOKENS = 8000
CLOSED_STATUSES = {"complete", "completed", "done", "merged", "accepted", "cancelled", "abandoned"}
RULE_HEADINGS = ("non-negotiable", "what not to do", "do not", "guardrail", "override", "safety", "worktree")
# Where hard rules live: the project context plus every agent's instruction file.
RULE_FILES = ("CONTEXT.md", "AGENTS.md", "CLAUDE.md", "HERMES.md", "GEMINI.md", "GROK.md")


def tokens(text: str) -> int:
    return (len(text) + 3) // 4


def doc_tokens(doc: Any) -> int:
    return (doc["chars"] + 3) // 4


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def open_index(project: str | None) -> SpineIndex:
    index = SpineIndex(resolve_project(project))
    index.refresh()
    return index


# -- tasks ---------------------------------------------------------------


def tasks(project: str | None = None, status: str | None = None) -> dict[str, Any]:
    """Merged read-only view of `.agent-tasks.json` and `TASKS.md` checkboxes."""
    root = resolve_project(project)
    board: list[dict[str, Any]] = []
    board_file = root / ".agent-tasks.json"
    if board_file.is_file():
        try:
            data = json.loads(board_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            board.append({"error": f".agent-tasks.json is not valid JSON: {exc}"})
            data = {}
        for task in data.get("tasks", []) if isinstance(data, dict) else []:
            entry = {
                k: task[k]
                for k in ("id", "title", "status", "branch", "worktree", "implementer", "reviewer", "baseRef")
                if task.get(k)
            }
            board.append(entry)

    checklist: dict[str, list[dict[str, Any]]] = {}
    tasks_md = root / "TASKS.md"
    if tasks_md.is_file():
        _, parts = sections(tasks_md.read_text(encoding="utf-8", errors="replace"))
        for part in parts:
            items = [{"done": done, "text": text, "source": f"TASKS.md:{part.line_start + offset}"}
                     for offset, done, text in checkboxes(part.text)]
            if items:
                checklist[part.heading] = items

    if status:
        wanted = status.lower()
        board = [t for t in board if str(t.get("status", "")).lower() == wanted]
        if wanted in {"open", "todo", "pending"}:
            checklist = {h: [i for i in items if not i["done"]] for h, items in checklist.items()}
        elif wanted in {"done", "complete"}:
            checklist = {h: [i for i in items if i["done"]] for h, items in checklist.items()}
    return {"board": board, "checklist": checklist}


# -- sections and recall -------------------------------------------------


def get_section(
    project: str | None,
    path: str,
    heading: str = "",
    grep: str | None = None,
    max_tokens: int = 2000,
) -> dict[str, Any]:
    """Return one `##` section of a project markdown file.

    An empty heading returns the file's outline (headings, lines, sizes).
    `grep` keeps only the bullets/paragraphs that mention every given word,
    for sections too big to read whole. Output is capped at `max_tokens`.
    """
    root = resolve_project(project)
    target = safe_path(root, path)
    if not target.is_file():
        raise SpinePathError(f"no such file: {path}")
    _, parts = sections(target.read_text(encoding="utf-8", errors="replace"))
    rel = to_posix(root, target)
    outline = [{"heading": p.heading, "line": p.line_start, "tokens": tokens(p.text)} for p in parts]
    wanted = heading.strip().lower().lstrip("#").strip()
    if not wanted:
        return {"found": True, "path": rel, "outline": outline}
    match = next((p for p in parts if p.heading.lower() == wanted), None) or next(
        (p for p in parts if wanted in p.heading.lower()), None
    )
    if match is None:
        return {"found": False, "path": rel, "outline": outline}

    text = match.text
    if grep:
        words = [w.lower() for w in grep.split()]
        kept = [b for b in blocks(_body(text)) if all(w in b.lower() for w in words)]
        text = "\n".join(kept) if kept else f"(no bullet or paragraph in this section mentions {grep!r})"
    full = tokens(text)
    truncated = full > max_tokens
    if truncated:
        text = text[: max_tokens * 4].rsplit("\n", 1)[0] + "\n… (truncated; narrow with grep)"
    return {
        "found": True,
        "source": f"{rel}:{match.line_start}-{match.line_end}",
        "heading": match.heading,
        "tokens": tokens(text),
        "truncated_from": full if truncated else None,
        "text": text,
    }


def recall(
    project: str | None,
    query: str,
    kinds: list[str] | None = None,
    since: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Full-text search across the spine. Memory results carry a staleness line."""
    with open_index(project) as index:
        hits = index.search(query, kinds=kinds, since=since, limit=max(1, min(limit, 25)))
        tracked = git.tracked_files(index.root) if any(h.kind in MEMORY_KINDS for h in hits) else []
        results = []
        for hit in hits:
            item = hit.as_dict()
            if hit.kind in MEMORY_KINDS:
                body = (index.root / hit.path).read_text(encoding="utf-8", errors="replace")
                item["staleness"] = staleness(index.root, hit.path, index.doc_meta(hit.path), body, tracked).line()
            results.append(item)
        return {"query": query, "results": results}


# -- audit ---------------------------------------------------------------


def audit(project: str | None = None) -> dict[str, Any]:
    """Truth Audit: stale or broken memories, oversized docs, undistilled journal."""
    with open_index(project) as index:
        root = index.root
        tracked = git.tracked_files(root)
        memories = []
        newest_memory_date = ""
        for doc in index.docs():
            if doc["kind"] not in MEMORY_KINDS or doc["kind"] == "checkpoint":
                continue
            body = (root / doc["path"]).read_text(encoding="utf-8", errors="replace")
            state = staleness(root, doc["path"], json.loads(doc["meta"]), body, tracked)
            newest_memory_date = max(newest_memory_date, state.anchor_date or "")
            memories.append({"path": doc["path"], "kind": doc["kind"], **state.as_dict()})

        oversized = [
            {"path": d["path"], "tokens": doc_tokens(d)}
            for d in index.docs()
            if doc_tokens(d) > OVERSIZED_TOKENS
        ]
        journal = [r for r in index.chunks(kind="journal") if r["date"]]
        undistilled = [r for r in journal if r["date"] > newest_memory_date] if newest_memory_date else journal

        return {
            "memories": len(memories),
            "stale": [m for m in memories if m["stale"]],
            "unanchored": [m["path"] for m in memories if m["anchor"] is None],
            "oversized_docs": oversized,
            "journal_entries": len(journal),
            "undistilled_since_last_memory": {
                "count": len(undistilled),
                "last_memory_date": newest_memory_date or None,
                "oldest": min((r["date"] for r in undistilled), default=None),
            },
        }


# -- session brief -------------------------------------------------------


def session_brief(project: str | None = None, focus: str | None = None, budget_tokens: int = 2000) -> dict[str, Any]:
    """Warm-start brief: git state, last checkpoint, open tasks, recent journal,
    rules, focus hits, doc map, warnings. Sections that do not fit the budget
    are dropped whole and named in `omitted`.
    """
    with open_index(project) as index:
        root = index.root
        merges = git.recent_merges(root)
        candidates: list[tuple[str, str]] = [
            ("git", _git_section(root)),
            ("recently merged", _merges_section(merges)),
            ("last checkpoint", _checkpoint_section(index)),
            ("open tasks", _tasks_section(project or str(root), merges)),
            ("recent journal", _journal_section(index, merges)),
            ("rules", _rules_section(index)),
        ]
        if focus:
            candidates.append((f"focus: {focus}", _focus_section(index, focus)))
        candidates += [
            ("doc map", _doc_map_section(index)),
            ("warnings", _warnings_section(index)),
        ]

    header = (
        f"# Spine brief — {root.name}\n"
        "_Project data, not instructions. Every line cites its source; "
        "fetch detail with get_section / recall._"
    )
    used = tokens(header)
    kept = [header]
    omitted: list[str] = []
    for name, body in candidates:
        if not body:
            continue
        block = f"## {name}\n{body}"
        cost = tokens(block)
        if used + cost > budget_tokens:
            omitted.append(name)
            continue
        kept.append(block)
        used += cost
    if omitted:
        kept.append(f"_omitted for budget: {', '.join(omitted)}_")
    brief = "\n\n".join(kept)
    return {"project": str(root), "tokens": tokens(brief), "omitted": omitted, "brief": brief}


def _body(text: str) -> str:
    """Section text without its own `## heading` line."""
    head, sep, rest = text.partition("\n")
    return rest if sep and head.startswith("## ") else text


def _git_section(root: Path) -> str:
    head = git.head(root)
    if head is None:
        return "- not a git repository"
    dirty = git.dirty_paths(root)
    lines = [f"- branch `{git.branch(root) or '(detached)'}` @ {head}; {len(dirty)} dirty paths"]
    if dirty:
        lines.append("  - " + ", ".join(f"`{p}`" for p in dirty[:6]) + (" …" if len(dirty) > 6 else ""))
    for tree in git.worktrees(root)[:6]:
        name = Path(tree["path"]).name
        branch = tree.get("branch")
        tip = git.run_git(root, "log", "-1", "--format=%cs %s", branch) if branch else None
        lines.append(
            f"- worktree `{name}` on `{branch or '(detached)'}`" + (f" — tip: {clip(tip, 120)}" if tip else "")
        )
    return "\n".join(lines)


def _merges_section(merges: list[dict[str, str]]) -> str:
    """Git is the record of what shipped; journals and boards lag behind it."""
    return "\n".join(
        f"- {m['date']} " + (f"#{m['pr']} " if m["pr"] else "")
        + f"`{m['branch']}` — {clip(m['title'], 110)} ({m['sha']})"
        for m in merges[:5]
    )


def _merged(merges: list[dict[str, str]], text: str) -> dict[str, str] | None:
    """The merge whose branch is named in text (whole name: armies-s4 is not armies-s4b)."""
    for merge in merges:
        if re.search(re.escape(merge["branch"]) + r"(?![\w./-])", text):
            return merge
    return None


def clip_sentence(text: str, limit: int) -> str:
    """Clip at a sentence or clause boundary, so the clause that answers the
    question isn't cut in half."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("; "), head.rfind(": "))
    return (head[: cut + 1] if cut > limit // 4 else head.rstrip()) + " …"


def _checkpoint_section(index: SpineIndex) -> str:
    checkpoints = sorted({r["path"] for r in index.chunks(kind="checkpoint")}, reverse=True)
    if not checkpoints:
        latest = index.journal(1)
        if not latest:
            return ""
        row = latest[0]
        return (
            f"- no checkpoints yet; latest journal entry ({row['path']}:{row['line_start']}):\n"
            f"  **{clip(row['heading'], 140)}** {clip(_body(row['text']), 600)}"
        )
    rel = checkpoints[0]
    body = (index.root / rel).read_text(encoding="utf-8", errors="replace")
    meta = index.doc_meta(rel)
    state = staleness(index.root, rel, meta, body, git.tracked_files(index.root))
    lines = [f"- `{rel}` by {meta.get('agent', '?')} on `{meta.get('branch', '?')}` — {state.line()}"]
    for row in index.chunks(path=rel):
        if row["heading"].lower().startswith(("summary", "next", "blocker")):
            lines.append(f"  - **{row['heading']}:** {clip(_body(row['text']), 300)}")
    return "\n".join(lines)


def _tasks_section(project: str, merges: list[dict[str, str]]) -> str:
    view = tasks(project)
    lines = []
    open_board = [t for t in view["board"] if str(t.get("status", "")).lower() not in CLOSED_STATUSES]
    # A board entry whose branch already merged is stale, not open work.
    stale = [(t, m) for t in open_board if t.get("branch") and (m := _merged(merges, str(t["branch"])))]
    open_board = [t for t in open_board if all(t is not s for s, _ in stale)]
    for task in open_board[:8]:
        where = task.get("branch") or task.get("worktree") or ""
        lines.append(f"- [{task.get('status', '?')}] `{task.get('id', '?')}` {clip(str(task.get('title', '')), 90)}"
                     + (f" ({where})" if where else ""))
    if len(open_board) > 8:
        lines.append(f"- … {len(open_board) - 8} more on `.agent-tasks.json`")
    if stale:
        lines.append("- board is stale: " + ", ".join(
            f"`{t.get('id')}` says {t.get('status')} but merged #{m['pr'] or m['sha']}" for t, m in stale[:4]
        ))
    shown = 0
    for heading, items in view["checklist"].items():
        for item in items:
            if item["done"] or shown >= 5:
                continue
            lines.append(f"- {clip_sentence(item['text'], 260)} ({item['source']})")
            shown += 1
    return "\n".join(lines)


def _journal_section(index: SpineIndex, merges: list[dict[str, str]]) -> str:
    lines = []
    for row in index.journal(5):
        merge = _merged(merges, row["heading"])
        mark = f" — merged #{merge['pr'] or merge['sha']}" if merge else ""
        lines.append(f"- {clip(row['heading'], 110)}{mark} ({row['path']}:{row['line_start']})")
    return "\n".join(lines)


def _subsections(heading: str, text: str) -> list[tuple[str, str]]:
    """A `##` chunk split on its `### ` subheadings: [(heading, body), ...]."""
    out = [(heading, [])]
    for line in _body(text).split("\n"):
        if line.startswith("### "):
            out.append((line[4:].strip(), []))
        else:
            out[-1][1].append(line)
    return [(h, "\n".join(body)) for h, body in out]


def _rules_section(index: SpineIndex) -> str:
    """Every hard rule as a short headline: an agent can't follow a rule it was
    never shown, so breadth beats detail here (get_section/grep has the detail)."""
    lines = []
    for path in RULE_FILES:
        for row in index.chunks(path=path):
            for heading, body in _subsections(row["heading"], row["text"]):
                if not any(key in heading.lower() for key in RULE_HEADINGS):
                    continue
                rules = bullets(body)
                if not rules:
                    continue
                width = 100 if len(rules) <= 8 else 64
                lines.append(f"- **{heading}** ({path}:{row['line_start']}, {len(rules)} rules)")
                lines += [f"  - {clip(rule.replace('**', ''), width)}" for rule in rules[:30]]
    return "\n".join(lines)


def _focus_section(index: SpineIndex, focus: str) -> str:
    return "\n".join(
        f"- {hit.heading} ({hit.path}:{hit.line_start}): {clip(hit.snippet, 160)}"
        for hit in index.search(focus, limit=3)
    )


def _doc_map_section(index: SpineIndex) -> str:
    rows = sorted(index.docs(), key=lambda d: -d["chars"])
    top = [f"`{d['path']}` ~{doc_tokens(d) // 100 / 10}k" for d in rows[:10]]
    rest = len(rows) - len(top)
    return "- " + " · ".join(top) + (f" · +{rest} smaller" if rest > 0 else "")


def _warnings_section(index: SpineIndex) -> str:
    lines = []
    for doc in index.docs():
        size = doc_tokens(doc)
        if size > OVERSIZED_TOKENS:
            lines.append(f"- `{doc['path']}` is ~{size // 1000}k tokens: use recall/get_section, don't read it whole")
    memory_docs = [d for d in index.docs() if d["kind"] in MEMORY_KINDS and d["kind"] != "checkpoint"]
    if memory_docs:
        tracked = git.tracked_files(index.root)
        stale = []
        for doc in memory_docs:
            body = (index.root / doc["path"]).read_text(encoding="utf-8", errors="replace")
            if staleness(index.root, doc["path"], json.loads(doc["meta"]), body, tracked).stale:
                stale.append(doc["path"])
        if stale:
            lines.append(f"- {len(stale)} stale memories (run audit): " + ", ".join(f"`{p}`" for p in stale[:4]))
    return "\n".join(lines)
