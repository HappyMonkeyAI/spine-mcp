import base64
import copy
import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from conftest import commit, git, write
from spine_design import imaging, measure, store, tools
from spine_mcp.writes import SpineWriteError

# Shaped like the real ZeroGTerm dark-theme measurement (2026-10-23).
EXTRACTION = {
    "version": 1,
    "url": "http://127.0.0.1:5199/?token=abc#x",
    "viewport": {"width": 400, "height": 300, "dpr": 1},
    "theme": "dark",
    "elements": 166,
    "masked": 1,
    "root_vars": {"--surface-0": "#0a0c10", "--line-1": "#202630", "--text-1": "#d8dee9", "--radius": "4px"},
    "counts": {
        "background": {"rgb(10, 12, 16)": 6, "rgb(10, 12, 17)": 2, "rgb(21, 26, 34)": 6},
        "color": {"rgb(216, 222, 233)": 28, "rgb(105, 115, 134)": 46},
        "border": {"rgb(32, 38, 48)": 9},
        "font_family": {"Inter, ui-sans-serif, system-ui, sans-serif": 142},
        "font_size": {"13px": 115, "11px": 26, "12.8px": 3},
        "font_weight": {"400": 120, "600": 20},
        "line_height": {},
        "spacing": {"8px": 40, "4px": 22},
        "radius": {"4px": 21, "5px": 11},
        "shadow": {},
    },
    "components": [
        {"key": "button.rail-button", "selector": "button.rail-button", "role": "button", "name": "Sessions",
         "count": 8, "bboxes": [[0, 0, 40, 40], [0, 40, 40, 40]],
         "styles": {"background": "rgb(21, 26, 34)", "color": "rgb(216, 222, 233)", "radius": "4px"}},
        {"key": "div.session-list", "selector": "div.session-list", "role": "", "name": "",
         "count": 1, "bboxes": [[40, 0, 160, 300]],
         "styles": {"background": "rgb(10, 12, 16)", "radius": "5px"}},
    ],
}


def _png(path: Path, rect_color: str = "#151a22", rect: tuple = (60, 20, 180, 80)) -> Path:
    image = Image.new("RGB", (400, 300), "#0a0c10")
    ImageDraw.Draw(image).rectangle(rect, fill=rect_color)
    image.save(path)
    return path


@pytest.fixture
def ui(project: Path) -> Path:
    write(project, "src/styles.css", ":root { --surface-0: #0a0c10; }\n.session-list { border-radius: 5px; }\n")
    write(project, "src/App.tsx", "export const App = () => null;\n")
    commit(project, "ui")
    return project


def _record(project: Path, tmp_path: Path, extraction=None, image=None, **view) -> dict:
    return tools.record_capture(str(project), "codex", {"route": "/", **view},
                                extraction or copy.deepcopy(EXTRACTION),
                                image_path=str(image) if image else None)


def test_color_math_and_clustering() -> None:
    assert measure.parse_color("rgb(10, 12, 16)") == (10, 12, 16, 1.0)
    assert measure.parse_color("rgba(37, 48, 68, 0.2)") == (37, 48, 68, 0.2)
    assert measure.to_hex(measure.parse_color("#abc")) == "#aabbcc"
    clusters = measure.cluster_colors({"rgb(10, 12, 16)": 6, "rgb(10, 12, 17)": 2, "rgb(255, 0, 0)": 1})
    assert [(c["hex"], c["count"]) for c in clusters] == [("#0a0c10", 8), ("#ff0000", 1)]
    assert clusters[0]["variants"] == ["#0a0c11"]
    assert measure.delta_e((0, 0, 0, 1.0), (0, 0, 0, 0.5)) == 100.0  # opacity differs


def test_record_measures_and_names_tokens_with_css_vars(ui: Path, tmp_path: Path) -> None:
    result = _record(ui, tmp_path, image=_png(tmp_path / "a.png"))
    assert result["view"] == {"route": "/", "viewport": "400x300", "theme": "dark", "state": "default"}
    assert result["pixels"] and result["palette"]["background"][0] == "#0a0c10"
    found = tools.tokens(str(ui))
    bg = found["colors"]["background"][0]
    assert bg["hex"] == "#0a0c10" and bg["count"] == 8 and bg["vars"] == ["--surface-0"]
    assert found["scales"]["font_size"][0] == {"value": "13px", "count": 118}  # 12.8px snapped into 13px
    record = store.captures(ui)[0]
    assert record["extraction"]["url"] == "http://127.0.0.1:5199/"  # query and fragment dropped
    assert record["image"]["file"].endswith(".webp")
    status = git(ui, "status", "--porcelain", "-uall")
    assert ".agent/design/cache" not in status  # the capture cache ignores itself


