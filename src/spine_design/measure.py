"""Turn raw extraction counts into design tokens: colours clustered by perceptual
distance, size scales, the app's own variable names, and comparisons between captures."""

from __future__ import annotations

import math
import re
from typing import Any

COLOR_GROUPS = ("background", "color", "border")
SCALE_GROUPS = ("font_size", "spacing", "radius", "line_height")
SAME_COLOR = 3.0      # CIE76 ΔE: below ~2-3 people can't tell two colours apart
VAR_MATCH = 2.0

RGB_RE = re.compile(r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)")
HEX_RE = re.compile(r"#([0-9a-fA-F]{3,8})\b")


def parse_color(value: str) -> tuple[int, int, int, float] | None:
    value = (value or "").strip()
    match = RGB_RE.search(value)
    if match:
        r, g, b = (round(float(match.group(i))) for i in (1, 2, 3))
        alpha = match.group(4)
        a = 1.0 if alpha is None else (float(alpha[:-1]) / 100 if alpha.endswith("%") else float(alpha))
        return r, g, b, a
    match = HEX_RE.search(value)
    if match:
        h = match.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) in (6, 8):
            a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a
    return None


def to_hex(rgba: tuple[int, int, int, float]) -> str:
    r, g, b, a = rgba
    base = f"#{r:02x}{g:02x}{b:02x}"
    return base if a >= 0.999 else f"{base}{round(a * 255):02x}"


def _lab(rgba: tuple[int, int, int, float]) -> tuple[float, float, float]:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in rgba[:3])
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: tuple[int, int, int, float], b: tuple[int, int, int, float]) -> float:
    if abs(a[3] - b[3]) > 0.05:
        return 100.0  # different opacity: treat as a different colour
    return math.dist(_lab(a), _lab(b))


def cluster_colors(counts: dict[str, int]) -> list[dict[str, Any]]:
    """Merge near-identical colours (ΔE < SAME_COLOR), most-used first."""
    parsed = [(parse_color(v), n) for v, n in counts.items()]
    parsed = sorted(((c, n) for c, n in parsed if c), key=lambda x: -x[1])
    clusters: list[dict[str, Any]] = []
    for color, n in parsed:
        for cluster in clusters:
            if delta_e(cluster["_rgba"], color) < SAME_COLOR:
                cluster["count"] += n
                if to_hex(color) != cluster["hex"]:
                    cluster["variants"].append(to_hex(color))
                break
        else:
            clusters.append({"hex": to_hex(color), "count": n, "variants": [], "_rgba": color})
    return clusters


def var_names(root_vars: dict[str, str]) -> list[tuple[str, tuple[int, int, int, float]]]:
    out = []
    for name, value in (root_vars or {}).items():
        color = parse_color(value)
        if color:
            out.append((name, color))
    return out


def name_for(color: tuple[int, int, int, float], variables: list[tuple[str, tuple[int, int, int, float]]]) -> list[str]:
    """CSS variables close to this colour, nearest first (#151a22 is --surface-3, not
    the neighbouring --surface-2 #11161e that is also within tolerance)."""
    near = [(delta_e(color, value), name) for name, value in variables]
    return [name for d, name in sorted(near) if d < VAR_MATCH]


def _px(value: str) -> float | None:
    match = re.fullmatch(r"\s*(-?[\d.]+)px\s*", value or "")
    return float(match.group(1)) if match else None


def scale(counts: dict[str, int]) -> list[dict[str, Any]]:
    """A size scale: px values snapped to whole pixels, most-used first; others kept as-is."""
    merged: dict[str, int] = {}
    for value, n in counts.items():
        px = _px(value)
        key = f"{round(px)}px" if px is not None else value
        merged[key] = merged.get(key, 0) + n
    return [{"value": v, "count": n} for v, n in sorted(merged.items(), key=lambda kv: -kv[1])]


