"""Write tools (slice S2): checkpoints, memories, distillation, the Stop-hook nudge.

Everything written lands in `<project>/.agent/memories/` as plain markdown, so
it travels with git, merges per file, and stays readable without Spine. Spine
never commits: the files show up as ordinary changes for the agent or human to
commit with the rest of the work.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spine_core import git
from spine_core.anchors import staleness
from spine_core.index import MEMORY_KINDS, SpineIndex
from spine_core.markdown import sections, split_frontmatter
from spine_core.resolve import resolve_project, safe_path, to_posix
from spine_core.scrub import scrub

from .core import DISTILLED_MARK, _body, clip, distilled_through, open_index

MEMORIES = ".agent/memories"
MEMORY_DIRS = {"insight": "codebase_insights", "decision": "architectural_decisions"}
LESSONS_FILE = f"{MEMORIES}/patterns_and_lessons.md"
VERIFY_VALUES = {"pass", "fail", "skip", "waived"}
WORD = re.compile(r"[a-z0-9]{4,}")


class SpineWriteError(ValueError):
    """A write was refused (bad input, read-only host, or a likely duplicate)."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _slug(text: str, limit: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "untitled"


def _check_writable() -> None:
    if os.environ.get("SPINE_READONLY", "").lower() in {"1", "true", "yes"}:
        raise SpineWriteError("this Spine host is read-only (SPINE_READONLY is set)")


def _check_agent(agent: str) -> str:
    agent = (agent or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}", agent):
        raise SpineWriteError("agent is required: who is writing (e.g. claude-code, codex, grok, hermes)")
    return agent


def _check_paths(root: Path, paths: list[str] | None) -> list[str]:
    clean = []
    for path in paths or []:
        if any(c in path for c in ",[]\n"):
            raise SpineWriteError(f"path may not contain , [ ] or newlines: {path!r}")
        clean.append(to_posix(root, safe_path(root, path)))
    return clean


