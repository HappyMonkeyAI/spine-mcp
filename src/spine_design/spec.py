"""Read a project's design spec: DESIGN.md YAML-frontmatter tokens.

Supports the subset design token files use: nested `key: value` mappings by
indentation, quoted or bare scalars, and `{group.name}` references. No PyYAML
dependency; anything unparseable is skipped rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .measure import parse_color

REF = re.compile(r"^\{([\w.-]+)\}$")


def parse_frontmatter_yaml(text: str) -> dict[str, Any]:
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw in text[4:end].split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#") or raw.lstrip().startswith("- "):
            continue
        indent = len(raw) - len(raw.lstrip())
        key, sep, value = raw.strip().partition(":")
        if not sep:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else root
        value = value.strip()
        if not value:
            child: dict[str, Any] = {}
            parent[key.strip()] = child
            stack.append((indent, child))
        else:
            parent[key.strip()] = value.strip("'\"")
    return root


def _px(value: Any, root_px: float = 16.0) -> float | None:
    match = re.fullmatch(r"\s*(-?[\d.]+)\s*(px|rem|em)?\s*", str(value))
    if not match:
        return None
    number = float(match.group(1))
    return number * root_px if match.group(2) in {"rem", "em"} else number


@dataclass
class DesignSpec:
    path: str
    colors: dict[str, str] = field(default_factory=dict)
    typography: dict[str, dict[str, str]] = field(default_factory=dict)
    rounded: dict[str, str] = field(default_factory=dict)
    spacing: dict[str, str] = field(default_factory=dict)
    components: dict[str, dict[str, str]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def resolve(self, value: str) -> str:
        """`{colors.border}` → its value; anything else unchanged."""
        match = REF.match(str(value).strip())
        if not match:
            return str(value)
        node: Any = self.raw
        for part in match.group(1).split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return self.resolve(node) if isinstance(node, str) else str(value)

    @property
    def color_values(self) -> dict[str, tuple[int, int, int, float]]:
        return {name: c for name, v in self.colors.items() if (c := parse_color(v))}

    @property
    def font_sizes(self) -> dict[str, float]:
        return {name: px for name, t in self.typography.items() if (px := _px(t.get("fontSize")))}

    @property
    def font_families(self) -> set[str]:
        return {_first_family(t["fontFamily"]) for t in self.typography.values() if t.get("fontFamily")}

    @property
    def radii(self) -> dict[str, float]:
        return {name: px for name, v in self.rounded.items() if (px := _px(v)) is not None}

    @property
    def spacings(self) -> dict[str, float]:
        return {name: px for name, v in self.spacing.items() if (px := _px(v)) is not None}


def _first_family(stack: str) -> str:
    return stack.split(",")[0].strip().strip("'\"").lower()


def load_spec(root: Path) -> DesignSpec | None:
    """DESIGN.md (any case) at the project root, if it has token frontmatter."""
    path = next((p for p in sorted(root.glob("*.md")) if p.name.lower() == "design.md"), None)
    if not path:
        return None
    data = parse_frontmatter_yaml(path.read_text(encoding="utf-8", errors="replace"))
    if not any(isinstance(data.get(k), dict) for k in ("colors", "typography", "rounded", "spacing", "components")):
        return None

    def table(name: str) -> dict[str, Any]:
        value = data.get(name)
        return value if isinstance(value, dict) else {}

    return DesignSpec(
        path=path.name,
        colors={k: v for k, v in table("colors").items() if isinstance(v, str)},
        typography={k: v for k, v in table("typography").items() if isinstance(v, dict)},
        rounded={k: v for k, v in table("rounded").items() if isinstance(v, str)},
        spacing={k: v for k, v in table("spacing").items() if isinstance(v, str)},
        components={k: v for k, v in table("components").items() if isinstance(v, dict)},
        raw=data,
    )