def tokens(extraction: dict[str, Any], top: int = 12) -> dict[str, Any]:
    """The design tokens a capture shows in use."""
    counts = extraction.get("counts", {})
    variables = var_names(extraction.get("root_vars", {}))
    out: dict[str, Any] = {"theme": extraction.get("theme"), "colors": {}, "scales": {}}
    for group in COLOR_GROUPS:
        out["colors"][group] = [
            {"hex": c["hex"], "count": c["count"], "vars": name_for(c["_rgba"], variables),
             **({"variants": c["variants"][:4]} if c["variants"] else {})}
            for c in cluster_colors(counts.get(group, {}))[:top]
        ]
    for group in SCALE_GROUPS:
        out["scales"][group] = scale(counts.get(group, {}))[:top]
    out["fonts"] = [{"family": f, "count": n} for f, n in
                    sorted(counts.get("font_family", {}).items(), key=lambda kv: -kv[1])[:6]]
    out["font_weights"] = scale(counts.get("font_weight", {}))[:6]
    out["shadows"] = [{"value": s, "count": n} for s, n in
                      sorted(counts.get("shadow", {}).items(), key=lambda kv: -kv[1])[:4]]
    out["root_vars"] = len(extraction.get("root_vars", {}))
    return out


def _normalise(group: str, counts: dict[str, int]) -> dict[str, int]:
    if group in COLOR_GROUPS:
        return {c["hex"]: c["count"] for c in cluster_colors(counts)}
    if group in SCALE_GROUPS:
        return {s["value"]: s["count"] for s in scale(counts)}
    return dict(counts)


def token_changes(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Token values that appeared, disappeared, or changed use, from capture a to b."""
    changes: dict[str, Any] = {}
    groups = set(a.get("counts", {})) | set(b.get("counts", {}))
    for group in sorted(groups):
        before = _normalise(group, a.get("counts", {}).get(group, {}))
        after = _normalise(group, b.get("counts", {}).get(group, {}))
        added = sorted(set(after) - set(before), key=lambda v: (-after[v], v))
        removed = sorted(set(before) - set(after), key=lambda v: (-before[v], v))
        shifted = sorted((v for v in set(before) & set(after) if before[v] != after[v]),
                         key=lambda v: (-abs(after[v] - before[v]), v))  # value breaks ties: deterministic
        if added or removed or shifted:
            entry: dict[str, Any] = {}
            if added:
                entry["added"] = [[v, after[v]] for v in added[:8]]
            if removed:
                entry["removed"] = [[v, before[v]] for v in removed[:8]]
            if shifted:
                # A value that stays in use but on more or fewer elements: 5px → 4px moves counts.
                entry["count_changed"] = [[v, before[v], after[v]] for v in shifted[:8]]
            changes[group] = entry
    return changes


STYLE_FIELDS = ("background", "color", "border", "radius", "padding", "gap", "font_size", "font_weight",
                "font_family", "shadow")


def component_changes(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    before = {c["key"]: c for c in a.get("components", [])}
    after = {c["key"]: c for c in b.get("components", [])}
    changed = []
    for key in sorted(set(before) & set(after)):
        diffs = {}
        for field in STYLE_FIELDS:
            old, new = (before[key].get("styles") or {}).get(field), (after[key].get("styles") or {}).get(field)
            if old != new:
                diffs[field] = [old, new]
        if before[key].get("count") != after[key].get("count"):
            diffs["count"] = [before[key].get("count"), after[key].get("count")]
        if diffs:
            changed.append({"key": key, "changes": diffs})
    return {
        "added": sorted(set(after) - set(before))[:20],
        "removed": sorted(set(before) - set(after))[:20],
        "changed": changed[:30],
    }


def overlapping(components: list[dict[str, Any]], region: list[int], limit: int = 3) -> list[str]:
    """Components under region [x, y, w, h], most specific first: the smallest
    intersecting instance wins, so a button ranks above the nav and main around it."""
    x, y, w, h = region
    hits: dict[str, int] = {}
    for comp in components:
        for bx, by, bw, bh in comp.get("bboxes", [])[:12]:
            if bx < x + w and x < bx + bw and by < y + h and y < by + bh:
                hits[comp["key"]] = min(hits.get(comp["key"], bw * bh), bw * bh)
    return [key for key, _ in sorted(hits.items(), key=lambda kv: kv[1])][:limit]