def _atomic_write(path: Path, text: str, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise FileExistsError(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _frontmatter(meta: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if value is None or value == [] or value == "":
            continue
        if isinstance(value, list):
            value = "[" + ", ".join(str(v) for v in value) + "]"
        lines.append(f"{key}: {value}")
    return "\n".join(lines + ["---", ""])


def _scrubbed(*parts: str) -> tuple[list[str], int]:
    out, total = [], 0
    for part in parts:
        clean, n = scrub(part)
        out.append(clean)
        total += n
    return out, total


# -- checkpoint ----------------------------------------------------------


def checkpoint(
    project: str | None,
    agent: str,
    summary: str,
    done: list[str] | None = None,
    next: list[str] | None = None,
    blockers: list[str] | None = None,
    decisions: list[str] | None = None,
    paths: list[str] | None = None,
    verification: dict[str, str] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Record where this session got to, as `.agent/memories/history/<time>-<slug>.md`."""
    _check_writable()
    agent = _check_agent(agent)
    if not summary or not summary.strip():
        raise SpineWriteError("summary is required")
    root = resolve_project(project)
    clean_paths = _check_paths(root, paths)
    verify = {}
    for stage, result in (verification or {}).items():
        if not re.fullmatch(r"V[0-4]", stage) or str(result).lower() not in VERIFY_VALUES:
            raise SpineWriteError(f"verification must map V0-V4 to {sorted(VERIFY_VALUES)}: {stage}={result}")
        verify[stage] = str(result).lower()

    def items(values: list[str] | None) -> str:
        return "\n".join(f"- {v.strip()}" for v in values or [] if v.strip()) or "- (none)"

    body = (
        f"## Summary\n\n{summary.strip()}\n\n## Done\n\n{items(done)}\n\n## Next\n\n{items(next)}\n\n"
        f"## Blockers\n\n{items(blockers)}\n\n## Decisions\n\n{items(decisions)}\n"
    )
    (body,), redactions = _scrubbed(body)

    when = _now()
    meta = {
        "kind": "checkpoint",
        "created": when.isoformat().replace("+00:00", "Z"),
        "agent": agent,
        "sha": git.head(root),
        "branch": git.branch(root),
        "task_id": task_id,
        "paths": clean_paths,
        "verification": "{" + ", ".join(f"{k}: {v}" for k, v in sorted(verify.items())) + "}" if verify else None,
    }
    stem = f"{when.strftime('%Y-%m-%dT%H%MZ')}-{_slug(summary)}"
    history = safe_path(root, f"{MEMORIES}/history")
    for n in range(100):
        target = history / (f"{stem}.md" if n == 0 else f"{stem}-{n}.md")
        try:
            meta["id"] = target.stem
            _atomic_write(target, _frontmatter(meta) + f"# Checkpoint: {clip(summary, 80)}\n\n" + body, exclusive=True)
            break
        except FileExistsError:
            continue
    else:
        raise SpineWriteError("could not find a free checkpoint filename")
    return {
        "written": to_posix(root, target),
        "redactions": redactions,
        "note": "uncommitted: commit it with the work it describes",
    }


# -- memories ------------------------------------------------------------


def _words(text: str) -> set[str]:
    return set(WORD.findall(text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _similar(index: SpineIndex, title: str, body: str) -> list[dict[str, Any]]:
    """Existing memories that look like the same fact (title or body overlap)."""
    title_words, body_words = _words(title), _words(body)
    found = []
    for row in index.db.execute("SELECT * FROM chunks WHERE kind IN ('insight','decision','lesson','memory')"):
        heading_score = _jaccard(title_words, _words(row["heading"]))
        body_score = _jaccard(body_words, _words(row["text"]))
        if heading_score >= 0.6 or body_score >= 0.45:
            target = row["path"] if row["kind"] != "lesson" else f"{row['path']}#{row['heading']}"
            found.append({
                "update_of": target,
                "kind": row["kind"],
                "heading": row["heading"],
                "score": round(max(heading_score, body_score), 2),
                "snippet": clip(_body(row["text"]), 200),
            })
    return sorted(found, key=lambda f: -f["score"])[:3]


def record_memory(
    project: str | None,
    agent: str,
    kind: str,
    title: str,
    body: str,
    paths: list[str] | None = None,
    tags: list[str] | None = None,
    update_of: str | None = None,
    force_new: bool = False,
) -> dict[str, Any]:
    """Write or update a durable memory: an insight, a decision, or a lesson.

    Before creating, look for an existing memory about the same thing. If one exists,
    nothing is written and the candidates come back: retry with `update_of`
    (to revise it) or `force_new=True` (it really is different).
    """
    _check_writable()
    agent = _check_agent(agent)
    kind = kind.strip().lower()
    if kind not in {"insight", "decision", "lesson"}:
        raise SpineWriteError("kind must be insight, decision or lesson")
    if not title.strip() or not body.strip():
        raise SpineWriteError("title and body are required")
    root = resolve_project(project)
    clean_paths = _check_paths(root, paths)
    clean_tags = [_slug(t, 24) for t in tags or [] if t.strip()]
    (title, body), redactions = _scrubbed(title.strip(), body.strip())

    with open_index(str(root)) as index:
        if not update_of and not force_new:
            candidates = _similar(index, title, body)
            if candidates:
                return {
                    "written": None,
                    "reason": "similar memories exist: pass update_of=<one of these> or force_new=true",
                    "candidates": candidates,
                }

    today = _now().date().isoformat()
    sha = git.head(root)
    if kind == "lesson":
        target = _write_lesson(root, agent, title, body, clean_paths, sha, today, update_of)
    else:
        target = _write_memory_file(root, agent, kind, title, body, clean_paths, clean_tags, sha, today, update_of)
    return {"written": target, "updated": bool(update_of), "redactions": redactions,
            "note": "uncommitted: commit it with the work it describes"}


def _write_memory_file(
    root: Path, agent: str, kind: str, title: str, body: str,
    paths: list[str], tags: list[str], sha: str | None, today: str, update_of: str | None,
) -> str:
    created = today
    if update_of:
        target = safe_path(root, update_of)
        rel = to_posix(root, target)
        if not rel.startswith(f"{MEMORIES}/{MEMORY_DIRS[kind]}/") or not target.is_file():
            raise SpineWriteError(f"update_of must be an existing {kind} file under {MEMORIES}/{MEMORY_DIRS[kind]}/")
        old_meta, _, _ = split_frontmatter(target.read_text(encoding="utf-8", errors="replace"))
        created = str(old_meta.get("created") or today)
    else:
        folder = safe_path(root, f"{MEMORIES}/{MEMORY_DIRS[kind]}")
        target = folder / f"{_slug(title)}.md"
        n = 2
        while target.exists():
            target = folder / f"{_slug(title)}-{n}.md"
            n += 1
    meta = {"kind": kind, "title": title.replace("\n", " "), "tags": tags, "paths": paths,
            "sha": sha, "agent": agent, "created": created, "updated": today}
    _atomic_write(target, _frontmatter(meta) + f"# {title}\n\n{body}\n")
    return to_posix(root, target)


def _write_lesson(
    root: Path, agent: str, title: str, body: str,
    paths: list[str], sha: str | None, today: str, update_of: str | None,
) -> str:
    """Lessons live as `##` sections of the protocol's single patterns_and_lessons.md."""
    target = safe_path(root, LESSONS_FILE)
    text = target.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n") if target.exists() else (
        f"# Patterns and lessons — {root.name}\n"
    )
    stamp = f"_{agent}, {today}" + (f", {sha}" if sha else "") + (
        "; paths: " + ", ".join(f"`{p}`" for p in paths) if paths else "") + "_"
    section = f"## {title}\n\n{body}\n\n{stamp}\n"
    if update_of:
        file_part, _, heading = update_of.partition("#")
        if file_part != LESSONS_FILE or not heading:
            raise SpineWriteError(f"update_of for a lesson must be '{LESSONS_FILE}#<heading>'")
        _, parts = sections(text)
        old = next((p for p in parts if p.heading == heading), None)
        if old is None:
            raise SpineWriteError(f"no lesson headed {heading!r} in {LESSONS_FILE}")
        lines = text.split("\n")
        text = "\n".join(lines[: old.line_start - 1] + section.rstrip("\n").split("\n") + [""] + lines[old.line_end:])
    else:
        text = text.rstrip("\n") + "\n\n" + section
    _atomic_write(target, text.rstrip("\n") + "\n")
    return f"{LESSONS_FILE}#{title}"


# -- distill -------------------------------------------------------------


def distill(
    project: str | None,
    since: str | None = None,
    limit: int = 15,
    done_through: str | None = None,
) -> dict[str, Any]:
    """Journal entries and checkpoints not yet distilled into memories, plus the
    current memories, so the calling agent can propose record_memory calls.
    Spine does no summarising itself.

    After recording memories for a batch, call again with done_through=<date of
    the last entry handled> to move the marker on (written to git, so every host
    and agent sees the same progress).
    """
    if done_through:
        _check_writable()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", done_through):
            raise SpineWriteError("done_through must be YYYY-MM-DD")
        root = resolve_project(project)
        current = distilled_through(root)
        if current and done_through < current:
            raise SpineWriteError(f"done_through {done_through} is before the current marker {current}")
        _atomic_write(safe_path(root, DISTILLED_MARK), done_through + "\n")
        return {"distilled_through": done_through, "written": DISTILLED_MARK,
                "note": "uncommitted: commit it with the memories it covers"}

    with open_index(project) as index:
        root = index.root
        tracked = git.tracked_files(root)
        memories = []
        for doc in index.docs():
            if doc["kind"] not in MEMORY_KINDS or doc["kind"] == "checkpoint":
                continue
            body = (root / doc["path"]).read_text(encoding="utf-8", errors="replace")
            state = staleness(root, doc["path"], json.loads(doc["meta"]), body, tracked)
            for row in index.chunks(path=doc["path"]):
                if row["heading"] and not row["heading"].startswith("("):
                    memories.append({"source": f"{row['path']}:{row['line_start']}", "kind": doc["kind"],
                                     "heading": row["heading"], "staleness": state.line()})
        cutoff = since or distilled_through(root)
        pending = [
            r for r in index.db.execute(
                "SELECT * FROM chunks WHERE kind IN ('journal','checkpoint') AND date IS NOT NULL"
                " AND date > ? ORDER BY date ASC, path, line_start", (cutoff or "",)
            )
            if r["kind"] == "journal" or r["heading"].lower().startswith(("summary", "decision"))
        ]
        return {
            "since": cutoff or None,
            "pending_count": len(pending),
            "entries": [
                {"source": f"{r['path']}:{r['line_start']}", "date": r["date"], "kind": r["kind"],
                 "heading": r["heading"], "text": clip(_body(r["text"]), 500)}
                for r in pending[:limit]
            ],
            "more": max(0, len(pending) - limit),
            "memories": memories,
            "how": "For each durable fact (why something is the way it is, a gotcha, a decision), call "
                   "record_memory. Revise existing ones with update_of rather than adding near-duplicates. "
                   "Skip anything git or the code already says. Then call distill(done_through=<last "
                   "entry date handled>) so the next agent starts after this batch.",
        }


# -- Stop-hook nudge (Claude Code; other agents use the AGENTS.md snippet) --


def _session_file(root: Path, session_id: str) -> Path:
    return root / ".agent" / "spine" / "sessions" / f"{_slug(session_id, 80)}.json"


def start_session(project: str | None, session_id: str) -> None:
    """Remember where a session started, for the Stop-hook nudge."""
    root = resolve_project(project)
    path = _session_file(root, session_id)
    if path.exists():
        return  # resumed or compacted session keeps its original start
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"start_sha": git.head(root), "started": _now().isoformat(), "nudged": False}),
                    encoding="utf-8")


def nudge(project: str | None, session_id: str, stop_hook_active: bool = False) -> str | None:
    """A one-time reminder to checkpoint, or None.

    Fires only when this session changed the project (new commits or dirty
    paths outside .agent/) and wrote no checkpoint since it started.
    """
    if stop_hook_active or not session_id:
        return None
    root = resolve_project(project)
    path = _session_file(root, session_id)
    if not path.is_file():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("nudged"):
        return None
    changed = git.head(root) != state.get("start_sha") or any(
        not p.strip('"').startswith(".agent/") for p in git.dirty_paths(root)
    )
    if not changed:
        return None
    started = datetime.fromisoformat(state["started"]).timestamp()
    history = root / MEMORIES / "history"
    if history.is_dir() and any(f.stat().st_mtime >= started for f in history.glob("*.md")):
        return None
    state["nudged"] = True
    path.write_text(json.dumps(state), encoding="utf-8")
    return (
        "Spine: this session changed the project but recorded no checkpoint. Before stopping, call "
        "the spine `checkpoint` tool (or `spine checkpoint --agent claude-code --summary ... --next ...`) "
        "with what was done, what's next, and any blockers. This reminder fires once per session."
    )
