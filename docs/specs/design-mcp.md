# Spine Design MCP — v1 Spec

Status: **Draft for review** · 2026-10-23
Home: this repo, as a second server (`spine-design-mcp`) and package (`spine_design/`) on the
shared `spine_core/` (project resolution, commit anchors, FTS index, scrub). See SPEC.md §9.
Pilot: **ZeroGTerm** (React + Vite renderer in Electron; `DESIGN.md` carries machine-readable
tokens and component specs). Second target: MRPGRealms' DOM HUD and panels, with the canvas masked.

## 1. Problem

For any UI work, agents open a browser, take screenshots, read them, and throw them away.
The next session, or another agent on another host, starts blind again:

- **Pixels are expensive and forgotten.** A 1024×768 screenshot costs about 1k tokens to look at
  (roughly width×height/750), and an agent typically takes several per task. Nothing records what it saw,
  so "did this change anything?" means taking a new screenshot and comparing it from memory.
- **The design spec and the app drift apart silently.** On ZeroGTerm (2026-10-23):
  - `DESIGN.md` was last changed 2026-08-31, but the renderer's CSS kept changing up to 2026-10-15.
  - A *static* source count was misleading. It suggested `DESIGN.md`'s palette was barely used,
    because each color appears once, as a CSS variable.
  - *Measured live* (D1), the dark theme does follow the palette: `--surface-0` #0a0c10,
    `--line-1` #202630, text #d8dee9 / #697386.
  - The real drift only shows at runtime. Font sizes are 13px on 115 elements, plus 11/10/9px,
    against a spec of 14px body / 20px display. Radius is 5px on 11 elements, off the 4/6/10px
    scale. One border uses `#3b82f6`, which isn't in any variable.
  - There's also a `:root[data-theme='light']` theme that `DESIGN.md` never mentions.
  - Nothing checks any of this. This is the case for measuring the running UI rather than
    reading source.
- **Design knowledge lives in prose.** An agent reads all of `DESIGN.md` to learn one colour,
  and learns nothing about the components the app really has.

## 2. What it is

A **visual memory and inventory for a project's running UI**: captures with history,
design tokens and components measured from the live DOM, comparison against `DESIGN.md`, and
diffs. It answers "what does this look like now, what changed, does it match the spec?" in
text by default and in pixels on request.

### Principles (Spine's, plus four for pixels)

1. **Files are truth; caches are disposable.** As in Spine. Large binaries stay out of git
   (§4).
2. **Every capture is anchored** to (route, viewport, theme, state, commit SHA, UI-source hash).
   Staleness is computed, not guessed.
3. **Text first, pixels on request.** Tools return measured facts, diffs and file paths.
   An image goes into an agent's context only when asked for, cropped and downscaled.
4. **Don't build a browser.** Spine Design orchestrates capture adapters (§5) and doesn't
   replace BrowserOS neo or Playwright.
5. **Measure, don't interpret.** Values come from computed styles and the accessibility tree.
   Aesthetic judgement is the calling agent's or the HTML Design Advisor's job, not this server's.
6. **Agent-neutral and multi-host**, exactly as Spine §5a.

### Boundary with what exists

| Concern | Owner | Spine Design's role |
|---|---|---|
| Choosing a design direction, templates, licensing | HTML Design Advisor MCP | none; can hand it a capture or an audit target later (D4) |
| Driving a live browser (logged in, interactive) | BrowserOS neo | adapter: capture + evaluate through its MCP (D3) |
| Headless, deterministic rendering | Playwright | default adapter (D2) |
| Three.js scene state and visual regressions | parallax-threejs plugin (checkpoint/diff) | none; canvas regions are masked as opaque |
| Screen-activity recording (what the user did) | uiqa / recall agent | none; different question (activity, not design) |
| Project docs, tasks, checkpoints | Spine | shares core; Spine's brief gains a short "design" section (D4) |

## 3. Data model

