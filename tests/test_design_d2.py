"""Spine Design D2: DESIGN.md spec, check_design, ensure_capture, BrowserOS adapter wiring."""

import base64
import copy
from pathlib import Path

import pytest

from conftest import write
from spine_design import browseros, store, tools
from spine_mcp.writes import SpineWriteError
from test_design import EXTRACTION, _png, _record, ui  # noqa: F401  (ui is a fixture)

DESIGN_MD = """---
version: alpha
name: Test
colors:
  primary: "#0A0C10"
  border: "#202630"
  neutral: "#D8DEE9"
  muted: "#697386"
typography:
  body-md:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: 0.875rem
  label:
    fontSize: 11px
rounded:
  sm: 4px
  md: 6px
spacing:
  sm: 8px
components:
  session-sidebar:
    backgroundColor: "{colors.primary}"
    rounded: "{rounded.md}"
  rail-button:
    rounded: "{rounded.sm}"
---
# Test design
"""


def test_spec_parser_resolves_references(project: Path) -> None:
    from spine_design.spec import load_spec
    write(project, "DESIGN.md", DESIGN_MD)
    spec = load_spec(project)
    assert spec.colors["border"] == "#202630" and spec.font_sizes == {"body-md": 14.0, "label": 11.0}
    assert spec.radii == {"sm": 4.0, "md": 6.0} and spec.font_families == {"inter"}
    assert spec.resolve(spec.components["session-sidebar"]["rounded"]) == "6px"
    write(project, "DESIGN.md", "# Prose only\n\nAmber for objectives.\n")
    assert load_spec(project) is None


def test_check_design_reports_measured_drift(ui: Path, tmp_path: Path) -> None:
    write(ui, "DESIGN.md", DESIGN_MD)
    write(ui, ".agent/design/spine-design.toml",
          '[components]\nsession-sidebar = "div.session-list"\nrail-button = "button.rail-button"\n')
    data = copy.deepcopy(EXTRACTION)
    data["components"][0]["styles"]["effective_background"] = "rgb(21, 26, 34)"
    data["components"][1]["styles"]["effective_background"] = "rgb(10, 12, 16)"
    _record(ui, tmp_path, extraction=data)
    view = tools.check_design(str(ui))["views"]["root-400x300-dark-default"]
    s = view["summary"]
    assert 0 < s["on_palette_pct"] < 100 and s["off_palette_colors"] >= 1
    off = {f["hex"]: f for f in view["palette"]["off_palette"]}
    assert "#151a22" in off and off["#151a22"]["role"] == "background"
    assert {f["value"] for f in view["font_sizes_off_scale"]} == {"13px"}  # 11px is on the label scale
    assert view["radii_off_scale"] == [{"value": "5px", "count": 11, "nearest_spec": "sm 4px"}]
    checked = {c["spec"]: c for c in view["components"]["checked"]}
    assert checked["rail-button"]["matches"]
    assert checked["session-sidebar"]["differences"] == {"rounded": {"spec": "6px", "measured": "5px"}}


def test_check_design_flags_undocumented_theme_and_inherited_background(ui: Path, tmp_path: Path) -> None:
    write(ui, "DESIGN.md", DESIGN_MD)
    write(ui, ".agent/design/spine-design.toml", '[components]\nsession-sidebar = "div.session-list"\n')
    light = copy.deepcopy(EXTRACTION)
    light["theme"] = "light"
    light["counts"] = {"background": {"rgb(255, 255, 255)": 9}, "color": {"rgb(37, 48, 68)": 31}}
    light["components"][1]["styles"] = {"background": None, "effective_background": "rgb(247, 249, 252)",
                                        "radius": "6px"}
    _record(ui, tmp_path, extraction=light)
    view = tools.check_design(str(ui))["views"]["root-400x300-light-default"]
    assert view["summary"]["on_palette_pct"] == 0 and "not documented" in view["note"]
    diff = view["components"]["checked"][0]["differences"]["backgroundColor"]
    assert diff == {"spec": "#0a0c10", "measured": "#f7f9fc", "inherited": True}


def test_check_design_without_spec_or_captures(ui: Path) -> None:
    assert tools.check_design(str(ui))["spec"] is None
    write(ui, "DESIGN.md", DESIGN_MD)
    assert "no captures yet" in tools.check_design(str(ui))["note"]


def test_brief_carries_drift_summary(ui: Path, tmp_path: Path) -> None:
    write(ui, "DESIGN.md", DESIGN_MD)
    _record(ui, tmp_path)
    text = tools.design_brief(str(ui))["brief"]
    assert "## vs DESIGN.md" in text and "colour use on palette" in text and "radii off scale" in text


