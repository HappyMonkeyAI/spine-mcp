"""Spine Design D3: approved baselines, state recipes, adapter selection, Playwright adapter."""

import copy
import json
import shutil
from pathlib import Path

import pytest

from conftest import write
from spine_design import browseros, recipes, store, tools
from spine_mcp.writes import SpineWriteError
from test_design import EXTRACTION, _png, _record, ui  # noqa: F401  (ui is a fixture)
from test_design_d2 import _FakeCapture


def test_approve_baseline_writes_tracked_files_and_inventory(ui: Path, tmp_path: Path) -> None:
    _record(ui, tmp_path, image=_png(tmp_path / "a.png"))
    result = tools.approve_baseline(str(ui), "claude-code", note="pilot baseline")
    slug = "root-400x300-dark-default"
    assert result["approved"] == slug and not result["replaced"]
    assert set(result["written"]) == {f".agent/design/baselines/{slug}.json", f".agent/design/baselines/{slug}.webp",
                                      ".agent/design/inventory.json"}
    record = store.baseline(ui, slug)
    assert record["baseline"]["approved_by"] == "claude-code" and record["baseline"]["note"] == "pilot baseline"
    inventory = json.loads((ui / ".agent/design/inventory.json").read_text(encoding="utf-8"))
    assert inventory["views"][slug]["colors"]["background"][0] == "#0a0c10"
    assert "button.rail-button" in inventory["views"][slug]["components"]
    # Baselines are tracked (not under the self-ignoring cache).
    from conftest import git
    status = git(ui, "status", "--porcelain", "-uall")
    assert f".agent/design/baselines/{slug}.json" in status and ".agent/design/inventory.json" in status
    assert "baseline" in tools.design_brief(str(ui))["brief"]


def test_diff_defaults_to_baseline_and_baseline_survives_cache_loss(ui: Path, tmp_path: Path) -> None:
    _record(ui, tmp_path, image=_png(tmp_path / "a.png"))
    tools.approve_baseline(str(ui), "codex")
    changed = copy.deepcopy(EXTRACTION)
    changed["components"][1]["styles"]["radius"] = "9px"
    _record(ui, tmp_path, extraction=changed, image=_png(tmp_path / "b.png", rect_color="#2459a6"))
    _record(ui, tmp_path, extraction=changed, image=_png(tmp_path / "b.png", rect_color="#2459a6"))
    result = tools.diff_captures(str(ui), view="root-400x300-dark-default")
    assert result["a"]["id"] == store.baseline(ui, "root-400x300-dark-default")["id"]  # not "previous"
    assert result["components"]["changed"] == [{"key": "div.session-list", "changes": {"radius": ["5px", "9px"]}}]
    assert result["pixels"]["region_count"] == 1
    # Another host has no cache, only the tracked baseline: get_capture still works.
    shutil.rmtree(ui / ".agent/design/cache")
    info, image = tools.get_capture(str(ui), "root-400x300-dark-default", which="baseline", include_image=True)
    assert info["image"]["file"] == "root-400x300-dark-default.webp" and image


def test_baseline_caps_and_guards(ui: Path, tmp_path: Path) -> None:
    write(ui, ".agent/design/spine-design.toml", "baseline_max_bytes = 10\n")
    _record(ui, tmp_path, image=_png(tmp_path / "a.png"))
    with pytest.raises(SpineWriteError, match="baseline_max_bytes"):
        tools.approve_baseline(str(ui), "codex")
    write(ui, ".agent/design/spine-design.toml", "baseline_max = 1\n")
    tools.approve_baseline(str(ui), "codex")
    tools.approve_baseline(str(ui), "codex")  # replacing the same view is fine
    _record(ui, tmp_path, theme="light")
    with pytest.raises(SpineWriteError, match="baseline_max"):
        tools.approve_baseline(str(ui), "codex", view="root-400x300-light-default")
    with pytest.raises(SpineWriteError, match="already the baseline"):
        tools.approve_baseline(str(ui), "codex", view="root-400x300-dark-default", which="baseline")
    with pytest.raises(SpineWriteError, match="no approved baseline"):
        tools.get_capture(str(ui), "root-400x300-light-default", which="baseline")


