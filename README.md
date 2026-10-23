# spine-mcp

Warm-start project context for any coding agent. A query and write layer over a project's
[Agents Protocol](https://github.com/HappyMonkeyAI/AgentsProtocol) documentation
spine: one `session_brief` call (~1.9k tokens) replaces reading README, CONTEXT,
TASKS and PROGRESS at session start, and `checkpoint` / `record_memory` leave the
next agent (any vendor, any host) a better starting point.

Files stay the source of truth. Writes are plain markdown in `.agent/memories/`, left
uncommitted for you to commit with the work. The index is a self-ignoring cache in
`<project>/.agent/spine/` and rebuilds itself. Design: [SPEC.md](SPEC.md).

## Status

**Spine: slices S1 (read) and S2 (write) built. Spine Design: slices D1–D3 built.** S3 (proxy catalogue, registry resolution,
protocol ADR) is next.

Measured on MRPGRealms (2026-10-23): brief ~2.4k tokens vs ~10k for protocol
grounding (and a 55k-token `PROGRESS.md` no agent can read whole); cold build ~1.9 s.
Independent cold-start evals (separate question writer and grader, no confidently wrong answers):

| Project | Brief only | Brief + tools |
|---|---|---|
| xeno-gate-tactical (held out: new questions, nothing tuned on it) | 6/20 | **18/20** |
| MRPGRealms (round 2, questions reused) | 8/20 | 16/20 |

Details: [xeno-gate-tactical](docs/evals/2026-10-23-xeno-gate-tactical-cold-start.md) ·
[MRPGRealms](docs/evals/2026-10-23-mrpgrealms-cold-start.md).

## Tools

| Tool | Purpose |
|---|---|
| `session_brief(project, focus?, budget_tokens=2500)` | Git state, recent merges, last checkpoint + commits since, open tasks (stale ones flagged), warnings (stale planning docs), recent journal, every hard rule (exact `code` conventions kept), verify commands, ADR index, doc map |
| `get_section(path, heading?, project, grep?, max_tokens=2000)` | One `##` section; no heading = outline; `grep` filters huge sections |
| `recall(query, project?, kinds?, since?, limit=8)` | FTS5 search; memory hits carry a staleness line |
| `tasks(project?, status?)` | Read-only merge of `.agent-tasks.json` and `TASKS.md` |
| `audit(project?)` | Truth Audit: stale/broken memories, oversized docs, undistilled journal |
| `checkpoint(agent, summary, project, done?, next?, blockers?, decisions?, paths?, verification?, task_id?)` | Writes `.agent/memories/history/<time>-<slug>.md`, anchored to HEAD; leads the next brief |
| `record_memory(agent, kind, title, body, project, paths?, tags?, update_of?, force_new?)` | insight / decision → own file; lesson → section of `patterns_and_lessons.md`. Refuses near-duplicates and returns them as `update_of` candidates |
| `distill(project, since?, limit?, done_through?)` | Undistilled journal + checkpoints (oldest first) and current memories; `done_through` moves the git-tracked marker |

`project` is a path, a launcher-registry name (with `SPINE_REGISTRY=/path/to/registry.json`),
or (CLI only) omitted for the working directory. `agent` is who is writing:
`claude-code`, `codex`, `grok`, `hermes`, ... Every write is secret-scrubbed. Set
`SPINE_READONLY=1` on a host to refuse all writes.

Work that nobody checkpointed isn't lost: the brief lists the commits since the last
checkpoint straight from git, so no agent or hook has to write anything for it.

## Install

```bash
uv sync
uv run pytest -q
uv run spine --project ../MRPGRealms brief
```

## CLI

```bash
spine brief [--focus TEXT] [--budget N] [--hook]
spine section CONTEXT.md "Non-negotiables"
spine recall "line of sight" --kind journal --since 2026-10-01
spine tasks | spine audit | spine reindex
spine checkpoint --agent codex --summary "..." --done "..." --next "..." --verify V0=pass
spine remember --agent hermes --kind lesson --title "..." --body "..." [--update-of X | --force-new]
spine distill [--limit N] [--done-through YYYY-MM-DD]
spine nudge   # Claude Code Stop hook
```

## Using it from any agent

Spine is agent-neutral: a stdio MCP server plus a `spine` CLI that runs the same code.
Nothing in it is specific to one vendor. Each agent needs two things: the server mounted,
and a warm-start trigger.

**MCP tools need an explicit `project`** (an absolute path or a registry name). MCP clients
start servers from their own working directory, so Spine refuses to guess. To pin a
server instance to one project, set `SPINE_PROJECT` in that client's server config instead.

| Agent | Mount | Warm start |
|---|---|---|
| Claude Code | `claude mcp add spine -- uv run --project <spine-mcp> spine-mcp` | SessionStart hook (below) |
| Codex | `~/.codex/config.toml` (below) | Codex reads `AGENTS.md`: add the snippet below |
| Hermes | DynamicMCPProxy catalogue entry `spine` (below) or native MCP config | Add the snippet to `HERMES.md` / `AGENTS.md` |
| Grok, or any MCP-capable CLI | same stdio command | the snippet in whatever file it reads |
| No MCP support at all | — | shell out: `spine --project <path> brief` |

Codex:

```toml
[mcp_servers.spine]
command = "uv"
args = ["run", "--project", "/path/to/spine-mcp", "spine-mcp"]
```

Snippet for `AGENTS.md` (or `HERMES.md`, `GROK.md`, ...), the warm start for agents without hooks:

```markdown
## Session start and end (Spine)
If the `spine` MCP is available, call `session_brief(project="<absolute path of this repo>")`
first and use `get_section` / `recall` instead of reading PROGRESS.md or CONTEXT.md whole.
Without the MCP, run `spine brief` from the repo root. If neither works, ground normally.
Before you stop, call `checkpoint` with your agent name (codex, hermes, grok, ...), what was
done, what's next and any blockers. Save durable why/gotcha/decision facts with `record_memory`.
```

## Claude Code hooks

SessionStart injects the brief and records where the session started. Stop blocks once,
only if the session changed the project and wrote no checkpoint. Both never fail or
block on error. Other agents get the same behaviour from the snippet above.

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command",
        "command": "uv run --project ~/happymonkey/spine-mcp spine --project \"$CLAUDE_PROJECT_DIR\" brief --hook" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command",
        "command": "uv run --project ~/happymonkey/spine-mcp spine --project \"$CLAUDE_PROJECT_DIR\" nudge" } ] }
    ]
  }
}
```

## Dynamic MCP Proxy Entry

```json
{
  "name": "spine",
  "description": "Warm-start project context for any agent: session brief, spine search, checkpoints and deduplicated memories over Agents Protocol docs",
  "command": "uv --project /home/stephen/projects/spine-mcp run spine-mcp",
  "tags": ["context", "memory", "docs", "agents", "local"],
  "tech_stack": ["python", "mcp", "sqlite"],
  "runtime": "stdio",
  "env_vars": ["SPINE_REGISTRY", "SPINE_READONLY"]
}
```

## Spine Design (second server, slices D1–D3)

Visual memory and inventory for a project's running UI. It stores captures with history,
measures design tokens and components from the live DOM, and diffs captures. Answers are
text; pixels only come back when you ask for them. Spec: [docs/specs/design-mcp.md](docs/specs/design-mcp.md).

Mount `uv run --project <spine-mcp> spine-design-mcp` the same way as Spine.

| Tool | Purpose |
|---|---|
| `design_brief(project)` | Views and their freshness, colours by role with the app's CSS variable names, size scales, fonts, components, drift vs `DESIGN.md`. About 300–1000 tokens, no image. |
| `ensure_capture(agent, project, route?, viewport?, theme?, state?, force?)` | A fresh capture of a view, taken only when needed: reused if no UI source file changed, otherwise the server captures through BrowserOS neo itself, so no screenshot passes through the agent. Pixels are best-effort; measurements always arrive. |
| `check_design(project, view?)` | Running UI vs `DESIGN.md` tokens: % of colour use on the palette, off-palette colours (with the CSS variable behind them), font sizes / radii / spacing off scale, fonts, and mapped components' measured styles vs spec. Facts with counts. |
| `extraction_script()` | The JS to run in the page before screenshotting: masks inputs/canvas with overlays (the page's data is untouched), freezes animation, returns measurements. |
| `record_capture(agent, extraction, project, route?, viewport?, theme?, state?, image_path?, image_base64?)` | Store a capture. The screenshot is optional: measurements alone give tokens, components and style diffs. |
| `get_capture(project, view?, which?, include_image?, crop?)` | Measurements and freshness. With `include_image`, a cropped, downscaled webp. |
| `diff_captures(project, view?, a?, b?, threshold?)` | Changed regions (with the components under them), token use shifts, component style changes, UI source files changed. |
| `approve_baseline(agent, project, view?, which?, note?)` | Approve a capture as the view's reference: `.agent/design/baselines/` plus `inventory.json`, tracked and size-capped. Diffs compare against it by default. |
| `tokens` / `components` / `forget` | Detail, inventory (with `spine-design.toml` mapping to `DESIGN.md` names), retention. |

**Capturing, by browser:**

| Agent's browser | How |
|---|---|
| Playwright MCP / chrome-devtools MCP (save files) | evaluate `extraction_script` with `filename=...json`, take a screenshot with `filename=...png`, then `record_capture(extraction=<json path>, image_path=<png>)`. Send the script once and keep it on `window`: the Playwright MCP echoes code back. |
| BrowserOS neo | Don't capture yourself: call `ensure_capture`. The server drives BrowserOS through its MCP (`SPINE_BROWSEROS_URL`, default `http://127.0.0.1:9010/mcp`). Pixels are best-effort, because BrowserOS throttles background tabs. |
| Headless (CI, Linux, no BrowserOS) | `uv sync --extra playwright`, then `adapter = "playwright"` (or `SPINE_DESIGN_ADAPTER=playwright`) with `browser_channel = "chrome"` / `"msedge"`, or `playwright install chromium`. Deterministic, pixels every time. |
| Any other | measurements only, the same way |

