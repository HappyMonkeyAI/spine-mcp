"""Disposable SQLite/FTS5 index over a project's spine documents.

The index lives in `<project>/.agent/spine/index.sqlite`. It is a cache: every
call re-stats the source files and re-chunks only the ones whose content changed.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .markdown import normalise, sections
from .resolve import to_posix

SCHEMA_VERSION = 1

DEFAULT_SOURCES = [
    # Every root-level doc: projects name them freely (ARCHITECTURE, ROADMAP, tasks.md, ...).
    # This includes each agent's instruction file (AGENTS, CLAUDE, HERMES, GEMINI, GROK),
    # so every agent's rules are searchable by every agent.
    "*.md",
    ".github/copilot-instructions.md",
    "docs/*.md",
    "docs/adr/*.md",
    "docs/plans/*.md",
    ".agent/memories/**/*.md",
    # Monorepo apps and packages: their own READMEs, agent files and docs.
    "apps/*/*.md",
    "apps/*/docs/*.md",
    "packages/*/README.md",
]

IGNORED_DIRS = {"node_modules", ".worktrees", ".git", "dist", "build", ".venv", "vendor"}

# Ordered: first matching prefix wins.
KINDS = [
    (".agent/memories/history/", "checkpoint"),
    (".agent/memories/architectural_decisions/", "decision"),
    (".agent/memories/codebase_insights/", "insight"),
    (".agent/memories/patterns_and_lessons", "lesson"),
    (".agent/memories/", "memory"),
    ("PROGRESS.md", "journal"),
    ("docs/adr/", "adr"),
    ("docs/plans/", "plan"),
]

MEMORY_KINDS = {"checkpoint", "decision", "insight", "lesson", "memory"}


def kind_for(path: str) -> str:
    return next((kind for prefix, kind in KINDS if path.startswith(prefix)), "doc")


@dataclass(frozen=True)
class Hit:
    path: str
    kind: str
    heading: str
    line_start: int
    line_end: int
    date: str | None
    snippet: str

    def as_dict(self) -> dict[str, object]:
        return {
            "source": f"{self.path}:{self.line_start}",
            "kind": self.kind,
            "heading": self.heading,
            "date": self.date,
            "snippet": self.snippet,
        }


class SpineIndex:
    def __init__(self, root: Path) -> None:
        self.root = root
        store = root / ".agent" / "spine"
        store.mkdir(parents=True, exist_ok=True)
        # Self-ignoring cache directory: no edit to the project's own .gitignore.
        ignore = store / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
        # Several agents (Claude, Codex, Hermes, ...) may share one index at once:
        # WAL lets readers run during a refresh, and refresh takes the write lock
        # up front (BEGIN IMMEDIATE) so two refreshes serialise instead of racing.
        self.db = sqlite3.connect(store / "index.sqlite", timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def __enter__(self) -> "SpineIndex":
        return self

    def __exit__(self, *exc: object) -> None:
        self.db.close()

    def _ensure_schema(self) -> None:
        if self.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION:
            return
        # Recheck under the write lock so two agents starting together don't
        # both rebuild (and drop each other's freshly created tables).
        self.db.execute("BEGIN IMMEDIATE")
        if self.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION:
            self.db.execute("COMMIT")
            return
        statements = f"""
            DROP TABLE IF EXISTS docs;
            DROP TABLE IF EXISTS chunks;
            DROP TABLE IF EXISTS chunks_fts;
            CREATE TABLE docs (
                path TEXT PRIMARY KEY, kind TEXT, sha256 TEXT,
                mtime_ns INTEGER, size INTEGER, chars INTEGER, meta TEXT
            );
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY, path TEXT, kind TEXT, heading TEXT,
                line_start INTEGER, line_end INTEGER, date TEXT, text TEXT
            );
            CREATE INDEX chunks_path ON chunks(path);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(heading, text, tokenize='porter unicode61');
            PRAGMA user_version = {SCHEMA_VERSION}
            """
        # executescript() would commit and drop the lock; run statements singly.
        for statement in statements.split(";"):
            if statement.strip():
                self.db.execute(statement)
        self.db.execute("COMMIT")

    def rebuild(self) -> dict[str, int]:
        """Drop everything and index from scratch."""
        self.db.execute("PRAGMA user_version = 0")
        self._ensure_schema()
        return self.refresh()

    # -- sources -----------------------------------------------------------

    def source_patterns(self) -> list[str]:
        config = self.root / ".agent" / "spine.toml"
        extra: list[str] = []
        if config.is_file():
            try:
                extra = list(tomllib.loads(config.read_text(encoding="utf-8")).get("extra_sources", []))
            except (OSError, tomllib.TOMLDecodeError):
                pass
        return DEFAULT_SOURCES + [str(p) for p in extra]

    def source_files(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        root = self.root.resolve()
        for pattern in self.source_patterns():
            for path in sorted(self.root.glob(pattern)):
                if path.is_file() and path.resolve().is_relative_to(root):
                    rel = to_posix(self.root, path)
                    if not any(part in IGNORED_DIRS for part in rel.split("/")[:-1]):
                        found.setdefault(rel, path)
        return found

    # -- refresh -----------------------------------------------------------

    def refresh(self) -> dict[str, int]:
        """Bring the index in line with the files on disk. Returns counts."""
        files = self.source_files()
        stats = {"scanned": len(files), "reindexed": 0, "removed": 0}

        self.db.execute("BEGIN IMMEDIATE")
        try:
            # Read under the write lock: another agent may have refreshed while we waited.
            known = {r["path"]: r for r in self.db.execute("SELECT path, sha256, mtime_ns, size FROM docs")}
            for rel in set(known) - set(files):
                self._drop(rel)
                stats["removed"] += 1

            for rel, path in files.items():
                stat = path.stat()
                row = known.get(rel)
                if row and row["mtime_ns"] == stat.st_mtime_ns and row["size"] == stat.st_size:
                    continue
                text = normalise(path.read_text(encoding="utf-8", errors="replace"))
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if row and row["sha256"] == digest:
                    self.db.execute(
                        "UPDATE docs SET mtime_ns=?, size=? WHERE path=?",
                        (stat.st_mtime_ns, stat.st_size, rel),
                    )
                    continue
                self._index(rel, text, digest, stat.st_mtime_ns, stat.st_size)
                stats["reindexed"] += 1
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")
        return stats

    def _drop(self, rel: str) -> None:
        ids = [r[0] for r in self.db.execute("SELECT id FROM chunks WHERE path=?", (rel,))]
        self.db.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(i,) for i in ids])
        self.db.execute("DELETE FROM chunks WHERE path=?", (rel,))
        self.db.execute("DELETE FROM docs WHERE path=?", (rel,))

    def _index(self, rel: str, text: str, digest: str, mtime_ns: int, size: int) -> None:
        self._drop(rel)
        kind = kind_for(rel)
        meta, parts = sections(text)
        kind = str(meta.get("kind") or kind) if kind in MEMORY_KINDS else kind
        self.db.execute(
            "INSERT INTO docs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rel, kind, digest, mtime_ns, size, len(text), json.dumps(meta)),
        )
        fallback_date = str(meta.get("updated") or meta.get("created") or "")[:10] or None
        for part in parts:
            cur = self.db.execute(
                "INSERT INTO chunks (path, kind, heading, line_start, line_end, date, text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rel, kind, part.heading, part.line_start, part.line_end, part.date or fallback_date, part.text),
            )
            self.db.execute(
                "INSERT INTO chunks_fts (rowid, heading, text) VALUES (?, ?, ?)",
                (cur.lastrowid, part.heading, part.text),
            )

    # -- queries -----------------------------------------------------------

    def docs(self) -> list[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM docs ORDER BY path"))

    def doc_meta(self, rel: str) -> dict[str, object]:
        row = self.db.execute("SELECT meta FROM docs WHERE path=?", (rel,)).fetchone()
        return json.loads(row["meta"]) if row else {}

    def chunks(self, *, path: str | None = None, kind: str | None = None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM chunks WHERE 1=1", []
        if path:
            sql += " AND path=?"
            args.append(path)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        return list(self.db.execute(sql + " ORDER BY path, line_start", args))

    def journal(self, limit: int) -> list[sqlite3.Row]:
        """Dated journal entries, newest first (file order breaks ties: journals are newest-first)."""
        return list(
            self.db.execute(
                "SELECT * FROM chunks WHERE kind='journal' AND date IS NOT NULL"
                " ORDER BY date DESC, line_start ASC LIMIT ?",
                (limit,),
            )
        )

    def search(
        self,
        query: str,
        *,
        kinds: list[str] | None = None,
        since: str | None = None,
        limit: int = 8,
        path: str | None = None,
    ) -> list[Hit]:
        terms = re.findall(r"\w+", query)
        if not terms:
            return []
        quoted = [f'"{t}"' for t in terms]
        # Chunks matching every term rank first; top up with any-term matches.
        hits = self._search(" ".join(quoted), terms, kinds, since, limit, path)
        if len(hits) < limit and len(terms) > 1:
            seen = {(h.path, h.line_start) for h in hits}
            hits += [
                h for h in self._search(" OR ".join(quoted), terms, kinds, since, limit, path)
                if (h.path, h.line_start) not in seen
            ][: limit - len(hits)]
        return hits

    def _search(
        self, match: str, terms: list[str], kinds: list[str] | None, since: str | None, limit: int, path: str | None
    ) -> list[Hit]:
        sql = (
            "SELECT c.*, snippet(chunks_fts, 1, '[', ']', ' … ', 48) AS snip"
            " FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
            " WHERE chunks_fts MATCH ?"
        )
        args: list[object] = [match]
        if kinds:
            sql += f" AND c.kind IN ({','.join('?' * len(kinds))})"
            args.extend(kinds)
        if since:
            sql += " AND c.date >= ?"
            args.append(since)
        if path:
            sql += " AND c.path LIKE ? ESCAPE '\\'"
            args.append(path.strip("/").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        sql += " ORDER BY bm25(chunks_fts, 5.0, 1.0) LIMIT ?"
        args.append(limit)
        return [
            Hit(r["path"], r["kind"], r["heading"], _matching_line(r["text"], r["line_start"], terms),
                r["line_end"], r["date"], r["snip"])
            for r in self.db.execute(sql, args)
        ]


def _matching_line(text: str, line_start: int, terms: list[str]) -> int:
    """The line of the chunk where the most query terms appear, so a citation
    points at the bullet, not the top of a long section."""
    words = [t.lower() for t in terms]
    best, best_score = line_start, 0
    for offset, line in enumerate(text.split("\n")):
        score = sum(w in line.lower() for w in words)
        if score > best_score:
            best, best_score = line_start + offset, score
    return best