def test_recipes_validate_strictly() -> None:
    ok = recipes.validate("palette", [{"press": "Control+Shift+R"}, {"wait_for": ".palette-layer"},
                                      {"fill": [".palette-query", "git"]}, {"pause": 200}])
    assert ok[0] == {"action": "press", "args": ["Control+Shift+R"]} and ok[3] == {"action": "pause", "args": [200]}
    for bad, message in [([], "non-empty"), ([{"navigate": "x"}], "unknown action"),
                         ([{"click": ".a", "fill": [".b", "c"]}], "one action"),
                         ([{"fill": ".only-one"}], "2 argument"), ([{"pause": 99999}], "pause is"),
                         ([{"click": ".a"}] * 21, "at most")]:
        with pytest.raises(recipes.RecipeError, match=message):
            recipes.validate("s", bad)
    assert "KeyR" not in recipes.RECIPE_FN and "Key${k.toUpperCase()}" in recipes.RECIPE_FN


def test_ensure_capture_passes_state_steps_and_picks_adapter(ui: Path, tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    write(ui, ".agent/design/spine-design.toml",
          'base_url = "http://127.0.0.1:5197"\n\n[states.palette]\nsteps = [ { press = "Control+k" } ]\n')
    fake = _FakeCapture(EXTRACTION)
    result = tools.ensure_capture(str(ui), "codex", theme="dark", state="palette", capture_fn=fake)
    assert result["view"]["state"] == "palette"
    assert fake.calls[0][1]["steps"] == [{"action": "press", "args": ["Control+k"]}]
    with pytest.raises(SpineWriteError, match="unknown state 'nope'.*known: palette"):
        tools.ensure_capture(str(ui), "codex", state="nope", capture_fn=fake)
    monkeypatch.setenv("SPINE_DESIGN_ADAPTER", "carrier-pigeon")
    with pytest.raises(SpineWriteError, match="unknown adapter"):
        tools.ensure_capture(str(ui), "codex", theme="light")


def test_browseros_run_code_carries_recipe() -> None:
    steps = recipes.validate("s", [{"click": ".open"}])
    code = browseros.build_run_code("http://127.0.0.1:1/", None, None, None, None, "body", False, [], steps)
    assert '"steps": [{"action": "click", "args": [".open"]}]' in code and "state recipe step" in code


def _chrome_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return any(Path(p).exists() for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe", "/usr/bin/google-chrome", "/usr/bin/chromium",
        "/Applications/Google Chrome.app"))


@pytest.mark.skipif(not _chrome_available(), reason="needs playwright and an installed Chrome")
def test_playwright_adapter_live_masks_and_runs_recipes() -> None:
    from spine_design import playwright_adapter
    page = ("<html data-theme='dark'><head><style>:root{--accent:#2459a6} body{background:#0a0c10;margin:0}"
            ".panel{display:none;background:var(--accent);border-radius:6px;padding:12px}"
            ".open .panel{display:block}</style></head><body>"
            "<button class='toggle' onclick=\"document.body.classList.add('open')\">open</button>"
            "<input class='secret' value='hunter2'><div class='panel'>panel</div><div class='panel'>panel</div></body></html>")
    url = "data:text/html," + page.replace("#", "%23")
    steps = recipes.validate("open", [{"click": ".toggle"}, {"wait_for": ".open .panel"}])
    result = playwright_adapter.capture(url, viewport=(320, 200), theme="dark", wait_for="body",
                                        steps=steps, channel="chrome")
    x = result.extraction
    assert result.png_base64 and x["masked"] == 1 and x["root_vars"]["--accent"] == "#2459a6"
    panel = next(c for c in x["components"] if c["key"] == "div.panel")
    assert panel["count"] == 2 and panel["styles"]["radius"] == "6px"
    assert "hunter2" not in json.dumps(x)  # input values never measured
