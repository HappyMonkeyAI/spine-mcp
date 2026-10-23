# spine-mcp

Warm-start project context for agents. A read-only query layer over a project's
[Agents Protocol](https://github.com/HappyMonkeyAI/AgentsProtocol) documentation
spine: one `session_brief` call (~1.3k tokens) replaces reading README, CONTEXT,
TASKS and PROGRESS at session start.

Files stay the source of truth. The index is a self-ignoring cache in
`<project>/.agent/spine/` and rebuilds itself. Design: [SPEC.md](SPEC.md).

## Status

**Slice S1 (read-only) built.** Writes (`checkpoint`, `record_memory`, `distill`)
and the SessionEnd/Stop hooks are slice S2.

Measured on MRPGRealms (2026-10-23): brief 1,265 tokens vs ~10k for protocol
grounding (and a 54k-token `PROGRESS.md` no agent can read whole); cold build ~1.9 s.

## Tools

| Tool | Purpose |
|---|---|
| `session_brief(project?, focus?, budget_tokens=1500)` | Git state, last checkpoint, open tasks, recent journal, rules, doc map, warnings |
| `get_section(path, heading, project?)` | One `##` section of a file |
| `recall(query, project?, kinds?, since?, limit=8)` | FTS5 search; memory hits carry a staleness line |
| `tasks(project?, status?)` | Read-only merge of `.agent-tasks.json` and `TASKS.md` |
| `audit(project?)` | Truth Audit: stale/broken memories, oversized docs, undistilled journal |

`project` is a path, a launcher-registry name (with `SPINE_REGISTRY=/path/to/registry.json`),
or omitted for the working directory.

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
## Session start (Spine)
If the `spine` MCP is available, call `session_brief(project="<absolute path of this repo>")`
first and use `get_section` / `recall` instead of reading PROGRESS.md or CONTEXT.md whole.
Without the MCP, run `spine brief` from the repo root. If neither works, ground normally.
```

## Claude Code SessionStart hook

Injects the brief at session start. `--hook` never fails or blocks the session.

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command",
        "command": "uv run --project ~/happymonkey/spine-mcp spine --project \"$CLAUDE_PROJECT_DIR\" brief --hook" } ] }
    ]
  }
}
```

## Dynamic MCP Proxy Entry

```json
{
  "name": "spine",
  "description": "Warm-start project context: session brief, section fetch, spine search, task view and truth audit over Agents Protocol docs",
  "command": "uv --project /home/stephen/projects/spine-mcp run spine-mcp",
  "tags": ["context", "memory", "docs", "agents", "local"],
  "tech_stack": ["python", "mcp", "sqlite"],
  "runtime": "stdio",
  "env_vars": ["SPINE_REGISTRY"]
}
```

## License

MIT
