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
# Substrings: "rule" covers Architectural/Operating/Non-negotiable rules; "boundar" covers boundaries.
RULE_HEADINGS = ("non-negotiable", "what not to do", "do not", "guardrail", "override", "safety", "worktree",
                 "rule", "workflow", "boundar", "invariant")
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
    # Case-insensitive: `tasks.md` is as common as `TASKS.md`, and Linux hosts care.
    tasks_md = next((p for p in sorted(root.glob("*.md")) if p.name.lower() == "tasks.md"), root / "TASKS.md")
    if tasks_md.is_file():
        _, parts = sections(tasks_md.read_text(encoding="utf-8", errors="replace"))
        for part in parts:
            items = [{"done": done, "text": text, "source": f"{tasks_md.name}:{part.line_start + offset}"}
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

    `path` may be `adr:0008` for the ADR numbered 0008. An empty heading returns
    the file's outline (headings, lines, sizes), or with `grep`, the matching
    bullets/paragraphs from the whole file. `grep` keeps only blocks that mention
    every given word, for sections too big to read whole. Output is capped at `max_tokens`.
    """
    root = resolve_project(project)
    adr = re.fullmatch(r"(?i)adr[:\s-]*(\d+)", path.strip())
    if adr:
        matches = sorted((root / "docs" / "adr").glob(f"{adr.group(1).zfill(4)}*.md"))
        if len(matches) != 1:
            return {"found": False, "path": path,
                    "candidates": [to_posix(root, m) for m in matches] or "no ADR with that number in docs/adr/"}
        path = to_posix(root, matches[0])
    target = safe_path(root, path)
    if not target.is_file():
        raise SpinePathError(f"no such file: {path}")
    _, parts = sections(target.read_text(encoding="utf-8", errors="replace"))
    rel = to_posix(root, target)
    outline = [{"heading": p.heading, "line": p.line_start, "tokens": tokens(p.text)} for p in parts]
    wanted = heading.strip().lower().lstrip("#").strip()
    if not wanted and grep:
        words = [w.lower() for w in grep.split()]
        hits = [
            f"[{rel}:{part.line_start} {part.heading}] {block}"
            for part in parts for block in blocks(_body(part.text))
            if all(w in block.lower() for w in words)
        ]
        text = "\n".join(hits) or f"(nothing in {rel} mentions {grep!r})"
        full = tokens(text)
        if full > max_tokens:
            text = text[: max_tokens * 4].rsplit("\n", 1)[0] + "\n… (truncated; narrow the grep)"
        return {"found": bool(hits), "source": rel, "matches": len(hits), "tokens": tokens(text),
                "truncated_from": full if full > max_tokens else None, "text": text}
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
    path: str | None = None,
) -> dict[str, Any]:
    """Full-text search across the spine; `path` limits it to a prefix such as
    `apps/xeno-tactical`. Each source points at the best-matching line. Memory
    results carry a staleness line."""
    with open_index(project) as index:
        hits = index.search(query, kinds=kinds, since=since, limit=max(1, min(limit, 25)), path=path)
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
        for doc in index.docs():
            if doc["kind"] not in MEMORY_KINDS or doc["kind"] == "checkpoint":
                continue
            body = (root / doc["path"]).read_text(encoding="utf-8", errors="replace")
            state = staleness(root, doc["path"], json.loads(doc["meta"]), body, tracked)
            memories.append({"path": doc["path"], "kind": doc["kind"], **state.as_dict()})

        oversized = [
            {"path": d["path"], "tokens": doc_tokens(d)}
            for d in index.docs()
            if doc_tokens(d) > OVERSIZED_TOKENS
        ]
        journal = [r for r in index.chunks(kind="journal") if r["date"]]
        cutoff = distilled_through(root)
        undistilled = [r for r in journal if r["date"] > cutoff] if cutoff else journal

        return {
            "memories": len(memories),
            "stale": [m for m in memories if m["stale"]],
            "unanchored": [m["path"] for m in memories if m["anchor"] is None],
            "oversized_docs": oversized,
            "journal_entries": len(journal),
            "undistilled": {
                "count": len(undistilled),
                "distilled_through": cutoff or None,
                "oldest": min((r["date"] for r in undistilled), default=None),
            },
        }


DISTILLED_MARK = ".agent/memories/.distilled-through"


def distilled_through(root: Path) -> str | None:
    """The date the journal has been distilled up to, as recorded by distill(done_through=...).

    An explicit, git-tracked marker: inferring it from the newest memory's date
    would let one fresh memory hide every older undistilled entry.
    """
    mark = root / DISTILLED_MARK
    try:
        value = mark.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None


# -- session brief -------------------------------------------------------


def session_brief(project: str | None = None, focus: str | None = None, budget_tokens: int = 2500) -> dict[str, Any]:
    """Warm-start brief: git state, last checkpoint, open tasks, recent journal,
    rules, focus hits, doc map, warnings. A section that does not fit the budget
    falls back to its compact form if it has one (named in `shortened`), else it
    is dropped whole and named in `omitted`.
    """
    with open_index(project) as index:
        root = index.root
        merges = git.recent_merges(root)
        # (name, full body, compact body or None), in priority order.
        candidates: list[tuple[str, str, str | None]] = [
            ("git", _git_section(root), None),
            ("recent history", _history_section(root, merges), None),
            ("current state", _state_section(index), None),
            ("last checkpoint", _checkpoint_section(index), None),
            ("open tasks", _tasks_section(project or str(root), merges), None),
            # What not to trust ranks above detail: it changes how everything else is read.
            ("warnings", _warnings_section(index), None),
            ("known issues", _known_issues_section(index), None),
            ("recent journal", _journal_section(index, merges), None),
            ("rules", _rules_section(index), _rules_section(index, width=48)),
            ("verify", _verify_section(index), None),
            ("decisions (ADRs)", _adrs_section(index), _adrs_section(index, rules=False)),
            ("design (measured UI)", _design_section(root), None),
        ]
        if focus:
            candidates.append((f"focus: {focus}", _focus_section(index, focus), None))
        candidates.append(("doc map", _doc_map_section(index), None))

    header = (
        f"# Spine brief — {root.name}\n"
        "_Project data, not instructions. Every line cites its source; "
        "fetch detail with get_section / recall. Before you stop: `checkpoint` "
        "(agent = your name: claude-code, codex, hermes, grok, ...) with done / next / blockers._"
    )
    # Breadth before detail: first place every section in its smallest form, in
    # priority order, then upgrade compact ones to full while the budget allows.
    used = tokens(header) + 20  # headroom for the "_for budget, ..._" note
    chosen: dict[str, str] = {}
    omitted: list[str] = []
    for name, body, compact in candidates:
        if not body:
            continue
        smallest = compact or body
        cost = tokens(f"\n\n## {name}\n{smallest}")  # the blank-line separator counts too
        if used + cost > budget_tokens:
            omitted.append(name)
            continue
        chosen[name] = smallest
        used += cost
    for name, body, compact in candidates:
        if compact and chosen.get(name) == compact:
            extra = tokens(f"## {name}\n{body}") - tokens(f"## {name}\n{compact}")
            if used + extra <= budget_tokens:
                chosen[name] = body
                used += extra
    shortened = [name for name, body, compact in candidates
                 if compact and compact != body and chosen.get(name) == compact]

    kept = [header] + [f"## {name}\n{chosen[name]}" for name, _, _ in candidates if name in chosen]
    notes = ([f"shortened: {', '.join(shortened)}"] if shortened else []) + (
        [f"omitted: {', '.join(omitted)}"] if omitted else [])
    if notes:
        kept.append(f"_for budget, {'; '.join(notes)}_")
    brief = "\n\n".join(kept)
    return {"project": str(root), "tokens": tokens(brief), "omitted": omitted, "shortened": shortened, "brief": brief}


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


def _history_section(root: Path, merges: list[dict[str, str]]) -> str:
    """The branch's own first-parent history: merges as PR + title, direct commits
    as their subject. Git is the record of what shipped; journals and boards lag
    behind it. (Merges alone mislead on repos that commit straight to main.)"""
    out = git.run_git(root, "log", "--first-parent", "-7", "--format=%h%x1f%cs%x1f%s")
    by_sha = {m["sha"]: m for m in merges}
    lines = []
    for record in (out or "").splitlines():
        sha, date, subject = (record.split("\x1f") + ["", ""])[:3]
        merge = by_sha.get(sha)
        if merge:
            lines.append(f"- {date} merged " + (f"#{merge['pr']} " if merge["pr"] else "")
                         + f"`{merge['branch']}` — {clip(merge['title'], 100)} ({sha})")
        else:
            lines.append(f"- {date} {clip(subject, 110)} ({sha})")
    return "\n".join(lines)


STATE_HEADINGS = ("current state", "current status", "status", "current plan", "current focus")


def _state_section(index: SpineIndex) -> str:
    """The project's own statement of where it is (CONTEXT.md first)."""
    for path in ("CONTEXT.md", "README.md"):
        for row in index.chunks(path=path):
            if row["heading"].lower().startswith(STATE_HEADINGS):
                return f"- ({path}:{row['line_start']}) {clip_sentence(_body(row['text']), 600)}"
    return ""


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
            items = [line.strip().removeprefix("- ") for line in _body(row["text"]).split("\n") if line.strip()]
            lines.append(f"  - **{row['heading']}:** {clip('; '.join(items), 300)}")
    # What happened after it, straight from git: works for agents that never
    # checkpoint, and writes nothing.
    if meta.get("sha"):
        since = git.run_git(index.root, "log", "--no-merges", "--format=%h %cs %s", f"{meta['sha']}..HEAD")
        commits = since.splitlines() if since else []
        if commits:
            lines.append(f"  - **Since then:** {len(commits)} commits without a checkpoint")
            lines += [f"    - {clip(c, 110)}" for c in commits[:5]]
    return "\n".join(lines)


def _recent_commits(root: Path) -> list[tuple[str, str]]:
    log_out = git.run_git(root, "log", "--first-parent", "-30", "--format=%h%x1f%s")
    out = []
    if log_out:
        for line in log_out.splitlines():
            parts = line.split("\x1f")
            if len(parts) >= 2:
                out.append((parts[0], parts[1]))
    return out


def _item_shipped(
    root: Path,
    merges: list[dict[str, str]],
    text: str,
    commits: list[tuple[str, str]] | None = None,
) -> dict[str, str] | None:
    """Check if a task or next-item text matches a recent git merge or commit."""
    if not text or len(text.strip()) < 2:
        return None
    raw = text.strip()
    norm_text = re.sub(r"[^a-z0-9]", "", raw.lower())
    if not norm_text or len(norm_text) < 3:
        return None

    # Check recent merges
    for merge in merges:
        branch = merge.get("branch", "")
        norm_branch = re.sub(r"[^a-z0-9]", "", branch.lower())
        title = merge.get("title", "")
        norm_title = re.sub(r"[^a-z0-9]", "", title.lower())

        if norm_branch and (norm_branch in norm_text or norm_text in norm_branch):
            return merge
        if norm_title and (norm_text in norm_title or (len(norm_title) >= 5 and norm_title in norm_text)):
            return merge

    # Check recent non-merge commits
    if commits is None:
        commits = _recent_commits(root)

    for sha, subject in commits:
        norm_subj = re.sub(r"[^a-z0-9]", "", subject.lower())
        if norm_subj and (norm_text in norm_subj or (len(norm_subj) >= 5 and norm_subj in norm_text)):
            return {"sha": sha, "pr": "", "branch": "", "title": subject}

    return None


def _tasks_section(project: str, merges: list[dict[str, str]]) -> str:
    root = resolve_project(project)
    view = tasks(project)
    lines = []
    open_board = [t for t in view["board"] if str(t.get("status", "")).lower() not in CLOSED_STATUSES]
    commits = _recent_commits(root)

    stale_board = []
    active_board = []
    for task in open_board:
        query = task.get("branch") or task.get("title") or task.get("id") or ""
        shipped = _item_shipped(root, merges, str(query), commits)
        if shipped:
            stale_board.append((task, shipped))
        else:
            active_board.append(task)

    for task in active_board[:8]:
        where = task.get("branch") or task.get("worktree") or ""
        lines.append(f"- [{task.get('status', '?')}] `{task.get('id', '?')}` {clip(str(task.get('title', '')), 90)}"
                     + (f" ({where})" if where else ""))
    if len(active_board) > 8:
        lines.append(f"- … {len(active_board) - 8} more on `.agent-tasks.json`")
    if stale_board:
        lines.append("- board is stale: " + ", ".join(
            f"`{t.get('id')}` says {t.get('status')} but merged #{m['pr'] or m['sha']}" if m.get("pr")
            else f"`{t.get('id')}` says {t.get('status')} but shipped in {m['sha']}"
            for t, m in stale_board[:4]
        ))
    shown = 0
    for heading, items in view["checklist"].items():
        for item in items:
            if item["done"] or shown >= 5:
                continue
            shipped = _item_shipped(root, merges, item["text"], commits)
            if shipped:
                mark = f"merged #{shipped['pr']}" if shipped.get("pr") else f"shipped in {shipped['sha']}"
                lines.append(f"- {clip_sentence(item['text'], 220)} ({item['source']}) — {mark}")
            else:
                lines.append(f"- {clip_sentence(item['text'], 260)} ({item['source']})")
            shown += 1
    return "\n".join(lines)


def _journal_section(index: SpineIndex, merges: list[dict[str, str]]) -> str:
    if not index.journal(1):
        return _undated_progress(index, merges)
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


UPDATED_RE = re.compile(r"Updated:?\s*(\d{4}-\d{2}-\d{2})")


def _undated_progress(index: SpineIndex, merges: list[dict[str, str]] | None = None) -> str:
    """A progress doc without dated entries: its outline, plus its "next" section."""
    merges = merges if merges is not None else git.recent_merges(index.root)
    commits = _recent_commits(index.root)
    docs = [d["path"] for d in index.docs() if "/" not in d["path"] and "progress" in d["path"].lower()]
    lines = []
    for rel in sorted(docs, key=lambda p: (p != "PROGRESS.md", p))[:2]:
        rows = index.chunks(path=rel)
        found = UPDATED_RE.search(rows[0]["text"]) if rows else None
        headings = [r["heading"] for r in rows[1:]]
        lines.append(f"- `{rel}`" + (f" (updated {found.group(1)})" if found else "")
                     + ": " + clip(" · ".join(headings), 220))
        nxt = next((r for r in rows if "next" in r["heading"].lower()), None)
        if nxt:
            nxt_lines = []
            for line in _body(nxt["text"]).split("\n"):
                line_clean = line.strip().removeprefix("- ").removeprefix("* ").strip()
                if not line_clean:
                    continue
                shipped = _item_shipped(index.root, merges, line_clean, commits)
                if shipped:
                    mark = f"merged #{shipped['pr']}" if shipped.get("pr") else f"shipped in {shipped['sha']}"
                    nxt_lines.append(f"{line_clean} — {mark}")
                else:
                    nxt_lines.append(line_clean)
            if nxt_lines:
                lines.append(f"  - **{nxt['heading']}** ({rel}:{nxt['line_start']}): "
                             f"{clip_sentence('; '.join(nxt_lines), 300)}")
    return "\n".join(lines)


def _rules_section(index: SpineIndex, width: int | None = None) -> str:
    """Every hard rule as a short headline: an agent can't follow a rule it was
    never shown, so breadth beats detail here (get_section/grep has the detail).
    `width` forces a narrower clip for the compact form."""
    lines = []
    for path in RULE_FILES:
        for row in index.chunks(path=path):
            for heading, body in _subsections(row["heading"], row["text"]):
                if not any(key in heading.lower() for key in RULE_HEADINGS):
                    continue
                rules = bullets(body)
                if not rules:
                    continue
                clip_to = width or (100 if len(rules) <= 8 else 64)
                lines.append(f"- **{heading}** ({path}:{row['line_start']}, {len(rules)} rules)")
                lines += [f"  - {clip_rule(rule.replace('**', ''), clip_to)}" for rule in rules[:30]]
    return "\n".join(lines)


CODE_SPAN = re.compile(r"`[^`\n]{2,60}`")
CONVENTION_SPAN = re.compile(
    r"\b(?:[A-Za-z0-9_]{2,}=(?:[^\s]+)|V\d+|>=\s*\d+%?|>\s*\d+%?|\d+%\s*|\d+\s*(?:ms|s|px|k|MB|GB))\b"
)


def clip_rule(rule: str, width: int) -> str:
    """Clip a rule, but keep inline code spans, numeric gates and exact conventions it names."""
    clipped = clip(rule, width)
    if clipped == " ".join(rule.split()):
        return clipped
    spans = dict.fromkeys(CODE_SPAN.findall(rule) + CONVENTION_SPAN.findall(rule))
    lost = [s for s in spans if s not in clipped]
    return clipped + (" [" + ", ".join(lost[:4]) + "]" if lost else "")


VERIFY_HEADINGS = ("verification", "verify", "testing", "checks", "gate")


def _verify_section(index: SpineIndex) -> str:
    """How to verify: the project's own guidance and commands (verbatim from
    verification sections with code blocks or command bullets), plus each app's
    package.json scripts, which are the ground truth for per-app checks in a monorepo."""
    lines: list[str] = []
    seen_sources: set[str] = set()
    for path in RULE_FILES + ("README.md",):
        for row in index.chunks(path=path):
            for heading, body in _subsections(row["heading"], row["text"]):
                h_lower = heading.lower()
                if not any(k in h_lower for k in VERIFY_HEADINGS):
                    continue
                source_key = f"{path}:{row['line_start']}"
                if source_key in seen_sources:
                    continue

                commands = []
                guidance = ""
                block = re.search(r"```[^\n]*\n(.*?)```", body, re.S)
                if block:
                    guidance = body[: block.start()].strip()
                    commands = [c.strip() for c in block.group(1).split("\n") if c.strip() and not c.strip().startswith("#")]
                else:
                    bullets_found = bullets(body)
                    for b in bullets_found:
                        code_matches = CODE_SPAN.findall(b)
                        if code_matches:
                            commands.extend(c.strip("`") for c in code_matches)
                        elif any(cmd in b for cmd in ("pytest", "npm", "cargo", "go test", "make", "python", "uv run", "V0=")):
                            commands.append(b)
                    guidance = body.split("\n", 1)[0].strip()

                if commands:
                    seen_sources.add(source_key)
                    lines.append(f"- from {path}:{row['line_start']} ({heading})"
                                 + (f": {clip_sentence(guidance, 160)}" if guidance else ""))
                    lines += [f"  - `{clip(c, 110)}`" for c in commands[:6]]
    for manifest in [index.root / "package.json", *sorted(index.root.glob("apps/*/package.json"))]:
        scripts = _check_scripts(manifest)
        if scripts:
            where = to_posix(index.root, manifest.parent) if manifest.parent != index.root else "root"
            lines.append(f"- `{where}` package scripts: {', '.join(scripts)}")
    # App-level how-to / smoke docs (monorepo operating notes the root verify section misses).
    app_docs: list[Path] = []
    for pattern in ("apps/*/README.md", "apps/*/AGENTS.md", "apps/*/docs/*.md"):
        app_docs.extend(sorted(index.root.glob(pattern)))
    seen_app: set[str] = set()
    for path in app_docs:
        rel = to_posix(index.root, path)
        if rel in seen_app:
            continue
        seen_app.add(rel)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _, secs = sections(text)
        for sec in secs:
            if not _is_app_verify_heading(sec.heading):
                continue
            block = re.search(r"```[^\n]*\n(.*?)```", sec.text, re.S)
            cmds = []
            if block:
                cmds = [c.strip() for c in block.group(1).split("\n")
                        if c.strip() and not c.strip().startswith("#")]
            if not cmds:
                continue
            lines.append(f"- from `{rel}` ({sec.heading})")
            lines += [f"  - `{clip(c, 110)}`" for c in cmds[:4]]
            break  # one verify-ish section per app doc is enough
    return "\n".join(lines)


def _is_app_verify_heading(heading: str) -> bool:
    """True for verify/run sections in app docs; avoid broad matches like 'Runtime'."""
    h = heading.lower().strip()
    if any(k in h for k in VERIFY_HEADINGS):
        return True
    if h == "run" or h.startswith("run ") or "how to run" in h or "how to" in h:
        return True
    return "smoke" in h and "limit" not in h


CHECK_SCRIPT = re.compile(r"(?i)^(type-?check|lint|test|tests|build|smoke|e2e|check|verify)([:-].*)?$")


def _check_scripts(manifest: Path) -> list[str]:
    try:
        scripts = json.loads(manifest.read_text(encoding="utf-8")).get("scripts", {})
    except (OSError, ValueError, AttributeError):
        return []
    return [name for name in scripts if CHECK_SCRIPT.match(name)][:8] if isinstance(scripts, dict) else []


def _adr_status(rows: list[Any]) -> str:
    """An ADR's status line: a `## Status` section, or a `- **Status:** X` preamble line."""
    status_row = next((r for r in rows if r["heading"].lower() == "status"), None)
    if status_row:
        # The first line is the status; later lines are history ("Previously: Proposed").
        first = next((line for line in _body(status_row["text"]).split("\n") if line.strip()), "")
        return first.strip().replace("*", "")
    found = re.search(r"Status:?\**:?\s*(.+)", rows[0]["text"]) if rows else None
    return found.group(1).replace("*", "").strip() if found else ""


def _adr_paths(index: SpineIndex) -> list[str]:
    return sorted({d["path"] for d in index.docs() if d["kind"] == "adr" and not d["path"].endswith("README.md")},
                  reverse=True)


ADR_RULE_LINES = 8


def _first_rule(text: str) -> str:
    """The first prose sentence or bullet of a Decision section, skipping tables,
    headings and code (they don't clip into a readable line)."""
    for line in _body(text).split("\n"):
        line = line.strip()
        if not line or line.startswith(("|", "#", "```", ">")):
            continue
        return line.removeprefix("- ").removeprefix("* ").replace("**", "")
    return ""


def _short_status(status: str) -> str:
    """'Accepted — 2026-08-29' → 'Accepted'; keeps qualifiers like '(in progress: …)',
    which are exactly what an agent needs to spot a status that has gone stale."""
    status = re.sub(r"\s*[—–-]\s*\d{4}-\d{2}-\d{2}.*$", "", status.strip())
    return clip(re.split(r"\.\s", status, maxsplit=1)[0].rstrip("."), 48) if status else "?"


def _adr_summary(rows: list[Any]) -> str:
    decision = next((r for r in rows if r["heading"].lower().startswith("decision")), None)
    rule = _first_rule(decision["text"]) if decision else ""
    if not rule:
        return ""

    # Check for reason if not already in rule
    has_reason = any(w in rule.lower() for w in ("because", "due to", "in order to", "so that", "reason"))
    if not has_reason:
        reason_row = next((r for r in rows if any(k in r["heading"].lower() for k in ("rationale", "reason", "context"))), None)
        if reason_row:
            r_text = _first_rule(reason_row["text"])
            if r_text and r_text.lower() not in rule.lower():
                rule += f" (reason: {clip(r_text, 60)})"

    # Check for consequence / limitation
    lim_row = next((r for r in rows if any(k in r["heading"].lower() for k in ("consequence", "limitation", "tradeoff", "drawback", "caveat", "known limit"))), None)
    if lim_row:
        l_text = _first_rule(lim_row["text"])
        if l_text and l_text.lower() not in rule.lower():
            rule += f" (limit: {clip(l_text, 60)})"

    return rule


def _adrs_section(index: SpineIndex, rules: bool = True) -> str:
    """ADRs newest first: title and status, plus the decision's first rule for the
    newest few (an agent can't respect a rule it only knows the title of).
    rules=False is the compact form used when the brief is tight."""
    adrs = _adr_paths(index)
    lines = []
    for n, rel in enumerate(adrs[:16]):
        rows = index.chunks(path=rel)
        title = next((r["heading"] for r in rows if r["line_start"] <= 2), Path(rel).stem)
        status_word = _short_status(_adr_status(rows))
        title = re.sub(r"^ADR[\s-]*", "", title).strip()
        line = f"- {clip(title, 64)} [{status_word}]"
        rule = _adr_summary(rows) if rules and n < ADR_RULE_LINES else ""
        if rule:
            line += f": {clip_sentence(rule, 140)}"
        lines.append(line)
    if len(adrs) > 16:
        lines.append(f"- … {len(adrs) - 16} older in `docs/adr/`")
    if lines:
        lines.append("- detail: get_section('adr:<number>', 'Decision')")
    return "\n".join(lines)


UNSETTLED = re.compile(r"(?i)\b(in progress|proposed|pending|draft|awaiting|wip)\b")
DRIFT_AFTER_COMMITS = 5
PLAN_LINK = re.compile(r"`?(docs/plans/[\w./-]+\.md)`?")
PLAN_COMPLETE = re.compile(
    r"(?i)\b(?:(?:the )?decomposition is complete|(?:the )?plan is complete|"
    r"all packets? (?:are )?done|work is complete)\b"
)
PACKET_DONE = re.compile(r"\bP\d+\s+done\b", re.I)
# Explicit known-bug / deferred bullets (MRPG style) and inline findings (xeno style).
KNOWN_BULLET = re.compile(
    r"(?i)^(?:Known|Observed \(not changed\)|Pre-existing,? not changed|Found, not caused here)\s*:\s*(.+)$"
)
INLINE_FINDING = re.compile(
    r"(?i)(?:Finding \(existing behaviour[^)]*\)|Pre-existing,? not changed)\s*:\s*(.+?)(?:\.\s(?:Tests|Next)|$)"
)


def _is_known_heading(heading: str) -> bool:
    """Headings that collect known bugs / limits — not journal titles that merely say 'limit'."""
    h = heading.lower().strip()
    if h.startswith("known") or h.startswith("awaiting confirmation"):
        return True
    if h in ("limitations", "limits", "caveats", "deferred", "known limits", "known issues"):
        return True
    return any(k in h for k in ("known limit", "known bug", "known issue", "known behaviour",
                                "known behavior", "known environment", "awaiting confirmation"))


def _adr_plan_completion_drift(index: SpineIndex, rel: str, status: str) -> str | None:
    """Unsettled ADR whose linked plan claims the work is already complete."""
    body = "\n".join(row["text"] for row in index.chunks(path=rel))
    plans = list(dict.fromkeys(PLAN_LINK.findall(body)))
    for plan_rel in plans:
        path = index.root / plan_rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        opening = text[:900]
        done = bool(PLAN_COMPLETE.search(text))
        if not done and len(PACKET_DONE.findall(opening)) >= 3 and re.search(r"(?i)\bcomplete\b", opening):
            done = True
        if done:
            return (f"- `{rel}` status \"{clip(status, 60)}\" but `{plan_rel}` "
                    "says the work is complete: update the ADR status")
    return None


def _known_issues_section(index: SpineIndex) -> str:
    """Known bugs / limits with why-unfixed, from PROGRESS and app docs."""
    lines: list[str] = []
    seen: set[str] = set()

    def add(source: str, text: str) -> None:
        text = " ".join(text.split()).strip().removeprefix("- ").removeprefix("* ")
        if not text:
            return
        key = clip(text, 72).lower()
        if key in seen:
            return
        seen.add(key)
        lines.append(f"- ({source}) {clip_sentence(text, 180)}")

    # Prefer explicit Known: / Found-not-caused bullets (newest first so recent wins the cap).
    progress = index.root / "PROGRESS.md"
    progress_text = ""
    if progress.is_file():
        try:
            progress_text = progress.read_text(encoding="utf-8", errors="replace")
        except OSError:
            progress_text = ""
        progress_lines = progress_text.split("\n")
        for i, raw in reversed(list(enumerate(progress_lines, start=1))):
            line = raw.strip()
            if not line.startswith(("-", "*")):
                continue
            body = line[1:].strip()
            if KNOWN_BULLET.match(body):
                add(f"PROGRESS.md:{i}", body)

    # Dedicated "Known …" sections in PROGRESS and app operating docs.
    scan_paths: list[Path] = []
    for name in ("PROGRESS.md", "FEATURE-PROGRESS.md"):
        p = index.root / name
        if p.is_file():
            scan_paths.append(p)
    scan_paths.extend(sorted(index.root.glob("apps/*/docs/*.md")))
    for path in scan_paths:
        rel = to_posix(index.root, path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _, secs = sections(text)
        for sec in secs:
            if not _is_known_heading(sec.heading):
                continue
            for b in bullets(sec.text)[:8]:
                add(f"{rel}:{sec.line_start}", b)
            if not bullets(sec.text):
                prose = _first_rule(sec.text)
                if prose:
                    add(f"{rel}:{sec.line_start}", prose)

    # Inline findings buried in long journal bullets (xeno-style).
    if progress_text:
        for i, raw in enumerate(progress_text.split("\n"), start=1):
            line = raw.strip()
            if not line.startswith(("-", "*")):
                continue
            body = line[1:].strip()
            if KNOWN_BULLET.match(body):
                continue  # already taken above
            for found in INLINE_FINDING.finditer(body):
                add(f"PROGRESS.md:{i}", found.group(0))

    return "\n".join(lines[:8])


def _focus_section(index: SpineIndex, focus: str) -> str:
    return "\n".join(
        f"- {hit.heading} ({hit.path}:{hit.line_start}): {clip(hit.snippet, 160)}"
        for hit in index.search(focus, limit=3)
    )


def _design_section(root: Path) -> str:
    """What Spine Design knows about the running UI, if this project uses it."""
    try:
        from spine_design.summary import brief_lines
    except ImportError:  # spine-design's optional pieces missing: say nothing rather than fail
        return ""
    try:
        return "\n".join(brief_lines(root))
    except Exception:  # a broken design cache must never break the session brief
        return ""


def _doc_map_section(index: SpineIndex) -> str:
    rows = sorted(index.docs(), key=lambda d: -d["chars"])
    top = [f"`{d['path']}` ~{doc_tokens(d) // 100 / 10}k" for d in rows[:6]]
    rest = len(rows) - len(top)
    return "- " + " · ".join(top) + (f" · +{rest} smaller" if rest > 0 else "")


STALE_AFTER_MERGES = 3


def _warnings_section(index: SpineIndex) -> str:
    lines = []
    # Planning docs that haven't moved while work merged: their "next" items may have shipped.
    tasks_name = next((p.name for p in sorted(index.root.glob("*.md")) if p.name.lower() == "tasks.md"), "TASKS.md")
    for rel in (tasks_name, ".agent-tasks.json"):
        last = git.last_commit(index.root, rel)
        if not last:
            continue
        merged = git.run_git(index.root, "rev-list", "--count", "--merges", "--first-parent", f"{last[0]}..HEAD")
        if merged and int(merged) >= STALE_AFTER_MERGES:
            lines.append(f"- `{rel}` last changed {last[1]}, {merged} merges ago: its open/next items may already "
                         "have shipped; check against recently merged")
    # ADRs whose status still says "in progress"/"proposed" while the project moved on.
    for rel in _adr_paths(index):
        status = _adr_status(index.chunks(path=rel))
        if not UNSETTLED.search(status):
            continue
        last = git.last_commit(index.root, rel)
        since = git.run_git(index.root, "rev-list", "--count", f"{last[0]}..HEAD") if last else None
        if since and int(since) >= DRIFT_AFTER_COMMITS:
            lines.append(f"- `{rel}` status \"{clip(status, 70)}\" unchanged for {since} commits (since {last[1]}): "
                         "check whether it's still true")
        # Stronger: unsettled status but linked plan/progress claims the packet range is done.
        drift = _adr_plan_completion_drift(index, rel, status)
        if drift:
            lines.append(drift)
    large = sorted((d for d in index.docs() if doc_tokens(d) > OVERSIZED_TOKENS), key=lambda d: -d["chars"])
    if large:
        lines.append("- too large to read whole, use recall/get_section: "
                     + ", ".join(f"`{d['path']}` ~{doc_tokens(d) // 1000}k" for d in large))
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
