"""Capture adapter for BrowserOS neo: the design server is an MCP client of BrowserOS.

Pixels never pass through an agent's context. Everything happens in one bounded
BrowserOS `run` call (30 s cap):
- open a tab, set the viewport and colour scheme through CDP
- evaluate extract.js; inside `run`, large page.evaluate values come back empty,
  so the JSON is stashed on window and read back in 4 KB chunks
- CDP Page.captureScreenshot (~250 ms; the SDK's page.screenshot returns no data)
- close the tab

`run` itself returns large values inline, as `ok\\nreturn: <json>`.
"""

from __future__ import annotations

import asyncio
import json

import anyio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://127.0.0.1:9010/mcp"
EXTRACT_JS = Path(__file__).with_name("extract.js")

RUN_SCRIPT = r"""
const A = __ARGS__;
const t0 = Date.now();
const laps = {};
const mark = (k) => { laps[k] = Date.now() - t0; };
const late = () => Date.now() - t0 > A.budgetMs;
// Every step gets its own deadline, so a stall reports where it happened instead of
// silently eating BrowserOS's whole 30 s run budget.
const step = (name, promise, ms) => Promise.race([
  promise,
  sleep(ms).then(() => { throw new Error(`step ${name} timed out after ${ms} ms; laps ${JSON.stringify(laps)}`); }),
]);
const page = await step('open', browser.open(A.url), 6000);
mark('open');
try {
  if (A.viewport) {
    await step('viewport', page.cdp('Emulation.setDeviceMetricsOverride', JSON.stringify(
      { width: A.viewport[0], height: A.viewport[1], deviceScaleFactor: 1, mobile: false })), 4000);
  }
  // Always emulate a colour scheme: besides theming, it makes the background tab render
  // promptly (measured: whole capture 0.5 s with it, 6-8 s without).
  await step('media', page.cdp('Emulation.setEmulatedMedia', JSON.stringify(
    { features: [{ name: 'prefers-color-scheme', value: A.colorScheme || 'no-preference' }] })), 4000);
  const ready = await step('ready', page.waitForSelector(A.waitFor, { timeout: 6000 }), 7000);
  mark('ready');
  if (A.theme && A.themeAttr) {
    await step('theme', page.evaluate((t) => document.documentElement.setAttribute(t[0], t[1]),
      [A.themeAttr, A.theme]), 4000);
    mark('theme');
  }
  if (A.steps.length) {
    const state = (await step('state', page.evaluate(A.recipe), 8000)).value;
    if (state && state.error) throw new Error(`state recipe step ${state.done + 1}: ${state.error}`);
    mark('state');
  }
  await step('options', page.evaluate((m) => { window.SPINE_DESIGN_OPTIONS = { mask: m }; }, A.mask), 4000);
  const length = (await step('extract', page.evaluate(
    "window.__spineDesignResult = JSON.stringify(" + A.script + "); return window.__spineDesignResult.length;"),
    6000)).value;
  mark('extract');
  let extraction = '';
  for (let i = 0; i < length; i += 4000) {
    extraction += (await step('read', page.evaluate((n) => window.__spineDesignResult.slice(n, n + 4000), i), 4000)).value;
  }
  mark('read');
  // Pixels never fail a capture: measurements are the product. A background tab only gets
  // a frame when Chrome decides to render it (0.1 s to 10+ s), so the screenshot gets
  // whatever is left of the run budget and is skipped if it doesn't arrive.
  let png = null;
  let pixelNote = null;
  if (A.pixels) {
    const left = A.budgetMs - (Date.now() - t0);
    if (left > 1500) {
      try {
        const shot = await step('screenshot', page.cdp('Page.captureScreenshot', JSON.stringify({ format: 'png' })), left);
        png = shot && shot.data;
        mark('screenshot');
      } catch (e) { pixelNote = String(e).slice(0, 160); }
    } else { pixelNote = 'no time left for a screenshot'; }
  }
  await step('cleanup', page.evaluate(() => {
    if (window.__spineDesignUnmask) window.__spineDesignUnmask(); delete window.__spineDesignResult; }), 3000);
  return { ready: ready.matched, extraction, png, laps, skippedPixels: Boolean(A.pixels && !png), pixelNote };
} finally {
  await step('close', page.close(), 3000).catch(() => {});
}
"""


CLEANUP_SCRIPT = r"""
const mine = (await browser.pages.list()).filter((p) => p.ownership === 'mine');
for (const p of mine) { try { await browser.page(p.pageId).close(); } catch (e) {} }
return mine.length;
"""


class AdapterError(RuntimeError):
    """BrowserOS was unreachable or the capture failed."""


@dataclass
class Capture:
    extraction: dict[str, Any]
    png_base64: str | None
    ready: bool
    laps: dict[str, int]
    skipped_pixels: bool
    pixel_note: str | None = None


