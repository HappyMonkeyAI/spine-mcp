"""Thin, failure-tolerant git helpers. Every call returns None/empty outside a repo."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    # rstrip only: `status --porcelain` lines start with a significant space.
    return result.stdout.rstrip()


def head(root: Path) -> str | None:
    return run_git(root, "rev-parse", "--short", "HEAD")


def branch(root: Path) -> str | None:
    return run_git(root, "branch", "--show-current") or None


def dirty_paths(root: Path) -> list[str]:
    out = run_git(root, "status", "--porcelain")
    return [line[3:] for line in out.splitlines() if line.strip()] if out else []


def worktrees(root: Path) -> list[dict[str, str]]:
    """Linked worktrees (excluding the main checkout) with their branches."""
    out = run_git(root, "worktree", "list", "--porcelain")
    if not out:
        return []
    trees: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in out.splitlines() + [""]:
        if not line:
            if current:
                trees.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
    main = root.resolve()
    return [t for t in trees if Path(t.get("path", "")).resolve() != main]


def tracked_files(root: Path) -> list[str]:
    out = run_git(root, "ls-files")
    return out.splitlines() if out else []


def last_commit(root: Path, path: str) -> tuple[str, str] | None:
    """(short sha, YYYY-MM-DD) of the last commit touching path."""
    out = run_git(root, "log", "-1", "--format=%h %cs", "--", path)
    if not out:
        return None
    sha, _, date = out.partition(" ")
    return sha, date


def changed_since(root: Path, sha: str, paths: list[str]) -> tuple[int, list[str]] | None:
    """(commits touching paths since sha, which of paths changed). None if sha is unknown."""
    if not paths:
        return 0, []
    count = run_git(root, "rev-list", "--count", f"{sha}..HEAD", "--", *paths)
    if count is None:
        return None
    changed = run_git(root, "diff", "--name-only", sha, "HEAD", "--", *paths) or ""
    return int(count), [p for p in changed.splitlines() if p]