A **view** is what you'd point at: `(route, viewport, theme, state)`, e.g.
`("/", "1440x900", "dark", "two-sessions-open")`. `state` is a named setup recipe (§5.3), or
`default`.

A **capture** is one observation of a view:

```json
{
  "id": "2026-10-23T1412Z-root-1440x900-dark",
  "view": {"route": "/", "viewport": "1440x900", "theme": "dark", "state": "default"},
  "sha": "bb58570", "ui_hash": "sha256 of files matching ui_paths",
  "agent": "codex", "adapter": "playwright", "taken": "2026-10-23T14:12:00Z",
  "image": "captures/<id>.webp", "masks": ["canvas", "[data-spine-mask]"],
  "dom": "captures/<id>.dom.json", "a11y": "captures/<id>.a11y.txt",
  "tokens": {"colors": {...}, "fonts": {...}, "spacing": {...}, "radii": {...}, "shadows": {...}},
  "components": [{"name": "session-item-active", "selector": "...", "count": 1, "bbox": [...], "styles": {...}}]
}
```

A **baseline** is a capture the owner approved as the reference for its view.

The **inventory** is a project-level summary built from the latest fresh capture of each view:
the tokens in use, the components found, and where they diverge from `DESIGN.md`.

## 4. Storage

```
<project>/.agent/design/
  spine-design.toml        # tracked: base_url, views, states, ui_paths, masks, allowed origins
  baselines/<view>.webp    # tracked, size-capped (default 150 KB each, 40 total); approved references
  baselines/<view>.json    # tracked: the baseline capture's metadata and measurements
  inventory.json           # tracked, small: current tokens/components summary + drift findings
  cache/                   # ignored (self-ignoring like .agent/spine): every capture, image, DOM, a11y
```

Captures are cheap to retake, so they live in the ignored cache. Each host keeps its own,
and they expire (default 30 days, last 20 per view). Baselines and the inventory are what other
hosts and agents need, so they are small, tracked and reviewed like any other change. With
nothing tracked, the server still works locally.

## 5. Capture

### 5.1 Adapters

| Adapter | When | Needs | Slice |
|---|---|---|---|
| **agent-supplied** | Any agent that already has a browser (Claude Code, Codex, Hermes) | Agent runs Spine Design's extraction script in its browser's `evaluate`, saves a screenshot, and calls `record_capture` with both | D1 |
| **playwright** | Default when the server should capture itself: deterministic and headless, works on Linux hosts and in CI. Electron apps through `_electron.launch` or the Vite dev URL | `playwright` (optional extra) | D2 |
| **browseros** | Logged-in or interactive state the user set up in BrowserOS neo | BrowserOS neo's HTTP MCP endpoint (`127.0.0.1:9010/mcp`) | D3 |

The **agent-supplied** path comes first because it needs no browser inside the server and works
for every agent today. It is also the honest way to capture what the agent saw.

### 5.2 Extraction script

One versioned, dependency-free JS function (`extract.js`, exposed as an MCP resource so any
agent can run it). It returns JSON, never page copy:

- computed styles of visible elements, sampled and deduplicated: color, background, border,
  font family/size/weight/line-height, padding/margin/gap, radius, shadow
- `:root` custom properties (`--color-*`, ...): the app's own token names
- component candidates: landmarks and roles, `data-testid` / `data-component`, custom elements,
  and repeated class signatures. Each gets its bbox and styles.
- redaction applied **in the page before capture** (§8): form values blanked, masked selectors
  covered

Text content is **not** returned, except accessible names truncated to 40 characters (needed to tell
components apart). Page text is untrusted data and a prompt-injection surface.

### 5.3 States

`spine-design.toml` names setup recipes. For example, `two-sessions-open` is a short list of
steps (`click`, `fill`, `press`, `wait_for`) or a project script path. These run through the adapter.
Recipes are what make captures of the same view comparable.

### 5.4 Freshness: the decision the server makes

`ensure_capture(view)` returns a fresh capture, taking one only when needed:

