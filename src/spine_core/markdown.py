"""Minimal markdown handling: frontmatter and `##` section chunking.

No YAML dependency: frontmatter supports `key: value`, `key: [a, b]`, and
leaves anything else as a raw string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Section:
    heading: str
    line_start: int  # 1-based, inclusive, counted in the original file
    line_end: int
    text: str

    @property
    def date(self) -> str | None:
        match = DATE_RE.match(self.heading)
        return match.group(1) if match else None


def normalise(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_frontmatter(text: str) -> tuple[dict[str, object], str, int]:
    """Return (frontmatter, body, number of lines the frontmatter occupied)."""
    text = normalise(text)
    if not text.startswith("---\n"):
        return {}, text, 0
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text, 0
    raw = text[4:end]
    after = text.find("\n", end + 4)
    body = text[after + 1 :] if after != -1 else ""
    consumed = text[: after + 1 if after != -1 else len(text)].count("\n")
    meta: dict[str, object] = {}
    for line in raw.split("\n"):
        key, sep, value = line.partition(":")
        if not sep or not key.strip() or key.startswith((" ", "\t")):
            continue
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = value.strip("'\"")
    return meta, body, consumed


def sections(text: str) -> tuple[dict[str, object], list[Section]]:
    """Split a markdown file into its preamble plus one Section per `## ` heading.

    Headings inside fenced code blocks are ignored.
    """
    meta, body, offset = split_frontmatter(text)
    lines = body.split("\n")
    out: list[Section] = []
    heading = ""
    start = 0
    fence: str | None = None

    def flush(end: int) -> None:
        chunk = "\n".join(lines[start:end]).strip()
        if chunk:
            out.append(Section(heading, start + 1 + offset, end + offset, chunk))

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else (fence or marker)
            continue
        if fence is None and line.startswith("## "):
            flush(i)
            heading = line[3:].strip()
            start = i
    flush(len(lines))

    if out and not out[0].heading:
        title = next((l[2:].strip() for l in lines if l.startswith("# ")), "")
        first = out[0]
        out[0] = Section(title or "(preamble)", first.line_start, first.line_end, first.text)
    return meta, out


def checkboxes(text: str) -> list[tuple[int, bool, str]]:
    """`- [ ]` / `- [x]` items in a section body as (line offset, done, text)."""
    items = []
    for offset, line in enumerate(normalise(text).split("\n")):
        match = re.match(r"\s*[-*] \[([ xX])\] (.+)", line)
        if match:
            items.append((offset, match.group(1) != " ", match.group(2).strip()))
    return items


def blocks(text: str) -> list[str]:
    """Top-level bullets (with their continuation lines) and paragraphs."""
    out: list[list[str]] = []
    for line in normalise(text).split("\n"):
        if not line.strip():
            if out and out[-1]:
                out.append([])
            continue
        starts_item = re.match(r"([-*]|\d+\.) ", line) is not None
        if starts_item or not out or not out[-1]:
            out.append([line])
        else:
            out[-1].append(line)
    return ["\n".join(b) for b in out if b]


def bullets(text: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^[-*] (.+)$", normalise(text), re.M)]