Captures live in `<project>/.agent/design/cache/`, which ignores itself, is kept per host, and expires after 30 days or 20 per view.
`<project>/.agent/design/spine-design.toml` (optional, tracked) configures it, for example:

```toml
base_url = "http://127.0.0.1:5197"   # the dev server ensure_capture visits
wait_for = "#root *"                 # selector that means "rendered"
theme_attr = "data-theme"            # set on <html> per view theme ("" = prefers-color-scheme only)
mask = [".xterm"]                    # never captured (inputs, canvas, [data-spine-mask] always are)
pixels = true                        # false = measurements only, no images stored
adapter = "browseros"                # or "playwright" (headless)
browser_channel = "chrome"           # playwright: use an installed browser
# allowed_hosts = ["127.0.0.1", "localhost"]   # ensure_capture refuses anything else

[components]                         # DESIGN.md name -> measured component, for check_design
session-sidebar = "aside.session-drawer"

[states.history-palette]             # a named state: steps run in the page before measuring
steps = [ { press = "Control+Shift+R" }, { wait_for = ".palette-layer" } ]
```

Actions: `click`, `fill = [sel, value]`, `press = "Control+Shift+R"`, `wait_for`,
`set_attribute = [sel, attr, value]`, `pause` (≤ 2000 ms). Capture with `ensure_capture(state="history-palette")`.

## License

MIT
