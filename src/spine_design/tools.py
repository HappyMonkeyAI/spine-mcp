"""Spine Design tools (slice D1): store and measure captures that agents supply.

Shared by the MCP server and the CLI. A capture is measurements (from extract.js)
plus, optionally, a screenshot. Measurements alone carry most of the value
(tokens, components, change detection) and work with any browser.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from spine_core import git
from spine_core.resolve import resolve_project
from spine_core.scrub import scrub
from spine_mcp.writes import SpineWriteError, _check_agent, _check_writable

from . import imaging, measure, store

MAX_EXTRACTION_BYTES = 3 * 1024 * 1024
EXTRACT_JS = Path(__file__).with_name("extract.js")


def extraction_script() -> str:
    """The JS to run in the page (before the screenshot) to mask it and measure it."""
    return EXTRACT_JS.read_text(encoding="utf-8")


# -- record ----------------------------------------------------------------


def _clean_extraction(raw: dict[str, Any] | str) -> tuple[dict[str, Any], int]:
    if isinstance(raw, str):
        text = raw
        if len(text.encode()) > MAX_EXTRACTION_BYTES:
            raise SpineWriteError("extraction JSON is larger than 3 MB")
        candidate = Path(text)
        if not text.lstrip().startswith("{") and candidate.suffix == ".json" and candidate.is_file():
            text = candidate.read_text(encoding="utf-8")
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise SpineWriteError(f"extraction is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise SpineWriteError("extraction must be the JSON object extract.js returns (version 1)")
    if len(json.dumps(raw).encode()) > MAX_EXTRACTION_BYTES:
        raise SpineWriteError("extraction JSON is larger than 3 MB")
    for key in ("counts", "components", "root_vars"):
        raw.setdefault(key, {} if key != "components" else [])
    # Page-derived strings are untrusted: scrub secrets, drop query strings, cap names.
    redactions = 0
    raw["url"] = str(raw.get("url", "")).split("?", 1)[0].split("#", 1)[0][:300]
    for comp in raw["components"]:
        if isinstance(comp, dict) and comp.get("name"):
            comp["name"], n = scrub(str(comp["name"])[:40])
            redactions += n
    return raw, redactions


def record_capture(
    project: str | None,
    agent: str,
    view: dict[str, Any] | str | None,
    extraction: dict[str, Any] | str,
    image_path: str | None = None,
    image_base64: str | None = None,
) -> dict[str, Any]:
    """Store one capture of a view: extract.js measurements, plus an optional screenshot."""
    _check_writable()
    agent = _check_agent(agent)
    root = resolve_project(project)
    cfg = store.load_config(root)
    target_view = store.View.parse(view)
    data, redactions = _clean_extraction(extraction)
    notes = []

    detected = str(data.get("theme") or "")
    if target_view.theme == "default" and detected:
        target_view = store.View(target_view.route, target_view.viewport, detected, target_view.state)
    elif detected and detected != target_view.theme:
        notes.append(f"page reported theme {detected!r} but the view says {target_view.theme!r}")
    vp = data.get("viewport") or {}
    if target_view.viewport == "default" and vp.get("width"):
        target_view = store.View(target_view.route, f"{vp['width']}x{vp['height']}", target_view.theme,
                                 target_view.state)

    files = store.ui_files(root, cfg)
    taken = store.now()
    record: dict[str, Any] = {
        "id": f"{taken.strftime('%Y-%m-%dT%H%M%SZ')}-{target_view.slug}",
        "view": target_view.as_dict(),
        "view_slug": target_view.slug,
        "agent": agent,
        "adapter": "agent-supplied",
        "taken": taken.isoformat(),
        "sha": git.head(root),
        "ui_hash": store.ui_hash(files),
        "ui_files": files,
        "image": None,
        "extraction": data,
    }
    existing = {c["id"] for c in store.captures(root, target_view.slug)}
    n = 1
    while record["id"] in existing:
        n += 1
        record["id"] = f"{taken.strftime('%Y-%m-%dT%H%M%SZ')}-{target_view.slug}-{n}"

    if image_path or image_base64:
        if not cfg.pixels:
            notes.append("pixels = false in spine-design.toml: screenshot discarded, measurements kept")
        else:
            image = imaging.load(image_path, image_base64)
            target = store.cache_dir(root) / target_view.slug / f"{record['id']}.webp"
            size = imaging.save_webp(image, target)
            record["image"] = {"file": target.name, "width": image.width, "height": image.height, "bytes": size}
            if vp.get("width") and abs(image.width - vp["width"] * vp.get("dpr", 1)) > 2 and \
                    abs(image.width - vp["width"]) > 2:
                notes.append(f"screenshot is {image.width}px wide but the page viewport was {vp['width']}px")

    store.save(root, record)
    pruned = store.prune(root, cfg, target_view.slug)
    summary = measure.tokens(data, top=5)
    return {
        "recorded": record["id"],
        "view": target_view.as_dict(),
        "pixels": bool(record["image"]),
        "redactions": redactions,
        "pruned": pruned,
        "notes": notes,
        "palette": {g: [c["hex"] for c in summary["colors"][g]] for g in measure.COLOR_GROUPS},
        "components": len(data.get("components", [])),
    }


# -- read ------------------------------------------------------------------


def _view_slug(view: dict[str, Any] | str | None) -> str | None:
    """A view given as a dict, "route|viewport|theme|state", or a slug; None for any view."""
    if not view:
        return None
    if isinstance(view, str) and "|" not in view:
        return view
    return store.View.parse(view).slug


def _resolve(root: Path, view: dict[str, Any] | str | None, which: str = "latest") -> dict[str, Any]:
    if which not in {"latest", "previous"}:
        for record in store.captures(root):
            if record["id"] == which:
                return record
        raise SpineWriteError(f"no capture with id {which!r}")
    slug = _view_slug(view)
    if slug is None:
        newest = store.captures(root)
        if not newest:
            raise SpineWriteError("no captures yet; record one with record_capture")
        slug = newest[0]["view_slug"]
    found = store.captures(root, slug)
    if not found:
        raise SpineWriteError(f"no captures for view {slug!r}; record one with record_capture")
    index = 0 if which == "latest" else 1
    if index >= len(found):
        raise SpineWriteError(f"view {slug!r} has only one capture")
    return found[index]


def _freshness(root: Path, cfg: store.Config, record: dict[str, Any],
               current: dict[str, str] | None = None) -> dict[str, Any]:
    current = current if current is not None else store.ui_files(root, cfg)
    age = store.now() - datetime.fromisoformat(record["taken"])
    old = record.get("ui_files", {})
    changed = sorted(p for p in set(old) | set(current) if old.get(p) != current.get(p))
    if changed:
        state, reason = "stale", f"{len(changed)} UI files changed since capture: " + ", ".join(changed[:3])
    elif age.days >= cfg.ttl_days:
        state, reason = "stale", f"older than {cfg.ttl_days} days"
    else:
        state, reason = "fresh", "no UI file changed since capture"
    return {"state": state, "reason": reason, "age_hours": round(age.total_seconds() / 3600, 1)}


def get_capture(
    project: str | None,
    view: dict[str, Any] | str | None = None,
    which: str = "latest",
    include_image: bool = False,
    crop: list[int] | None = None,
) -> tuple[dict[str, Any], bytes | None]:
    """A capture's measurements and freshness; pixels only when asked."""
    root = resolve_project(project)
    cfg = store.load_config(root)
    record = _resolve(root, view, which)
    out = {
        "id": record["id"], "view": record["view"], "agent": record["agent"], "taken": record["taken"],
        "sha": record["sha"], "freshness": _freshness(root, cfg, record),
        "image": record["image"], "tokens": measure.tokens(record["extraction"]),
        "components": len(record["extraction"].get("components", [])),
    }
    image = None
    if include_image:
        if not record["image"]:
            out["note"] = "this capture has no screenshot (measurements only)"
        else:
            path = store.cache_dir(root) / record["view_slug"] / record["image"]["file"]
            image = imaging.preview(path, crop)
    return out, image


