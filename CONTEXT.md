# Context

## Runtime

- Python 3.11+ (`tomllib`), `uv`.
- MCP via `mcp.server.fastmcp.FastMCP`, pinned `mcp<2` (2.x renamed it `MCPServer`; siblings are on 1.x).
- SQLite FTS5 index at `<project>/.agent/spine/index.sqlite`, self-ignored by a `*` `.gitignore` in that folder.
- `spine_core/` is collector-neutral (resolve, git, markdown, index, anchors, scrub); `spine_design/` (the second server) reuses it.
  `spine_mcp/core.py` holds the read tools, `writes.py` the write tools; `server.py` and `cli.py` are thin wrappers.
- `spine_design/` needs Pillow; screenshots are stored as lossless webp under `<project>/.agent/design/cache/` (self-ignored, per host).

## Non-negotiables

- Project files are the source of truth; the index is disposable and rebuilt from them.
- Every path argument goes through `safe_path`; nothing is read outside the resolved project root.
- Writes land only in `.agent/memories/**` (agent-authored, git-tracked) and `.agent/spine/**` (ignored cache, session state).
- Reads never write project files: brief/recall/audit/distill-without-done_through touch only `.agent/spine/`.
- Spine never commits; every write is secret-scrubbed and names its `agent`.
- Child processes never inherit stdin (`stdin=DEVNULL`): under MCP it is the protocol pipe.
- Tasks are read, never written (agent-coordination-mcp and `.agent-tasks.json` own them).
- Every brief line cites `path:line`; sections that miss the budget are named in `omitted`, never cut silently.
- Hooks (`brief --hook`, `nudge`) never fail or block a session.

## What not to do

- Do not add an LLM call inside the server; summarising is the calling agent's job.
- Do not add a file watcher or daemon; refresh is lazy, on each call.
- Do not edit README/CONTEXT/source in target projects.
- Do not upgrade to `mcp` 2.x here alone; migrate with the siblings.

## Current plan

S1–S3 built (proxy `user.catalogue.json`, default `~/.spine` + launcher-registry resolution,
AgentsProtocol ADR-0005 + BOOTSTRAP). Deferred: opt-in `PROGRESS.md` render from `history/`.
Grader-reported brief gaps remain open (docs/evals/): ADR one-liners with their reasons,
stale "next" items, and exact conventions and gates.
