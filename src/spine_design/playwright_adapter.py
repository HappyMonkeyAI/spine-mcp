"""Headless capture with Playwright: for CI, Linux hosts, and machines without BrowserOS.

Deterministic where BrowserOS is not: a headless page renders on demand, so
screenshots are never starved of frames. Optional dependency:

    uv sync --extra playwright
    # then either use an installed browser (browser_channel = "chrome" | "msedge")
    # or download one: uv run playwright install chromium

Same extraction script, masks and state recipes as the BrowserOS adapter.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

from .browseros import AdapterError, Capture, _script_expression
from .recipes import RECIPE_FN


def capture(url: str, viewport: tuple[int, int] | None = None, theme: str | None = None,
            theme_attr: str | None = "data-theme", color_scheme: str | None = None,
            wait_for: str = "body", pixels: bool = True, mask: list[str] | None = None,
            steps: list[dict[str, Any]] | None = None, channel: str | None = None) -> Capture:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise AdapterError("Playwright is not installed: `uv sync --extra playwright` "
                           "(then browser_channel = \"chrome\" or `playwright install chromium`)") from exc

    t0 = time.time()
    laps: dict[str, int] = {}

    def mark(name: str) -> None:
        laps[name] = round((time.time() - t0) * 1000)

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
                ready = True
                try:
                    page.wait_for_selector(wait_for, timeout=8000)
                except PlaywrightError:
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
                png = base64.b64encode(page.screenshot(type="png")).decode() if pixels else None
                if pixels:
                    mark("screenshot")
                page.evaluate("() => window.__spineDesignUnmask && window.__spineDesignUnmask()")
            finally:
                browser.close()
    except AdapterError:
        raise
    except PlaywrightError as exc:
        raise AdapterError(f"Playwright capture of {url} failed: {str(exc)[:300]}") from exc
    return Capture(extraction=extraction, png_base64=png, ready=ready, laps=laps, skipped_pixels=False)
