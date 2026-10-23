"""State recipes: named, repeatable setup steps that put a page into a view's state.

Declared in spine-design.toml, for example:

    [states.palette-open]
    steps = [ { press = "Control+k" }, { wait_for = ".palette-layer" } ]

Steps run inside the page (both adapters evaluate the same JS), so they can only
do what page script can: no navigation away, no file access. Only the actions
below are accepted, and each is validated before anything runs.
"""

from __future__ import annotations

import json
from typing import Any

MAX_STEPS = 20
MAX_PAUSE_MS = 2000

# action -> number of arguments (a str for 1, a list for more)
ACTIONS = {"click": 1, "fill": 2, "press": 1, "wait_for": 1, "set_attribute": 3, "pause": 1}


class RecipeError(ValueError):
    """A state recipe in spine-design.toml is malformed."""


def validate(name: str, steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not steps:
        raise RecipeError(f"state {name!r}: steps must be a non-empty list")
    if len(steps) > MAX_STEPS:
        raise RecipeError(f"state {name!r}: at most {MAX_STEPS} steps")
    clean = []
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) or len(step) != 1:
            raise RecipeError(f"state {name!r} step {i}: one action per step, e.g. {{ click = \".button\" }}")
        (action, value), = step.items()
        if action not in ACTIONS:
            raise RecipeError(f"state {name!r} step {i}: unknown action {action!r}; use one of {sorted(ACTIONS)}")
        args = value if isinstance(value, list) else [value]
        if len(args) != ACTIONS[action] or not all(isinstance(a, (str, int)) for a in args):
            raise RecipeError(f"state {name!r} step {i}: {action} takes {ACTIONS[action]} argument(s)")
        if action == "pause" and not (0 <= int(args[0]) <= MAX_PAUSE_MS):
            raise RecipeError(f"state {name!r} step {i}: pause is 0-{MAX_PAUSE_MS} ms")
        clean.append({"action": action, "args": [str(a) if action != "pause" else int(a) for a in args]})
    return clean


def load(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    states = data.get("states") or {}
    if not isinstance(states, dict):
        raise RecipeError("[states] must be a table of named states")
    return {name: validate(name, (spec or {}).get("steps") if isinstance(spec, dict) else None)
            for name, spec in states.items()}


# Runs in the page. Returns {done, error?}; never throws, so the adapter can report it.
RECIPE_FN = r"""async (steps) => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const find = (sel) => document.querySelector(sel);
  const key = (combo) => {
    const parts = combo.split('+');
    const k = parts.pop();
    const mods = new Set(parts.map((p) => p.toLowerCase()));
    // Apps often match on event.code (layout-independent), so set it as well as key.
    const code = /^[a-z]$/i.test(k) ? `Key${k.toUpperCase()}` : /^[0-9]$/.test(k) ? `Digit${k}` : k;
    return { key: k.length === 1 ? k.toLowerCase() : k, code, bubbles: true, cancelable: true,
      ctrlKey: mods.has('control') || mods.has('ctrl'), shiftKey: mods.has('shift'),
      altKey: mods.has('alt'), metaKey: mods.has('meta') || mods.has('cmd') };
  };
  let done = 0;
  for (const { action, args } of steps) {
    try {
      if (action === 'click') {
        const el = find(args[0]); if (!el) return { done, error: `click: no element ${args[0]}` };
        el.click();
      } else if (action === 'fill') {
        const el = find(args[0]); if (!el) return { done, error: `fill: no element ${args[0]}` };
        el.focus(); el.value = args[1];
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      } else if (action === 'press') {
        const target = document.activeElement || document.body;
        target.dispatchEvent(new KeyboardEvent('keydown', key(args[0])));
        target.dispatchEvent(new KeyboardEvent('keyup', key(args[0])));
      } else if (action === 'wait_for') {
        let ok = false;
        for (let i = 0; i < 30 && !ok; i++) { ok = Boolean(find(args[0])); if (!ok) await sleep(100); }
        if (!ok) return { done, error: `wait_for: ${args[0]} never appeared` };
      } else if (action === 'set_attribute') {
        const el = find(args[0]); if (!el) return { done, error: `set_attribute: no element ${args[0]}` };
        el.setAttribute(args[1], args[2]);
      } else if (action === 'pause') {
        await sleep(args[0]);
      }
      done++;
    } catch (e) { return { done, error: `${action}: ${String(e).slice(0, 120)}` }; }
  }
  return { done };
}"""


def run_expression(steps: list[dict[str, Any]]) -> str:
    """A `return ...` body that runs the steps, for string-form evaluate."""
    return f"return ({RECIPE_FN})({json.dumps(steps)});"