| Condition | Result |
|---|---|
| Latest capture's `ui_hash` equals the current hash of `ui_paths` and age < TTL (default 7 days) | **fresh**: reuse, no browser |
| UI files changed (hash differs), or a newer commit touched `ui_paths` | **stale**: recapture if an adapter is available; otherwise return the old capture marked stale, with the reason |
| No capture yet | capture, or tell the agent how to supply one |

`ui_paths` defaults to common UI globs (`src/**/*.{css,scss,tsx,jsx,vue,svelte,html}`,
`index.html`, `tailwind.config.*`) and is configurable. Using a hash of the files, rather than the
commit SHA, means uncommitted edits count, and commits that touch only the backend don't trigger
pointless recaptures.

## 6. Tools (v1)

Every tool takes `project` (required, as in Spine). Results are text and JSON with file paths.
`include_image=true` adds a downscaled, cropped image.

| Tool | Slice | Purpose |
|---|---|---|
| `design_brief(project, budget_tokens=1200)` | D1 | Views and their freshness, the token palette in use, the component inventory, top drift findings, baselines pending review. The design counterpart of `session_brief`. |
| `record_capture(project, agent, view, image_path, extraction_json, sha?)` | D1 | Agent-supplied capture: validates, scrubs, stores, measures. |
| `get_capture(project, view, which=latest\|baseline\|<id>, include_image=false, crop?)` | D1 | Measurements and paths. Pixels only if asked. |
| `diff_captures(project, view, a="baseline", b="latest", threshold=0.1)` | D1 | Pixel diff (masks honoured): % changed, changed regions as bboxes plus the components they overlap, and token/style changes per component. Crops on request. |
| `tokens(project, view?)` | D1 | The design tokens the app actually uses, with counts, and the `:root` variable names where the app defines them. |
| `components(project, name?)` | D1 | Component inventory: where each lives, its measured styles, and which views show it. |
| `check_design(project)` | D2 | Compare against `DESIGN.md`: off-palette colors, unregistered font sizes and spacing, components whose measured styles differ from their `DESIGN.md` spec (resolving `{colors.border}`-style references). |
| `ensure_capture(project, view, state?)` | D2 | The freshness decision in §5.4, capturing through the configured adapter. |
| `approve_baseline(project, agent, view, capture_id)` | D3 | Promote a capture to the tracked baseline (owner or agent says why). |
| `forget(project, view? , before?)` | D1 | Delete captures: retention and privacy. |

There are 10 tools, and agents that only read need 5 of them.

## 7. Comparing against DESIGN.md

Supported spec formats, in priority order:

1. **YAML frontmatter tokens** (ZeroGTerm style): `colors`, `typography`, `rounded`, `spacing`,
   `components` with `{group.name}` references. This is the precise path.
2. **CSS custom properties** declared in the app (`:root { --color-primary: ... }`) as the
   app's own source of truth, when there's no `DESIGN.md`.
3. **Prose** (LongSilence style: "amber for objectives, red for threats"): not parsed. The
   design brief only points at it. The agent compares.

The checks:
- **Off-palette:** a measured color not within ΔE < 3 of any spec color, with usage counts and example components.
- **Off-scale:** font sizes, spacing and radii not on the spec's scale (±1px).
- **Component drift:** measured styles of a `DESIGN.md` component that differ from its resolved spec
  (`session-item-active.rounded` is 6px, spec says `{rounded.sm}` = 4px).
- **Unspecified:** components found in the app with no spec entry, and spec components never
  found (renamed or removed?).

How a component is matched to a spec name: `data-component="session-item"`, then a class or
test-id containing the name, then a mapping in `spine-design.toml`. If none applies, it stays
unmatched and is reported, never guessed. ZeroGTerm has no `data-component` or `data-testid`
hooks, and its classes (`session-list`, `session-drawer`, ...) don't match the spec's names
(`session-sidebar`, `session-item`). So the pilot needs a small mapping in `spine-design.toml`.
That is expected, and it is the honest outcome.

