"""Spine Design D4: WCAG contrast, HTML Design Advisor hand-off, Spine brief integration."""

import copy
import shutil
from pathlib import Path

import pytest

from conftest import write
from spine_design import tools
from spine_design.check import contrast_findings, contrast_ratio
from spine_design.measure import parse_color
from spine_design.summary import brief_lines
from spine_mcp import core
from spine_mcp.writes import SpineWriteError
from test_design import EXTRACTION, _png, _record, ui  # noqa: F401  (ui is a fixture)
from test_design_d2 import DESIGN_MD


def _comp(key, color, background=None, effective=None, size="13px", weight="400", has_text=True):
    return {"key": key, "count": 1, "has_text": has_text,
            "styles": {"color": color, "background": background, "effective_background": effective,
                       "font_size": size, "font_weight": weight}}


def test_contrast_ratio_matches_wcag_reference_values() -> None:
    assert contrast_ratio(parse_color("#000000"), parse_color("#ffffff")) == 21.0
    assert contrast_ratio(parse_color("#777777"), parse_color("#ffffff")) == 4.48
    # 50% black over white composites to mid grey before measuring.
    assert contrast_ratio(parse_color("rgba(0, 0, 0, 0.5)"), parse_color("#ffffff")) == \
        contrast_ratio(parse_color("#808080"), parse_color("#ffffff"))


def test_contrast_findings_judge_only_text_with_an_opaque_backdrop() -> None:
    extraction = {"components": [
        _comp("span.dim", "rgb(78, 89, 105)", effective="rgb(13, 16, 21)", size="9px"),     # 2.68:1 fails
        _comp("h1.title", "#777777", background="#ffffff", size="24px"),                    # large: 3:1 passes
        _comp("p.body", "#777777", background="#ffffff"),                                   # 4.48:1 fails
        _comp("span.dot", "#8b93a3", background="#9ece6a", has_text=False),                 # no text: skipped
        _comp("span.float", "#000000", background="rgba(255, 255, 255, 0.5)"),              # translucent: skipped
    ]}
    found = {f["component"]: f for f in contrast_findings(extraction)}
    assert set(found) == {"span.dim", "p.body"}
    assert found["span.dim"]["ratio"] == 2.68 and found["span.dim"]["needs"] == 4.5
    assert list(found) == ["span.dim", "p.body"]  # worst first


def _themed(theme: str, bg: str, comps: list) -> dict:
    data = copy.deepcopy(EXTRACTION)
    data["theme"] = theme
    data["counts"]["background"] = {bg: 9}
    data["components"] = comps
    return data


def test_advisor_handoff_fills_recommend_design_from_measurements(ui: Path, tmp_path: Path) -> None:
    write(ui, "DESIGN.md", DESIGN_MD)
    write(ui, ".agent/design/spine-design.toml", "[states.palette]\nsteps = [ { press = \"Control+k\" } ]\n")
    dim = _comp("span.dim", "rgb(78, 89, 105)", effective="rgb(13, 16, 21)", size="9px")
    _record(ui, tmp_path, extraction=_themed("dark", "rgb(10, 12, 16)", [dim]))
    _record(ui, tmp_path, extraction=_themed("light", "rgb(255, 255, 255)",
                                             [_comp("span.dim", "#b8c6d8", background="#eef2f7")]))
    _record(ui, tmp_path, extraction=_themed("dark", "rgb(10, 12, 16)", [dim]), state="palette")
    handoff = tools.advisor_handoff(str(ui), "settings panel", audience="developers", desired_mood="calm")
    args = handoff["recommend_design"]
    assert set(args) == {"page_purpose", "audience", "desired_mood", "constraints", "accessibility_needs",
                         "required_interactions"}
    assert args["page_purpose"] == "settings panel" and args["required_interactions"] == "palette"
    assert "dark palette #0a0c10 (--surface-0)" in args["constraints"] and "light palette #ffffff" in args["constraints"]
    assert "spec DESIGN.md (tokens)" in args["constraints"]
    # The same component failing in several views counts once, at its worst ratio.
    assert args["accessibility_needs"].startswith("1 text components below WCAG AA contrast (worst: span.dim 1.54:1")
    assert len(args["constraints"]) <= 700 and len(handoff["get_design_guidance"]["brief"]) <= 1000
    with pytest.raises(SpineWriteError, match="page_purpose"):
        tools.advisor_handoff(str(ui), "  ")


def test_spine_brief_gains_a_design_section(ui: Path, tmp_path: Path) -> None:
    assert brief_lines(ui) == [] and "## design" not in core.session_brief(str(ui), budget_tokens=6000)["brief"]
    write(ui, "DESIGN.md", DESIGN_MD)
    _record(ui, tmp_path, image=_png(tmp_path / "a.png"))
    tools.approve_baseline(str(ui), "codex")
    lines = brief_lines(ui)
    assert lines[0].startswith("- 1 views captured here (0 stale vs UI source), 1 approved baselines")
    assert "on DESIGN.md palette" in lines[1]
    brief = core.session_brief(str(ui), budget_tokens=6000)["brief"]
    assert "## design (measured UI)" in brief and "spine-design MCP `design_brief`" in brief
    # Another host: no capture cache, only the tracked baseline. Spine still reports it.
    shutil.rmtree(ui / ".agent/design/cache")
    lines = brief_lines(ui)
    assert lines[0].startswith("- 0 views captured here") and "1 approved baselines" in lines[0]
    assert "root-400x300-dark-default" in lines[1]


def test_broken_design_cache_never_breaks_the_session_brief(ui: Path, tmp_path: Path, monkeypatch) -> None:
    _record(ui, tmp_path)
    import spine_design.summary as summary

    def boom(root):
        raise RuntimeError("corrupt cache")

    monkeypatch.setattr(summary, "brief_lines", boom)
    assert "Spine brief" in core.session_brief(str(ui))["brief"]
