"""Capture storage under `<project>/.agent/design/`.

- `spine-design.toml` (tracked, optional): project config.
- `cache/` (self-ignored): every capture, one JSON per capture plus an optional webp.
  Captures are cheap to retake, so they stay per host and expire.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from spine_core.index import IGNORED_DIRS
from spine_core.resolve import to_posix

DESIGN_DIR = ".agent/design"
DEFAULT_UI_PATHS = [
    "index.html",
    "src/**/*.css", "src/**/*.scss", "src/**/*.less",
    "src/**/*.tsx", "src/**/*.jsx", "src/**/*.vue", "src/**/*.svelte", "src/**/*.html",
    "client/**/*.css", "client/**/*.html",
    "styles/**/*.css", "public/**/*.css",
    "tailwind.config.*",
]


@dataclass
class Config:
    pixels: bool = True
    ui_paths: list[str] = field(default_factory=lambda: list(DEFAULT_UI_PATHS))
    ttl_days: int = 7
    cache_days: int = 30
    keep_per_view: int = 20
    mask: list[str] = field(default_factory=list)
    components: dict[str, str] = field(default_factory=dict)  # DESIGN.md name -> component key/selector
    # Self-capture (D2): where the running UI is, how to know it rendered, how themes switch.
    base_url: str = ""
    wait_for: str = "body"
    theme_attr: str = "data-theme"   # set on <html> per view theme; "" to use prefers-color-scheme only
    allowed_hosts: list[str] = field(default_factory=lambda: ["127.0.0.1", "localhost", "::1"])
    # Baselines are committed: keep them small and few.
    baseline_max_bytes: int = 150_000
    baseline_max: int = 40
    # D3: named setup recipes per state, and which adapter self-captures.
    states: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    adapter: str = "browseros"          # or "playwright" (headless; CI and Linux hosts)
    browser_channel: str = ""           # playwright: "chrome"/"msedge" uses an installed browser
    electron_app: str = "."             # electron: the app directory, relative to the project
    electron_executable: str = ""       # electron: default node_modules/electron/dist/electron(.exe)


def load_config(root: Path) -> Config:
    path = root / DESIGN_DIR / "spine-design.toml"
    if not path.is_file():
        return Config()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    cfg = Config()
    for name in ("pixels", "ttl_days", "cache_days", "keep_per_view", "baseline_max_bytes", "baseline_max"):
        if name in data:
            setattr(cfg, name, type(getattr(cfg, name))(data[name]))
    for name in ("base_url", "wait_for", "theme_attr", "adapter", "browser_channel", "electron_app",
                 "electron_executable"):
        if isinstance(data.get(name), str):
            setattr(cfg, name, data[name])
    for name in ("ui_paths", "mask", "allowed_hosts"):
        if isinstance(data.get(name), list):
            setattr(cfg, name, [str(v) for v in data[name]])
    if isinstance(data.get("components"), dict):
        cfg.components = {str(k): str(v) for k, v in data["components"].items()}
    from .recipes import load as load_states
    cfg.states = load_states(data)
    return cfg


@dataclass(frozen=True)
class View:
    route: str = "/"
    viewport: str = "default"
    theme: str = "default"
    state: str = "default"

    @classmethod
    def parse(cls, value: dict[str, Any] | str | None) -> "View":
        if isinstance(value, dict):
            known = {k: str(v) for k, v in value.items() if k in {"route", "viewport", "theme", "state"} and v}
            return cls(**known)
        if isinstance(value, str) and value:
            parts = value.split("|")
            if len(parts) > 1:
                return cls(*[p or d for p, d in zip(parts, ("/", "default", "default", "default"))])
        return cls()

    @property
    def slug(self) -> str:
        route = re.sub(r"[^a-z0-9]+", "-", self.route.lower()).strip("-") or "root"
        bits = [route, self.viewport, self.theme, self.state]
        return "-".join(re.sub(r"[^a-z0-9x]+", "-", b.lower()).strip("-") or "default" for b in bits)

    def as_dict(self) -> dict[str, str]:
        return {"route": self.route, "viewport": self.viewport, "theme": self.theme, "state": self.state}


def cache_dir(root: Path) -> Path:
    path = root / DESIGN_DIR / "cache"
    path.mkdir(parents=True, exist_ok=True)
    ignore = path / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    return path


def ui_files(root: Path, cfg: Config) -> dict[str, str]:
    """Short content hashes of every UI source file, so freshness can name what changed."""
    found: dict[str, str] = {}
    for pattern in cfg.ui_paths:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            rel = to_posix(root, path)
            if any(part in IGNORED_DIRS for part in rel.split("/")[:-1]):
                continue
            data = path.read_bytes().replace(b"\r\n", b"\n")
            found[rel] = hashlib.sha1(data).hexdigest()[:12]
    return dict(sorted(found.items()))


def ui_hash(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def captures(root: Path, view_slug: str | None = None) -> list[dict[str, Any]]:
    """Capture records, newest first."""
    base = cache_dir(root)
    folders = [base / view_slug] if view_slug else [p for p in base.iterdir() if p.is_dir()]
    out = []
    for folder in folders:
        for meta in folder.glob("*.json"):
            try:
                out.append(json.loads(meta.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return sorted(out, key=lambda c: c.get("taken", ""), reverse=True)


def save(root: Path, record: dict[str, Any]) -> Path:
    folder = cache_dir(root) / record["view_slug"]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{record['id']}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=1), encoding="utf-8")
    tmp.replace(target)
    return target


def image_path(root: Path, record: dict[str, Any]) -> Path | None:
    """Where a record's screenshot lives: the tracked baselines folder or the local cache."""
    if not record.get("image"):
        return None
    folder = baseline_dir(root) if record.get("baseline") else cache_dir(root) / record["view_slug"]
    return folder / record["image"]["file"]


# -- baselines: approved references, tracked in git ------------------------------

BASELINES = f"{DESIGN_DIR}/baselines"
INVENTORY = f"{DESIGN_DIR}/inventory.json"


def baseline_dir(root: Path) -> Path:
    path = root / BASELINES
    path.mkdir(parents=True, exist_ok=True)
    return path


def baselines(root: Path) -> dict[str, dict[str, Any]]:
    folder = root / BASELINES
    found = {}
    if folder.is_dir():
        for meta in sorted(folder.glob("*.json")):
            try:
                found[meta.stem] = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return found


def baseline(root: Path, view_slug: str) -> dict[str, Any] | None:
    return baselines(root).get(view_slug)


def delete(root: Path, record: dict[str, Any]) -> None:
    folder = cache_dir(root) / record["view_slug"]
    for suffix in (".json", ".webp"):
        (folder / f"{record['id']}{suffix}").unlink(missing_ok=True)


def prune(root: Path, cfg: Config, view_slug: str) -> int:
    """Retention: keep the newest `keep_per_view`, and nothing older than `cache_days`."""
    cutoff = (now() - timedelta(days=cfg.cache_days)).isoformat()
    removed = 0
    for n, record in enumerate(captures(root, view_slug)):
        if n >= cfg.keep_per_view or record.get("taken", "") < cutoff:
            delete(root, record)
            removed += 1
    return removed