**Themes** are part of the view. Where an app themes through `:root[data-theme=...]` or
`prefers-color-scheme`, the adapter sets the theme before capture, and `check_design` compares
each theme against the spec's palette for it. If the spec has no palette for a theme, every color
in that theme is reported as unspecified. That is how ZeroGTerm's undocumented light theme surfaces.

## 8. Privacy and security

- **Screenshots capture whatever is on screen.** Logged-in apps, terminal contents (ZeroGTerm!) and
  patient data (Fusion) are all real risks.
  - Before capture, the extraction script blanks form values and covers `[data-spine-mask]`, `canvas`
    (configurable) and configured selectors.
  - ZeroGTerm's config masks terminal panes by default.
  - `pixels = false` in `spine-design.toml` keeps measurements only, with no images at all.
- **Retention:** cache TTL (30 days) and last-N per view. `forget` deletes. Baselines are
  committed on purpose and reviewed like code.
- **Origins:** adapters only navigate to `base_url` and `allowed_origins` from the config
  (default `127.0.0.1`/`localhost`). No arbitrary URLs, so the server isn't a fetch proxy.
- **Untrusted page:** extracted values are data, and no page text reaches the agent beyond
  truncated accessible names. The extraction script runs in the page, so the server never
  evaluates page-supplied code itself.
- **Writes:** only under `.agent/design/`. Paths go through `safe_path`, and git children get
  `stdin=DEVNULL` (the Spine lesson). `SPINE_READONLY` is honoured.

## 9. Slices and acceptance

### Learned building D1 (2026-10-23), and what changed

- **Screenshots are optional.** BrowserOS neo's SDK `page.screenshot` returns only
  `{bytes, format, page}` (no data or path) and takes about 16 s. An agent working through BrowserOS
  can't hand over an image file. Measurement-only captures carry most of the value (tokens,
  components, style diffs) and work with any browser, so `record_capture` takes the image as
  optional.
- **BrowserOS pixels come through the server.** Raw CDP `Page.captureScreenshot` through
  BrowserOS's `run` returns a ~10 KB webp in about 250 ms. The server should call BrowserOS's MCP
  itself, so bytes never pass through an agent's context.
  - Quirk: large `evaluate` results inside `run` come back empty (the ~18–33 KB extraction), so the
    adapter must fetch results in chunks.
  - **The BrowserOS adapter moves to D2**, because it's the user's preferred browser. Playwright
    self-capture moves to D3.
