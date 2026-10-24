"""Headless capture with Playwright: for CI, Linux hosts, and machines without BrowserOS.

Deterministic where BrowserOS is not: a headless page renders on demand, so
screenshots are never starved of frames. Optional dependency:

    uv sync --extra playwright
    # then either use an installed browser (browser_channel = "chrome" | "msedge")
    # or download one: uv run playwright install chromium

Also launches a packaged Electron app (capture_electron), for views that need its
main-process APIs (live sessions, terminals), in an isolated throwaway profile.

Same extraction script, masks and state recipes as the BrowserOS adapter.
"""

from __future__ import annotations

import base64
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .browseros import AdapterError, Capture, _script_expression
from .recipes import RECIPE_FN


def _playwright() -> tuple[Any, type]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise AdapterError("Playwright is not installed: `uv sync --extra playwright` "
                           "(then browser_channel = \"chrome\" or `playwright install chromium`)") from exc
    return sync_playwright, PlaywrightError


def _measure(page: Any, error: type, mark: Callable[[str], None], theme: str | None, theme_attr: str | None,
             wait_for: str, pixels: bool, mask: list[str] | None,
             steps: list[dict[str, Any]] | None) -> tuple[dict[str, Any], str | None, bool]:
    """The shared page flow: wait, theme, state recipe, masks, extract, screenshot.

    Pixels are best-effort, as in the BrowserOS adapter: a window that is hidden
    (an Electron app started with show: false) produces no frames, so after 10 s
    the capture keeps its measurements and skips the screenshot.
    """
    ready = True
    try:
        page.wait_for_selector(wait_for, timeout=8000)
    except error:
        ready = False
    mark("ready")
    if theme and theme_attr:
        page.evaluate("([a, t]) => document.documentElement.setAttribute(a, t)", [theme_attr, theme])
    if steps:
        state = page.evaluate(RECIPE_FN, steps)
        if state and state.get("error"):
            raise AdapterError(f"state recipe step {state['done'] + 1}: {state['error']}")
        mark("state")
    page.evaluate("(m) => { window.SPINE_DESIGN_OPTIONS = { mask: m }; }", list(mask or []))
    extraction = page.evaluate(_script_expression())
    mark("extract")
    png = None
    if pixels:
        try:
            png = base64.b64encode(page.screenshot(type="png", timeout=10000)).decode()
            mark("screenshot")
        except error:
            mark("screenshot-skipped")
    page.evaluate("() => window.__spineDesignUnmask && window.__spineDesignUnmask()")
    return extraction, png, ready


def _timer() -> tuple[dict[str, int], Callable[[str], None]]:
    t0 = time.time()
    laps: dict[str, int] = {}

    def mark(name: str) -> None:
        laps[name] = round((time.time() - t0) * 1000)

    return laps, mark


def capture(url: str, viewport: tuple[int, int] | None = None, theme: str | None = None,
            theme_attr: str | None = "data-theme", color_scheme: str | None = None,
            wait_for: str = "body", pixels: bool = True, mask: list[str] | None = None,
            steps: list[dict[str, Any]] | None = None, channel: str | None = None) -> Capture:
    sync_playwright, error = _playwright()
    laps, mark = _timer()
    channel = channel or os.environ.get("SPINE_PLAYWRIGHT_CHANNEL") or None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=channel, headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": viewport[0], "height": viewport[1]} if viewport else None,
                    device_scale_factor=1, color_scheme=color_scheme or "no-preference")
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                mark("open")
                extraction, png, ready = _measure(page, error, mark, theme, theme_attr, wait_for, pixels, mask, steps)
            finally:
                browser.close()
    except AdapterError:
        raise
    except error as exc:
        raise AdapterError(f"Playwright capture of {url} failed: {str(exc)[:300]}") from exc
    return Capture(extraction=extraction, png_base64=png, ready=ready, laps=laps,
                   skipped_pixels=bool(pixels and not png))


def electron_executable(app_dir: Path) -> Path | None:
    """The app's own Electron binary from node_modules (no download, matches the app's version)."""
    dist = app_dir / "node_modules" / "electron" / "dist"
    for candidate in (dist / "electron.exe", dist / "electron", dist / "Electron.app" / "Contents" / "MacOS" / "Electron"):
        if candidate.is_file():
            return candidate
    return None


