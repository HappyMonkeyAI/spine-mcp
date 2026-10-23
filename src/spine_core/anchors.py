"""Commit anchoring: how stale is a piece of recorded knowledge?

A record's anchor is the `sha` in its frontmatter, or failing that the last
commit that touched the record's own file. Its subject paths are the `paths`
frontmatter list, or failing that the backtick-quoted paths in its body that
match tracked files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import git

PATH_TOKEN = re.compile(r"`([\w./-]+\.[A-Za-z0-9]{1,6})`")


@dataclass
class Staleness:
    anchor: str | None
    anchor_date: str | None
    paths: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    commits_since: int | None = None
    changed: list[str] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return bool(self.changed or self.missing)

    def line(self) -> str:
        if self.anchor is None:
            return "unanchored (not committed yet)"
        parts = [f"anchored {self.anchor} ({self.anchor_date or '?'})"]
        if self.commits_since:
            parts.append(f"{self.commits_since} commits since touched {len(self.changed)}/{len(self.paths)} of its paths")
        if self.missing:
            parts.append(f"{len(self.missing)} referenced paths missing")
        if not self.stale:
            parts.append("current")
        return "; ".join(parts)

    def as_dict(self) -> dict[str, object]:
        return {
            "anchor": self.anchor,
            "anchor_date": self.anchor_date,
            "stale": self.stale,
            "commits_since": self.commits_since,
            "changed": self.changed,
            "missing": self.missing,
            "summary": self.line(),
        }


def referenced_paths(meta: dict[str, object], body: str, tracked: list[str]) -> tuple[list[str], list[str]]:
    """(resolved tracked paths, referenced paths that match nothing tracked)."""
    declared = meta.get("paths")
    tokens = list(declared) if isinstance(declared, list) else PATH_TOKEN.findall(body)
    tracked_set = set(tracked)
    found: list[str] = []
    missing: list[str] = []
    for token in dict.fromkeys(str(t).strip().removeprefix("./") for t in tokens):
        if not token:
            continue
        if token in tracked_set:
            found.append(token)
            continue
        # Shorthand like `render/quality.ts` for client/src/render/quality.ts.
        suffix = [t for t in tracked if t.endswith("/" + token)]
        if len(suffix) == 1:
            found.append(suffix[0])
        elif not suffix and "/" in token:
            # Bare filenames (`foo.ts`) are too ambiguous to call missing.
            missing.append(token)
    return found, missing


def staleness(root: Path, rel: str, meta: dict[str, object], body: str, tracked: list[str]) -> Staleness:
    sha = str(meta["sha"]) if meta.get("sha") else None
    date = str(meta.get("updated") or meta.get("created") or "")[:10] or None
    if sha is None:
        last = git.last_commit(root, rel)
        if last:
            sha, date = last
    paths, missing = referenced_paths(meta, body, tracked)
    result = Staleness(anchor=sha, anchor_date=date, paths=paths, missing=missing)
    if sha:
        changed = git.changed_since(root, sha, paths)
        if changed:
            result.commits_since, result.changed = changed
    return result
