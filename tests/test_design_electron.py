"""Spine Design: the Electron adapter (packaged-app capture in a throwaway profile)."""

import copy
import glob
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from conftest import write
from spine_design import browseros, playwright_adapter as pa, store, tools
from test_design import EXTRACTION, ui  # noqa: F401  (ui is a fixture)
from test_design_d2 import _FakeCapture

ELECTRON = Path(r"C:\Users\Stephen.phillips\Documents\development\ZeroGTerm\node_modules\electron\dist\electron.exe")


def test_electron_executable_found_in_node_modules(tmp_path: Path) -> None:
    assert pa.electron_executable(tmp_path) is None
    exe = tmp_path / "node_modules" / "electron" / "dist" / ("electron.exe" if os.name == "nt" else "electron")
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert pa.electron_executable(tmp_path) == exe


def test_devtools_endpoint_is_read_from_stderr_with_a_deadline() -> None:
    script = ("import sys, time; print('noise', file=sys.stderr, flush=True); "
              "print('DevTools listening on ws://127.0.0.1:51234/devtools/browser/abc', file=sys.stderr, flush=True); "
              "time.sleep(5)")
    proc = subprocess.Popen([sys.executable, "-c", script], stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    try:
        assert pa._devtools_url(proc, timeout=10) == "ws://127.0.0.1:51234/devtools/browser/abc"
    finally:
        pa._kill_tree(proc)
    silent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL)
    try:
        with pytest.raises(browseros.AdapterError, match="DevTools endpoint"):
            pa._devtools_url(silent, timeout=1)  # returns at the deadline, not when the app exits
    finally:
        pa._kill_tree(silent)


def test_ensure_capture_with_electron_needs_no_url(ui: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(ui, ".agent/design/spine-design.toml", "[states.terminal]\nsteps = [ { press = \"Control+Shift+T\" } ]\n")
    monkeypatch.setenv("SPINE_DESIGN_ADAPTER", "electron")
    fake = _FakeCapture(EXTRACTION)
    result = tools.ensure_capture(str(ui), "claude-code", theme="dark", state="terminal", capture_fn=fake)
    assert result["action"] == "captured" and fake.calls[0][0] == f"electron:{ui.name}"
    assert store.captures(ui)[0]["adapter"] == "electron"
    write(ui, ".agent/design/spine-design.toml", 'electron_app = "../outside"\n')
    from spine_core.resolve import SpinePathError
    with pytest.raises(SpinePathError):
        tools.ensure_capture(str(ui), "claude-code", theme="light", capture_fn=fake)


@pytest.mark.skipif(not ELECTRON.is_file(), reason="needs an installed Electron binary")
def test_electron_capture_live_isolated_and_cleaned_up(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "package.json").write_text('{"name": "spine-design-electron-probe", "main": "main.js"}', encoding="utf-8")
    (app / "main.js").write_text(
        "const { app, BrowserWindow } = require('electron');\n"
        "app.whenReady().then(() => { const w = new BrowserWindow({ width: 400, height: 300 });"
        " w.loadFile('index.html'); });\n", encoding="utf-8")
    (app / "index.html").write_text(
        "<html data-theme='dark'><body style='background:#0a0c10;color:#d8dee9'>"
        "<input class='secret' value='hunter2'><div class='card' style='border-radius:6px'>card</div>"
        "<div class='card' style='border-radius:6px'>card</div></body></html>", encoding="utf-8")
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "spine-design-electron-*")))
    result = pa.capture_electron(app, executable=ELECTRON, viewport=(400, 300), theme="dark",
                                 color_scheme="dark", wait_for=".card")
    x = result.extraction
    assert result.png_base64 and x["masked"] == 1 and x["url"] == "file:"
    assert next(c for c in x["components"] if c["key"] == "div.card")["count"] == 2
    assert "hunter2" not in str(x)
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "spine-design-electron-*")))
    assert after <= before  # the throwaway profile was deleted
    assert not result.skipped_pixels