def _first(exc: BaseException, kind: type) -> BaseException | None:
    """The first exception of `kind` inside (possibly nested) exception groups."""
    if isinstance(exc, kind) and not isinstance(exc, BaseExceptionGroup):
        return exc
    for inner in getattr(exc, "exceptions", ()):
        found = _first(inner, kind)
        if found:
            return found
    return None


def _script_expression() -> str:
    """extract.js as one expression (its comment header removed) for evaluate."""
    source = EXTRACT_JS.read_text(encoding="utf-8")
    start = source.index("(() => {")
    return source[start:].rstrip().rstrip(";")


def build_run_code(url: str, viewport: tuple[int, int] | None, theme: str | None, theme_attr: str | None,
                   color_scheme: str | None, wait_for: str, pixels: bool, mask: list[str] | None = None,
                   steps: list[dict[str, Any]] | None = None) -> str:
    from .recipes import run_expression

    args = {
        "url": url, "viewport": list(viewport) if viewport else None, "theme": theme, "themeAttr": theme_attr,
        "mask": list(mask or []), "steps": list(steps or []),
        "recipe": run_expression(list(steps or [])),
        "colorScheme": color_scheme, "waitFor": wait_for, "pixels": pixels, "script": _script_expression(),
        # Leave headroom under BrowserOS's hard 30 s cap: skip the screenshot rather than lose everything.
        "budgetMs": 25000,
    }
    return RUN_SCRIPT.replace("__ARGS__", json.dumps(args))


def parse_run_result(text: str) -> dict[str, Any]:
    """BrowserOS returns `ok\\nreturn: <json>`, or a JSON envelope with `value`."""
    text = text.strip()
    if text.startswith("{"):
        envelope, _ = json.JSONDecoder().raw_decode(text)
        if not envelope.get("ok", True):
            raise AdapterError(str(envelope.get("error") or envelope)[:400])
        return envelope.get("value", envelope)
    marker = "return:"
    if marker not in text:
        raise AdapterError(f"unexpected BrowserOS run result: {text[:300]}")
    value, _ = json.JSONDecoder().raw_decode(text.split(marker, 1)[1].strip())
    return value


async def _capture(endpoint: str, code: str) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("name_session", {
                "name": "spine design capture", "category": "dev-tools",
                "summary": "Measuring a local dev UI for design memory"})
            try:
                result = await session.call_tool("run", {"code": code, "timeout": 30000})
                text = "".join(getattr(c, "text", "") for c in result.content)
                if result.isError:
                    raise AdapterError(f"BrowserOS run failed: {text[:400]}")
                return parse_run_result(text)
            except BaseException:
                # A run killed at BrowserOS's 30 s cap never reaches its own `finally`, so close
                # whatever this session still has open. Leaked tabs keep running the app in the
                # background and slow every later capture.
                with anyio.CancelScope(shield=True):
                    await session.call_tool("run", {"code": CLEANUP_SCRIPT, "timeout": 10000})
                raise


def capture(url: str, viewport: tuple[int, int] | None = None, theme: str | None = None,
            theme_attr: str | None = "data-theme", color_scheme: str | None = None,
            wait_for: str = "body", pixels: bool = True, mask: list[str] | None = None,
            steps: list[dict[str, Any]] | None = None, endpoint: str | None = None) -> Capture:
    """Capture one view through BrowserOS neo. Runs its own event loop, so call it
    from a worker thread when already inside one (the MCP server does)."""
    endpoint = endpoint or os.environ.get("SPINE_BROWSEROS_URL", DEFAULT_URL)
    code = build_run_code(url, viewport, theme, theme_attr, color_scheme, wait_for, pixels, mask, steps)
    value = None
    for attempt in (1, 2):
        try:
            value = asyncio.run(_capture(endpoint, code))
            break
        except BaseException as exc:  # noqa: BLE001 - anyio wraps errors in exception groups
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            inner = _first(exc, AdapterError)
            # One retry for a run that hit the 30 s cap: a dev server's first-load dependency
            # optimisation can reload the page mid-run. Anything else fails at once.
            if inner and ("exceeded" in str(inner) or "timed out" in str(inner)) and attempt == 1:
                continue
            if inner:
                raise inner from None
            raise AdapterError(f"BrowserOS neo not reachable at {endpoint}: {_first(exc, Exception) or exc}") from exc
    try:
        extraction = json.loads(value.get("extraction") or "")
    except ValueError as exc:
        raise AdapterError(f"extraction did not come back as JSON: {exc}") from exc
    return Capture(extraction=extraction, png_base64=value.get("png"), ready=bool(value.get("ready")),
                   laps=value.get("laps") or {}, skipped_pixels=bool(value.get("skippedPixels")),
                   pixel_note=value.get("pixelNote"))
