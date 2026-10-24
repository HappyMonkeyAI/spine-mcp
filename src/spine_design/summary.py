"""A few lines about a project's design memory, for Spine's session brief.

Deliberately light: no image libraries, no browser. Works from the local capture
cache when present, otherwise from tracked baselines (so a host with no cache
still learns that design memory exists and what it says).
"""

from __future__ import annotations

from pathlib import Path

from . import store


def brief_lines(root: Path) -> list[str]:
    if not (root / store.DESIGN_DIR).is_dir():
        return []
    from . import check as checker
    from .spec import load_spec

    cfg = store.load_config(root)
    approved = store.baselines(root)
    latest: dict[str, dict] = {}
    for record in store.captures(root):
        latest.setdefault(record["view_slug"], record)
    if not latest and not approved:
        return []

    current = store.ui_files(root, cfg)
    stale = sum(1 for r in latest.values() if r.get("ui_files") != current)
    lines = [f"- {len(latest)} views captured here ({stale} stale vs UI source), {len(approved)} approved baselines"
             " — details: spine-design MCP `design_brief`"]

    spec = load_spec(root)
    if spec:
        source = {**approved, **latest}  # prefer this host's newer captures
        for slug, record in sorted(source.items())[:3]:
            s = checker.check(record["extraction"], spec, cfg.components)["summary"]
            bits = [f"{s['on_palette_pct']}% on {spec.path} palette" if s["on_palette_pct"] is not None else "no colours",
                    f"{s['font_sizes_off_scale']} font sizes off scale"]
            if s["components_checked"]:
                bits.append(f"{s['components_differing']}/{s['components_checked']} mapped components differ")
            if s.get("contrast_failures"):
                bits.append(f"{s['contrast_failures']} below WCAG AA contrast")
            lines.append(f"  - `{slug}`: " + ", ".join(bits))
    return lines