- **Agent-supplied pixels work with file-saving browsers.** The Playwright MCP (`browser_evaluate`
  with `filename`, `browser_take_screenshot` with `filename`) and chrome-devtools both save files.
  - The Playwright MCP echoes the whole function back, so agents should send `extract.js` once and
    keep it on `window`.
  - Its sandbox has no `import`/`fs`, so it can't load the script from disk itself.
  - Pages with a CSP (ZeroGTerm's blocks `eval`) still work, because evaluation goes through CDP.
- **Storage is lossless webp.** UI screenshots are flat color, so lossless came out *smaller* than
  lossy (14 KB vs 16 KB at 1280×800). It also removes compression noise from diffs, which allows
  a 2% default threshold. The original 10% missed a real dark-theme change: #0d1015 → #151a22 is
  only 8/255.
- **No tracked `inventory.json` in D1.** Writing a tracked file on every capture would dirty every
  host's tree and conflict across hosts, the trap Spine avoided. The inventory is computed from
  the local cache; a tracked summary arrives with approved baselines in D3.
- **Live check on ZeroGTerm.** Changing one rule (`.rail-button`: radius 5→4px, background →
  `--surface-3`) produced:
  - pixels: 0.66% in the 2 rail-button regions
  - tokens: `#151a22` 6→13, 4px 21→29, 5px 11→3, the 8 buttons exactly
  - components: `button.rail-button` background and radius, nothing else

**D1 — store and measure agent-supplied captures (no browser in the server). Built 2026-10-23.**
`record_capture`, `get_capture`, `diff_captures`, `tokens`, `components`, `design_brief`, `forget`,
`extract.js` resource, storage and retention.
Acceptance, on ZeroGTerm, captured through BrowserOS neo by an agent:
- The extraction script returns the app's `:root` variables (25 declared in source) and its in-use
  palette for both themes, matching a static count of the CSS within clustering tolerance.
- `diff_captures` on two captures with one CSS change reports the right region and component,
  and nothing else (masks honoured).
- `design_brief` ≤ 1.2k tokens. Answering "what color is the active session item?" costs no image.
- Seeded secrets in an input field never appear in a stored image or JSON (V2).

**D2 — BrowserOS adapter and spec comparison. Built 2026-10-23.** The server captures through
BrowserOS neo's MCP (CDP screenshot, chunked extraction), plus `ensure_capture` freshness and `check_design`.

What D2 learned about driving BrowserOS from a server (all in `spine_design/browseros.py`):
- **Background tabs are throttled unpredictably.** Agent tabs open in the background. Once
  Chrome deprioritises them, every step slows (about 2 s per evaluate), and a CDP screenshot
  waits for a frame that can take 0.1 s to over 20 s. Focus emulation and
  `Page.setWebLifecycleState` don't force a frame. Raising the tab or opening a window would
  disturb the user, so they aren't used.
- **The design response: pixels are best-effort, measurements are not.**
  - The screenshot gets whatever is left of a 25 s budget and is skipped (`skipped_pixels`)
    rather than failing the capture.
  - Emulating a colour scheme on every capture keeps tabs noticeably quicker.
  - After the first slow capture or two, captures take about 0.8 s.
- **Never leak tabs.** A run killed at BrowserOS's 30 s cap never reaches its `finally`.
  - 13 leaked ZeroGTerm tabs accumulated during development, each running the app in the
    background and slowing every later capture.
  - Every step now has its own deadline. After any failure, a cleanup run in the same session
    closes the session's own tabs.
- **Result shape.** `run` returns values inline even at 120 KB (`ok\nreturn: <json>` plus a session
  line). Only `page.evaluate` inside `run` drops large values, hence the 4 KB chunked read-back.
- **Do not wait on `document.fonts.ready`.** In a background tab, that promise never settled.

Live on ZeroGTerm (both themes, `check_design` against its `DESIGN.md`):
- **Dark theme:** 61.9% of colour use is on the palette.
  - Off palette: `--text-2` #8b93a3 ×34 (a third text tier the spec lacks), the
    `--surface-3/4` and `--line-2/3` ramps, and a stray `#3b82f6` border.
  - `primary` #0A0C10 is unused: the main surfaces are `--surface-1` #0d1015.
- **Light theme:** 20.0% on palette, flagged as undocumented.
- **Scales:** 13px ×117 vs 14px body; 10/9px vs the 11px label; 5px radius ×11 vs 4/6/10.
- **Components:** `session-sidebar` has radius 0 / padding 0 vs 6px / 12px. `app-shell`'s background
  matches once the inherited background is compared (a transparent element shows its ancestor's
  colour); its 13px text doesn't match 14px.
- **Freshness:** a second `ensure_capture` with no UI change reused the capture in 0.1 s,
  without a browser.
Acceptance:
- Second `ensure_capture` with no UI change takes no screenshot.
- Editing one `.css` file makes the view stale with that file as the reason.
- `check_design` on ZeroGTerm reports the drift already found statically (§1): the dark palette's
  divergence from `DESIGN.md`, and the light theme as unspecified. Measured component styles are
  cross-checked by hand on 5 components.

**D3 — baselines and headless capture. Built 2026-10-23 (except Electron launch).**
- **`approve_baseline`:** promotes a capture to `.agent/design/baselines/<view>.{json,webp}` and
  rewrites `inventory.json`, all tracked and size-capped (150 KB per image, 40 views).
  - The inventory is written only on approval, so ordinary captures never dirty a tree.
  - `diff_captures` compares against the baseline by default (`a="auto"`).
  - A host with no cache still reads baselines.
- **State recipes** (`[states.<name>] steps = [...]`): click / fill / press / wait_for /
  set_attribute / pause, strictly validated and run in the page by both adapters. ZeroGTerm's
  `history-palette` state (Ctrl+Shift+R) opens the command palette. That surfaced drift only
  visible there: palette radius 7px vs 10px, padding 10px vs 16px.
- **Playwright adapter** (`adapter = "playwright"` or `SPINE_DESIGN_ADAPTER`): optional extra,
  using an installed browser via `browser_channel = "chrome"` (no download).
  - It's deterministic where BrowserOS isn't: about 2 s with pixels, every time.
  - On ZeroGTerm both adapters measured the same 168 elements and the same palettes.

Learned:
- **Synthetic key events need `code` as well as `key`.** ZeroGTerm matches shortcuts on `event.code`
  (`KeyR`), as layout-independent apps do.
- **A capture's URL leaked page content.** `location.origin + pathname` for a `data:` URL is the
  whole document, form values included (caught by the live adapter test). extract.js now reports
  origin + path only for http(s), else just the scheme.
- **Not done: Electron `_electron.launch`.** ZeroGTerm's renderer measures fine as a plain page
  (styles are real; only preload APIs are missing). Launching the packaged app needs a build plus
  node-pty and is left until a view truly needs the preload (sessions, terminals).

**D4 — integration.**
- Spine's brief gets a 3-line design section (views stale, drift count).
- The HTML Design Advisor can be handed a capture for review.
- A DynamicMCPProxy catalogue entry.

**Eval (as for Spine).** An independent agent writes 8 UI questions on ZeroGTerm:
- current look: "primary background?", "active item style?"
- what changed since the baseline
- does X match `DESIGN.md`?

Then compare a cold agent with the MCP against one with a browser only: score, tokens, and the number
of screenshots it took.

## 10. Non-goals (v1)

Aesthetic judgement or redesign suggestions · generating UI code · visual regression *gating*
in CI (diffs are information, not pass/fail) · Three.js scene inspection (parallax-threejs) ·
non-web UIs (native desktop outside Electron) · storing page text.

## 11. Pre-mortem

| Failure | Mitigation |
|---|---|
| Captures aren't comparable (animations, timestamps, cursors, random data) | Named state recipes. Disable animations and transitions in the script. Mask volatile selectors. Diff threshold. Fixed viewport/DPR. |
| Component detection is noise | Prefer explicit `data-component`. Report unmatched rather than guess. The config mapping wins. |
| Token extraction floods with near-duplicates | Cluster colors by ΔE, snap sizes to px, report usage counts, top-N in the brief. |
| A screenshot leaks secrets or patient data | Redact in the page before capture. Terminal/canvas masked by default. `pixels=false` mode. Retention. Never committed except approved baselines. |
| Git bloat from baselines | Size cap per image and total, webp, and baselines only on approval. |
| Agents don't call `record_capture` | `design_brief` says which views are stale. Spine's brief surfaces it (D4). `ensure_capture` self-serves in D2. |
| Electron renderer won't load in a plain browser (preload APIs missing) | Playwright's Electron launch, or a stub preload for capture only. Decided in D2 on ZeroGTerm. |
| Works on one host, missing on another | Baselines + inventory are tracked. Caches are per host by design. |

## 12. Open questions for you

1. **Name and home:** `spine-design` inside the spine-mcp repo (recommended: shared core, one
   install), or a separate repo?
2. **Pilot:** ZeroGTerm (recommended: structured `DESIGN.md`, conventional DOM), or another?
3. **Baselines in git:** yes, with size caps (recommended), or local only?
4. **Terminal contents in ZeroGTerm captures:** masked by default (recommended), or allowed on your machine?