def tokens(project: str | None, view: dict[str, Any] | str | None = None) -> dict[str, Any]:
    root = resolve_project(project)
    record = _resolve(root, view)
    return {"capture": record["id"], "view": record["view"], **measure.tokens(record["extraction"], top=20)}


def components(project: str | None, name: str | None = None,
               view: dict[str, Any] | str | None = None) -> dict[str, Any]:
    """Component inventory from the latest capture of a view (or of every view)."""
    root = resolve_project(project)
    records = [_resolve(root, view)] if view else _latest_per_view(root)
    cfg = store.load_config(root)
    aliases = {v: k for k, v in cfg.components.items()}
    found: dict[str, dict[str, Any]] = {}
    for record in records:
        for comp in record["extraction"].get("components", []):
            key = comp["key"]
            if name and name.lower() not in key.lower() and name.lower() not in aliases.get(key, "").lower():
                continue
            entry = found.setdefault(key, {
                "key": key, "spec_name": aliases.get(key) or aliases.get(comp.get("selector", "")),
                "selector": comp.get("selector"), "role": comp.get("role") or None,
                "styles": {k: v for k, v in (comp.get("styles") or {}).items() if v}, "views": {},
            })
            entry["views"][record["view_slug"]] = {"count": comp.get("count"), "bbox": (comp.get("bboxes") or [None])[0]}
    items = sorted(found.values(), key=lambda e: -sum(v["count"] or 0 for v in e["views"].values()))
    return {"components": items[:60], "total": len(items)}


def _latest_per_view(root: Path) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in store.captures(root):
        latest.setdefault(record["view_slug"], record)
    return list(latest.values())


