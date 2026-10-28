"""Project root resolution and path sandboxing."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .git import run_git


class SpinePathError(ValueError):
    """A path argument escaped the project root or could not be resolved."""


def resolve_project(project: str | None = None) -> Path:
    """Resolve a project argument (path, registry name, or None for cwd) to its root.

    A directory inside a git checkout resolves to the checkout's top level.
    """
    if not project:
        candidate = Path.cwd()
    else:
        candidate = Path(project).expanduser()
        if not candidate.is_dir():
            registered = _lookup_registry(project)
            if registered is None:
                raise SpinePathError(f"project not found: {project}")
            candidate = registered

    candidate = candidate.resolve()
    toplevel = run_git(candidate, "rev-parse", "--show-toplevel")
    return Path(toplevel).resolve() if toplevel else candidate


def _key(value: object) -> str:
    """Compare names loosely: "MRPGRealms", "mrpg-realms" and "MRPG Realms" are one project."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _lookup_registry(name: str) -> Path | None:
    """Look a project name up in launcher-project-registry's registry.json, if configured.

    The registry's shape (launcher ADR 0001, registry_ops.py) is
    {"projects": [{"slug", "name", "path", "catalogue_name", ...}]}; a project is
    found by any of those names, compared loosely.
    """
    registry = os.environ.get("SPINE_REGISTRY")
    if not registry:
        return None
    try:
        data = json.loads(Path(registry).expanduser().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    projects = data.get("projects", data) if isinstance(data, dict) else data
    entries = projects.values() if isinstance(projects, dict) else projects
    wanted = _key(name)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        names = {_key(entry.get(k, "")) for k in ("name", "id", "slug", "catalogue_name")} - {""}
        if wanted in names:
            path = entry.get("path") or entry.get("location")
            if path and Path(path).expanduser().is_dir():
                return Path(path).expanduser()
    return None


def safe_path(root: Path, relative: str) -> Path:
    """Resolve a project-relative path, refusing anything that lands outside root."""
    if not relative or Path(relative).is_absolute() or relative.startswith(("/", "\\")):
        raise SpinePathError(f"path must be relative to the project root: {relative!r}")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise SpinePathError(f"path escapes the project root: {relative!r}")
    return resolved


def to_posix(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()