def test_measurements_only_capture_and_pixels_off(ui: Path, tmp_path: Path) -> None:
    assert _record(ui, tmp_path)["pixels"] is False
    write(ui, ".agent/design/spine-design.toml", "pixels = false\n")
    result = _record(ui, tmp_path, image=_png(tmp_path / "a.png"))
    assert result["pixels"] is False and "pixels = false" in result["notes"][0]
    assert not list((ui / ".agent/design/cache").rglob("*.webp"))


def test_freshness_names_the_changed_ui_file(ui: Path, tmp_path: Path) -> None:
    _record(ui, tmp_path)
    assert tools.get_capture(str(ui))[0]["freshness"]["state"] == "fresh"
    write(ui, "README.md", "# backend-only change\n")  # not a UI file
    assert tools.get_capture(str(ui))[0]["freshness"]["state"] == "fresh"
    write(ui, "src/styles.css", ":root { --surface-0: #000000; }\n")  # uncommitted edit counts
    fresh = tools.get_capture(str(ui))[0]["freshness"]
    assert fresh["state"] == "stale" and "src/styles.css" in fresh["reason"]


def test_diff_reports_the_changed_region_and_component(ui: Path, tmp_path: Path) -> None:
    _record(ui, tmp_path, image=_png(tmp_path / "a.png"))
    changed = copy.deepcopy(EXTRACTION)
    changed["counts"]["radius"] = {"4px": 21, "6px": 11}
    changed["components"][1]["styles"]["radius"] = "6px"
    _record(ui, tmp_path, extraction=changed, image=_png(tmp_path / "b.png", rect_color="#2459a6"))
    result = tools.diff_captures(str(ui))
    pixels = result["pixels"]
    assert 0 < pixels["changed_pct"] < 20 and pixels["region_count"] == 1
    region = pixels["regions"][0]
    x, y, w, h = region["bbox"]
    assert x <= 60 and y <= 20 and x + w >= 180 and y + h >= 80  # covers the changed rectangle
    assert region["components"] == ["div.session-list"]  # and nothing else
    assert result["tokens"]["radius"] == {"added": [["6px", 11]], "removed": [["5px", 11]]}
    assert result["components"]["changed"] == [{"key": "div.session-list", "changes": {"radius": ["5px", "6px"]}}]


def test_identical_captures_diff_to_nothing(ui: Path, tmp_path: Path) -> None:
    _record(ui, tmp_path, image=_png(tmp_path / "a.png"))
    _record(ui, tmp_path, image=_png(tmp_path / "a.png"))
    result = tools.diff_captures(str(ui))
    assert result["pixels"]["changed_pct"] == 0 and result["pixels"]["regions"] == []
    assert result["tokens"] == {} and result["components"]["changed"] == []


def test_brief_is_text_only_and_within_budget(ui: Path, tmp_path: Path) -> None:
    assert "no captures yet" in tools.design_brief(str(ui))["brief"]
    _record(ui, tmp_path, image=_png(tmp_path / "a.png"))
    light = copy.deepcopy(EXTRACTION)
    light["theme"] = "light"
    light["counts"]["background"] = {"rgb(255, 255, 255)": 7}
    _record(ui, tmp_path, extraction=light)
    brief = tools.design_brief(str(ui))
    text = brief["brief"]
    assert brief["tokens"] <= 1200
    assert "`root-400x300-dark-default` fresh" in text and "`root-400x300-light-default` fresh" in text
    assert "#0a0c10×8 (--surface-0)" in text and "#ffffff×7" in text
    assert "measurements only" in text and "button.rail-button×8" in text
    tight = tools.design_brief(str(ui), budget_tokens=150)["brief"]
    assert tight.count("## tokens in use") == 1 and "more views" in tight


def test_get_capture_pixels_only_on_request_and_cropped(ui: Path, tmp_path: Path) -> None:
    _record(ui, tmp_path, image=_png(tmp_path / "a.png"))
    result, image = tools.get_capture(str(ui))
    assert image is None and result["image"]["width"] == 400
    _, image = tools.get_capture(str(ui), include_image=True, crop=[60, 20, 120, 60])
    assert Image.open(io.BytesIO(image)).size == (120, 60)