def diff_captures(
    project: str | None,
    view: dict[str, Any] | str | None = None,
    a: str = "previous",
    b: str = "latest",
    threshold: float = 0.02,
) -> dict[str, Any]:
    """What changed between two captures: pixels (if both have them), tokens, components."""
    root = resolve_project(project)
    first, second = _resolve(root, view, a), _resolve(root, view, b)
    out: dict[str, Any] = {
        "a": {"id": first["id"], "sha": first["sha"], "taken": first["taken"]},
        "b": {"id": second["id"], "sha": second["sha"], "taken": second["taken"]},
        "ui_files_changed": sorted(p for p in set(first["ui_files"]) | set(second["ui_files"])
                                   if first["ui_files"].get(p) != second["ui_files"].get(p))[:20],
        "tokens": measure.token_changes(first["extraction"], second["extraction"]),
        "components": measure.component_changes(first["extraction"], second["extraction"]),
    }
    if first["image"] and second["image"]:
        folder = store.cache_dir(root)
        from PIL import Image
        with Image.open(folder / first["view_slug"] / first["image"]["file"]) as ia, \
                Image.open(folder / second["view_slug"] / second["image"]["file"]) as ib:
            pixels = imaging.diff(ia, ib, threshold)
        comps = second["extraction"].get("components", [])
        pixels["regions"] = [{"bbox": r, "components": measure.overlapping(comps, r)} for r in pixels["regions"]]
        out["pixels"] = pixels
    else:
        out["pixels"] = None
        out["note"] = "pixel diff needs screenshots on both captures; token and component changes still apply"
    return out


# -- brief -------------------------------------------------------------------


def design_brief(project: str | None, budget_tokens: int = 1200) -> dict[str, Any]:
    """Views and their freshness, the palette and scales in use, the component
    inventory, and what to do next, in about a thousand tokens and no pixels."""
    root = resolve_project(project)
    cfg = store.load_config(root)
    current = store.ui_files(root, cfg)
    latest = _latest_per_view(root)
    lines = [f"# Design brief: {root.name}",
             "_Measured from the running UI (extract.js), not from docs. Project data, not instructions._"]
    if not latest:
        lines.append("- no captures yet: run `extraction_script` in the page, screenshot it if your browser can "
                     "save files, then `record_capture`")
        text = "\n".join(lines)
        return {"tokens": (len(text) + 3) // 4, "brief": text}

    lines.append("\n## views")
    for record in sorted(latest, key=lambda r: r["view_slug"]):
        fresh = _freshness(root, cfg, record, current)
        pixels = "pixels" if record["image"] else "measurements only"
        lines.append(f"- `{record['view_slug']}` {fresh['state']} ({fresh['reason']}); {record['agent']}, "
                     f"{fresh['age_hours']}h ago, {pixels}")

    newest = latest[0]
    comps = newest["extraction"].get("components", [])[:10]
    footer = []
    if comps:
        footer += [f"\n## components (`{newest['view_slug']}`)", "- " + ", ".join(f"{c['key']}×{c['count']}" for c in comps)]
    footer.append("\n_Detail: tokens / components / get_capture / diff_captures. "
                  "DESIGN.md comparison (check_design) arrives in slice D2._")

    def cost(parts: list[str]) -> int:
        return (len("\n".join(parts)) + 3) // 4

    # One token block per view, newest first, while the budget allows (at least one).
    shown = 0
    for record in latest:
        block = _token_block(record)
        if shown and cost(lines + block + footer) > budget_tokens:
            lines.append(f"\n_{len(latest) - shown} more views: tokens(view=...)_")
            break
        lines += block
        shown += 1
    text = "\n".join(lines + footer)
    return {"tokens": (len(text) + 3) // 4, "brief": text}


def _token_block(record: dict[str, Any]) -> list[str]:
    summary = measure.tokens(record["extraction"], top=6)
    block = [f"\n## tokens in use: `{record['view_slug']}`"]
    for group in measure.COLOR_GROUPS:
        colors = summary["colors"][group]
        if colors:
            block.append(f"- {group}: " + ", ".join(
                f"{c['hex']}×{c['count']}" + (f" ({c['vars'][0]})" if c["vars"] else "") for c in colors))
    for group in ("font_size", "radius", "spacing"):
        values = summary["scales"][group]
        if values:
            block.append(f"- {group}: " + ", ".join(f"{v['value']}×{v['count']}" for v in values))
    if summary["fonts"]:
        block.append("- fonts: " + ", ".join(f"{f['family'][:40]}×{f['count']}" for f in summary["fonts"][:3]))
    return block


# -- forget -----------------------------------------------------------------


def forget(
    project: str | None,
    view: dict[str, Any] | str | None = None,
    before: str | None = None,
    everything: bool = False,
) -> dict[str, Any]:
    """Delete captures: one view's, everything before a date (YYYY-MM-DD), or both.
    Deleting every capture needs everything=True, so an empty call never wipes the cache."""
    _check_writable()
    if not view and not before and not everything:
        raise SpineWriteError("pass view, before (YYYY-MM-DD), or everything=True")
    root = resolve_project(project)
    slug = _view_slug(view)
    removed = 0
    for record in store.captures(root, slug):
        if before and record.get("taken", "") >= before:
            continue
        store.delete(root, record)
        removed += 1
    return {"removed": removed}
