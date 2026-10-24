"""check_design: compare what a capture measured against DESIGN.md.

Reports facts, not taste. Every finding names the measured value, how often it is
used, and the nearest spec value, so an agent or a person can decide whether the
spec or the app is wrong.
"""

from __future__ import annotations

import re
from typing import Any

from . import measure
from .spec import DesignSpec, _first_family, _px

PX_TOLERANCE = 0.5
MIN_SPACING_USES = 5  # spacing is noisy (1px borders, odd margins): only report values in real use


def _nearest_color(color: tuple[int, int, int, float], spec: DesignSpec) -> tuple[str, float]:
    best = min(((measure.delta_e(color, v), name) for name, v in spec.color_values.items()),
               default=(999.0, ""))
    return best[1], round(best[0], 1)


def _nearest_px(value: float, scale: dict[str, float]) -> tuple[str, float] | None:
    if not scale:
        return None
    name, px = min(scale.items(), key=lambda kv: abs(kv[1] - value))
    return name, px


def _palette_findings(extraction: dict[str, Any], spec: DesignSpec) -> dict[str, Any]:
    variables = measure.var_names(extraction.get("root_vars", {}))
    counts = extraction.get("counts", {})
    on, off, total = 0, [], 0
    used_spec: set[str] = set()
    for role in measure.COLOR_GROUPS:
        for cluster in measure.cluster_colors(counts.get(role, {})):
            total += cluster["count"]
            name, distance = _nearest_color(cluster["_rgba"], spec)
            if distance < measure.SAME_COLOR:
                on += cluster["count"]
                used_spec.add(name)
            else:
                off.append({"hex": cluster["hex"], "role": role, "count": cluster["count"],
                            "vars": measure.name_for(cluster["_rgba"], variables),
                            "nearest_spec": f"{name} {spec.colors.get(name, '')}".strip(), "delta_e": distance})
    off.sort(key=lambda f: -f["count"])
    return {
        "on_palette_pct": round(100 * on / total, 1) if total else None,
        "off_palette": off[:15],
        "spec_colors_unused": sorted(set(spec.colors) - used_spec),
    }


def _scale_findings(counts: dict[str, int], scale: dict[str, float], min_uses: int = 1) -> list[dict[str, Any]]:
    findings = []
    for entry in measure.scale(counts):
        px = _px(entry["value"])
        if px is None or entry["count"] < min_uses:
            continue  # 50%, 9999px pills and the like aren't on a px scale
        nearest = _nearest_px(px, scale)
        if nearest and abs(nearest[1] - px) > PX_TOLERANCE:
            findings.append({"value": entry["value"], "count": entry["count"],
                             "nearest_spec": f"{nearest[0]} {nearest[1]:g}px"})
    return findings


def _component_findings(extraction: dict[str, Any], spec: DesignSpec,
                        mapping: dict[str, str]) -> dict[str, Any]:
    measured = {c["key"]: c for c in extraction.get("components", [])}
    by_selector = {c.get("selector"): c for c in extraction.get("components", [])}
    checked, unmapped, not_found = [], [], []
    for name, props in spec.components.items():
        target = mapping.get(name)
        if not target:
            # An explicit data-component / data-testid hook: extract.js uses its value as the key.
            hook = measured.get(name)
            if not hook or not str(hook.get("selector", "")).startswith("[data-"):
                unmapped.append(name)
                continue
            comp = hook
        else:
            comp = measured.get(target) or by_selector.get(target)
            if not comp:
                not_found.append({"spec": name, "mapped_to": target})
                continue
        styles = comp.get("styles") or {}
        diffs = {}
        for prop, field in (("backgroundColor", "background"), ("textColor", "color")):
            if prop in props:
                want = measure.parse_color(spec.resolve(props[prop]))
                own = styles.get(field)
                # Transparent elements show an ancestor's background; compare what is seen.
                seen = own or (styles.get("effective_background") if field == "background" else None)
                got = measure.parse_color(seen or "")
                if want and (not got or measure.delta_e(want, got) >= measure.SAME_COLOR):
                    diffs[prop] = {"spec": measure.to_hex(want), "measured": measure.to_hex(got) if got else None,
                                   **({"inherited": True} if seen and not own else {})}
        for prop, field in (("rounded", "radius"), ("padding", "padding")):
            if prop in props:
                want = _px(spec.resolve(props[prop]))
                raw = (styles.get(field) or "0px").split()[0]
                got = _px(raw)
                if want is not None and (got is None or abs(want - got) > PX_TOLERANCE):
                    diffs[prop] = {"spec": f"{want:g}px", "measured": raw}
        if "typography" in props:
            ref = re.match(r"^\{typography\.([\w-]+)\}$", props["typography"].strip())
            typo = spec.typography.get(ref.group(1)) if ref else None
            if typo and typo.get("fontSize"):
                want = _px(typo["fontSize"])
                got = _px(styles.get("font_size") or "")
                if want is not None and (got is None or abs(want - got) > PX_TOLERANCE):
                    diffs["fontSize"] = {"spec": f"{want:g}px", "measured": styles.get("font_size")}
        checked.append({"spec": name, "component": comp["key"], "matches": not diffs, "differences": diffs})
    return {"checked": checked, "unmapped": unmapped, "mapped_but_not_found": not_found}