class _FakeCapture:
    def __init__(self, extraction, png=None, fail=None):
        self.calls, self.extraction, self.png, self.fail = [], extraction, png, fail

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.fail:
            raise browseros.AdapterError(self.fail)
        return browseros.Capture(extraction=copy.deepcopy(self.extraction), png_base64=self.png, ready=True,
                                 laps={"open": 1}, skipped_pixels=False)


def test_ensure_capture_reuses_fresh_and_recaptures_stale(ui: Path, tmp_path: Path) -> None:
    write(ui, ".agent/design/spine-design.toml",
          'base_url = "http://127.0.0.1:5197"\nwait_for = "#root *"\nmask = [".xterm"]\n')
    png = base64.b64encode(_png(tmp_path / "a.png").read_bytes()).decode()
    fake = _FakeCapture(EXTRACTION, png)
    first = tools.ensure_capture(str(ui), "hermes", theme="dark", viewport="400x300", capture_fn=fake)
    assert first["action"] == "captured" and first["pixels"] and first["reason"] == "no capture of this view yet"
    url, kwargs = fake.calls[0]
    assert url == "http://127.0.0.1:5197/" and kwargs["viewport"] == (400, 300)
    assert kwargs["theme"] == "dark" and kwargs["color_scheme"] == "dark" and kwargs["mask"] == [".xterm"]
    assert kwargs["wait_for"] == "#root *" and store.captures(ui)[0]["adapter"] == "browseros"

    again = tools.ensure_capture(str(ui), "hermes", theme="dark", viewport="400x300", capture_fn=fake)
    assert again["action"] == "reused" and len(fake.calls) == 1  # no browser for a fresh view

    write(ui, "src/styles.css", ".session-list { border-radius: 6px; }\n")
    stale = tools.ensure_capture(str(ui), "hermes", theme="dark", viewport="400x300", capture_fn=fake)
    assert stale["action"] == "captured" and "src/styles.css" in stale["reason"] and len(fake.calls) == 2


def test_ensure_capture_falls_back_to_stale_and_guards_hosts(ui: Path, tmp_path: Path) -> None:
    write(ui, ".agent/design/spine-design.toml", 'base_url = "http://127.0.0.1:5197"\n')
    tools.ensure_capture(str(ui), "codex", theme="dark", viewport="400x300", capture_fn=_FakeCapture(EXTRACTION))
    write(ui, "src/styles.css", "/* changed */\n")
    down = _FakeCapture(EXTRACTION, fail="BrowserOS neo not reachable")
    result = tools.ensure_capture(str(ui), "codex", theme="dark", viewport="400x300", capture_fn=down)
    assert result["action"] == "stale" and "not reachable" in result["error"] and "styles.css" in result["reason"]
    with pytest.raises(SpineWriteError, match="allowed_hosts"):
        tools.ensure_capture(str(ui), "codex", url="https://example.com/", capture_fn=down)
    write(ui, ".agent/design/spine-design.toml", "pixels = true\n")
    with pytest.raises(SpineWriteError, match="no URL"):
        tools.ensure_capture(str(ui), "codex", capture_fn=down)


def test_browseros_run_code_and_result_parsing() -> None:
    code = browseros.build_run_code("http://127.0.0.1:5197/", (1280, 800), "dark", "data-theme", "dark",
                                    "#root *", True, [".xterm"])
    assert '"viewport": [1280, 800]' in code and '"mask": [".xterm"]' in code
    assert "__spineDesignUnmask" in code and "const VERSION = 1" in code  # the real extract.js, inlined
    assert "Spine Design extraction script" not in code  # comment header stripped for evaluate
    assert browseros.parse_run_result('ok\nreturn: {"a": 1}') == {"a": 1}
    assert browseros.parse_run_result('{"ok": true, "value": {"a": 2}}') == {"a": 2}
    with pytest.raises(browseros.AdapterError):
        browseros.parse_run_result('{"ok": false, "error": "boom"}')
    group = BaseExceptionGroup("outer", [ValueError("x"), BaseExceptionGroup("inner", [browseros.AdapterError("deep")])])
    assert str(browseros._first(group, browseros.AdapterError)) == "deep"


def test_browseros_unreachable_is_a_clear_error() -> None:
    with pytest.raises(browseros.AdapterError, match="not reachable"):
        browseros.capture("http://127.0.0.1:5197/", endpoint="http://127.0.0.1:9/mcp")