def test_secrets_in_names_are_scrubbed_and_inputs_validated(ui: Path, tmp_path: Path) -> None:
    leaky = copy.deepcopy(EXTRACTION)
    leaky["components"][0]["name"] = "ghp_" + "a" * 36
    result = _record(ui, tmp_path, extraction=leaky)
    assert result["redactions"] == 1
    stored = json.dumps(store.captures(ui)[0])
    assert "ghp_" not in stored
    with pytest.raises(SpineWriteError, match="version 1"):
        tools.record_capture(str(ui), "codex", None, {"counts": {}})
    with pytest.raises(SpineWriteError, match="agent is required"):
        tools.record_capture(str(ui), "", None, copy.deepcopy(EXTRACTION))
    bad = tmp_path / "fake.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(imaging.ImageError):
        _record(ui, tmp_path, image=bad)


def test_base64_images_and_retention(ui: Path, tmp_path: Path) -> None:
    png = _png(tmp_path / "a.png").read_bytes()
    tools.record_capture(str(ui), "hermes", None, copy.deepcopy(EXTRACTION),
                         image_base64="data:image/png;base64," + base64.b64encode(png).decode())
    assert store.captures(ui)[0]["image"]
    write(ui, ".agent/design/spine-design.toml", "keep_per_view = 2\n")
    for _ in range(3):
        _record(ui, tmp_path)
    assert len(store.captures(ui)) == 2


def test_forget_needs_an_explicit_scope(ui: Path, tmp_path: Path) -> None:
    _record(ui, tmp_path)
    with pytest.raises(SpineWriteError, match="everything=True"):
        tools.forget(str(ui))
    assert tools.forget(str(ui), everything=True) == {"removed": 1}
    assert store.captures(ui) == []


def test_components_inventory_with_spec_mapping(ui: Path, tmp_path: Path) -> None:
    write(ui, ".agent/design/spine-design.toml", '[components]\nsession-sidebar = "div.session-list"\n')
    _record(ui, tmp_path)
    inv = tools.components(str(ui))
    assert inv["total"] == 2 and inv["components"][0]["key"] == "button.rail-button"
    sidebar = tools.components(str(ui), name="session-sidebar")["components"]
    assert [c["key"] for c in sidebar] == ["div.session-list"] and sidebar[0]["spec_name"] == "session-sidebar"


def test_extraction_script_is_served_and_masks_non_destructively() -> None:
    script = tools.extraction_script()
    assert script.lstrip().startswith("//") and "__spineDesignUnmask" in script
    assert "el.value" not in script  # overlays, never edits form values
    assert "textContent.trim()" in script and "innerText" not in script  # no page text returned


def test_token_count_shifts_and_specific_attribution() -> None:
    a = {"counts": {"radius": {"4px": 21, "5px": 11}}}
    b = {"counts": {"radius": {"4px": 29, "5px": 3}}}
    assert measure.token_changes(a, b) == {"radius": {"count_changed": [["4px", 21, 29], ["5px", 11, 3]]}}
    comps = [{"key": "nav.rail", "bboxes": [[0, 0, 48, 800]]},
             {"key": "button.rail-button", "bboxes": [[8, 56, 32, 32]]},
             {"key": "main", "bboxes": [[0, 0, 1280, 800]]}]
    assert measure.overlapping(comps, [0, 48, 48, 48]) == ["button.rail-button", "nav.rail", "main"]


def test_subtle_dark_ui_change_is_detected(tmp_path: Path) -> None:
    # ZeroGTerm: transparent rail button on #0d1015 gains --surface-3 #151a22 (8/255 per channel).
    a = Image.new("RGB", (200, 100), "#0d1015")
    b = a.copy()
    ImageDraw.Draw(b).rectangle((8, 8, 40, 40), fill="#151a22")
    for name, image in (("a", a), ("b", b)):
        imaging.save_webp(image, tmp_path / f"{name}.webp")
    with Image.open(tmp_path / "a.webp") as ia, Image.open(tmp_path / "b.webp") as ib:
        result = imaging.diff(ia, ib)
    assert result["region_count"] == 1 and result["changed_pct"] > 4
    with Image.open(tmp_path / "a.webp") as ia, Image.open(tmp_path / "a.webp") as ib:
        assert imaging.diff(ia, ib)["changed_pct"] == 0  # lossless: no compression noise


def test_nearest_css_variable_names_a_colour() -> None:
    variables = measure.var_names({"--surface-2": "#11161e", "--surface-3": "#151a22"})
    assert measure.name_for(measure.parse_color("rgb(21, 26, 34)"), variables)[0] == "--surface-3"