def _luminance(rgba: tuple[int, int, int, float]) -> float:
    def channel(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgba[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _over(fg: tuple[int, int, int, float], bg: tuple[int, int, int, float]) -> tuple[int, int, int, float]:
    """Composite a translucent text colour over its background, as the eye sees it."""
    a = fg[3]
    return (round(fg[0] * a + bg[0] * (1 - a)), round(fg[1] * a + bg[1] * (1 - a)),
            round(fg[2] * a + bg[2] * (1 - a)), 1.0)


def contrast_ratio(fg: tuple[int, int, int, float], bg: tuple[int, int, int, float]) -> float:
    lighter, darker = sorted((_luminance(_over(fg, bg)), _luminance(bg)), reverse=True)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def contrast_findings(extraction: dict[str, Any]) -> list[dict[str, Any]]:
    """WCAG 2 contrast of each component's text against the background it actually shows.
    Large text (>= 24px, or >= 18.66px at weight 700+) needs 3:1, other text 4.5:1."""
    findings = []
    for comp in extraction.get("components", []):
        if comp.get("has_text") is False:
            continue  # dots, icons, dividers: no text to read (older captures lack the flag)
        styles = comp.get("styles") or {}
        fg = measure.parse_color(styles.get("color") or "")
        bg = measure.parse_color(styles.get("background") or styles.get("effective_background") or "")
        if not fg or not bg or bg[3] < 0.999:
            continue  # no opaque backdrop measured: can't judge honestly
        size = _px(styles.get("font_size") or "") or 16.0
        weight = int(str(styles.get("font_weight") or "400")[:3]) if str(styles.get("font_weight") or "400")[:3].isdigit() else 400
        large = size >= 24 or (size >= 18.66 and weight >= 700)
        need = 3.0 if large else 4.5
        ratio = contrast_ratio(fg, bg)
        if ratio < need:
            findings.append({"component": comp["key"], "count": comp.get("count"), "ratio": ratio, "needs": need,
                             "text": measure.to_hex(fg), "background": measure.to_hex(bg),
                             "font_size": styles.get("font_size")})
    return sorted(findings, key=lambda f: f["ratio"])[:20]


def check(extraction: dict[str, Any], spec: DesignSpec, mapping: dict[str, str]) -> dict[str, Any]:
    counts = extraction.get("counts", {})
    families = [(_first_family(f), n) for f, n in counts.get("font_family", {}).items()]
    fonts_off = [{"family": f, "count": n} for f, n in sorted(families, key=lambda x: -x[1])
                 if f not in spec.font_families]
    result = {
        "palette": _palette_findings(extraction, spec),
        "font_sizes_off_scale": _scale_findings(counts.get("font_size", {}), spec.font_sizes),
        "radii_off_scale": _scale_findings(counts.get("radius", {}), spec.radii),
        "spacing_off_scale": _scale_findings(counts.get("spacing", {}), spec.spacings, MIN_SPACING_USES),
        "fonts_not_in_spec": fonts_off,
        "components": _component_findings(extraction, spec, mapping),
        "contrast_below_wcag_aa": contrast_findings(extraction),
    }
    comp = result["components"]
    result["summary"] = {
        "on_palette_pct": result["palette"]["on_palette_pct"],
        "off_palette_colors": len(result["palette"]["off_palette"]),
        "font_sizes_off_scale": len(result["font_sizes_off_scale"]),
        "radii_off_scale": len(result["radii_off_scale"]),
        "spacing_off_scale": len(result["spacing_off_scale"]),
        "components_checked": len(comp["checked"]),
        "components_differing": sum(not c["matches"] for c in comp["checked"]),
        "components_unmapped": len(comp["unmapped"]),
        "contrast_failures": len(result["contrast_below_wcag_aa"]),
    }
    return result