def _remove(path: str) -> None:
    # Windows keeps a just-closed app's profile files locked for a moment.
    for _ in range(10):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.3)


def _kill_tree(proc: "subprocess.Popen[bytes]") -> None:
    """End the app and everything it started (pty shells included)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, stdin=subprocess.DEVNULL)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _devtools_url(proc: "subprocess.Popen[bytes]", timeout: float = 30.0) -> str:
    """Electron prints `DevTools listening on ws://127.0.0.1:PORT/...` once it is up.

    stderr is drained on a background thread for the app's whole life: a blocking
    readline can't enforce the deadline, and an undrained pipe can stall a chatty app.
    """
    lines: "queue.Queue[str]" = queue.Queue()

    def drain() -> None:
        for raw in iter(proc.stderr.readline, b""):
            lines.put(raw.decode("utf-8", "replace"))

    threading.Thread(target=drain, daemon=True).start()
    deadline = time.time() + timeout
    seen: list[str] = []
    while time.time() < deadline and proc.poll() is None:
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            continue
        seen.append(line.strip())
        match = re.search(r"DevTools listening on (ws://127\.0\.0\.1:\d+/\S+)", line)
        if match:
            return match.group(1)
    raise AdapterError("Electron did not open its DevTools endpoint: " + " | ".join(seen[-3:])[:300])


def capture_electron(app_dir: Path, executable: Path | None = None, viewport: tuple[int, int] | None = None,
                     theme: str | None = None, theme_attr: str | None = "data-theme",
                     color_scheme: str | None = None, wait_for: str = "body", pixels: bool = True,
                     mask: list[str] | None = None, steps: list[dict[str, Any]] | None = None) -> Capture:
    """Launch the packaged Electron app and measure its first window.

    Python Playwright has no Electron driver, so the app's own Electron binary is
    started with a local-only DevTools port and Playwright attaches over CDP.

    Always in a throwaway profile (`--user-data-dir`), so the app never sees the
    user's saved workspaces, sessions, secrets or port forwards. On Windows,
    redirecting APPDATA does NOT do this: Electron asks the OS for the real folder.
    The adapter refuses to measure unless Electron demonstrably wrote into the
    throwaway profile, then kills the whole process tree and deletes the profile.
    """
    sync_playwright, error = _playwright()
    exe = executable or electron_executable(app_dir)
    if not exe or not Path(exe).is_file():
        raise AdapterError(f"no Electron binary for {app_dir}: install the app's dependencies, "
                           "or set electron_executable in spine-design.toml")
    laps, mark = _timer()
    profile = tempfile.mkdtemp(prefix="spine-design-electron-")
    proc = subprocess.Popen(
        [str(exe), str(app_dir), f"--user-data-dir={profile}", "--remote-debugging-port=0",
         "--remote-debugging-address=127.0.0.1"],
        cwd=str(app_dir), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        endpoint = _devtools_url(proc)
        # Isolation guard: Chromium writes its state into the user-data dir at startup.
        for _ in range(50):
            if any(Path(profile).iterdir()):
                break
            time.sleep(0.1)
        else:
            raise AdapterError("refusing to measure: Electron wrote nothing to the throwaway profile")
        mark("isolated")
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(endpoint, timeout=20000)
            try:
                page = None
                for _ in range(100):  # the first BrowserWindow appears shortly after startup
                    pages = [p for c in browser.contexts for p in c.pages if not p.url.startswith("devtools://")]
                    if pages:
                        page = pages[0]
                        break
                    time.sleep(0.2)
                if page is None:
                    raise AdapterError("the Electron app opened no window")
                page.wait_for_load_state("domcontentloaded")
                if viewport:
                    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
                if color_scheme in {"dark", "light"}:
                    page.emulate_media(color_scheme=color_scheme)
                mark("open")
                extraction, png, ready = _measure(page, error, mark, theme, theme_attr, wait_for, pixels, mask, steps)
            finally:
                browser.close()
    except AdapterError:
        raise
    except error as exc:
        raise AdapterError(f"Electron capture of {app_dir} failed: {str(exc)[:300]}") from exc
    finally:
        _kill_tree(proc)
        _remove(profile)
    return Capture(extraction=extraction, png_base64=png, ready=ready, laps=laps,
                   skipped_pixels=bool(pixels and not png))
