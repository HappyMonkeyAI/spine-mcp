# Context

## Runtime

- Python 3.11+ (`tomllib`), `uv`.
- MCP via `mcp.server.fastmcp.FastMCP`, pinned `mcp<2` (2.x renamed it `MCPServer`; siblings are on 1.x).
- SQLite FTS5 index at `<project>/.agent/spine/index.sqlite`, self-ignored by a `*` `.gitignore` in that folder.
- `spine_core/` is collector-neutral (resolve, git, markdown, index, anchors) so the UI design collector can reuse it.
  `spine_mcp/core.py` holds the dev tools; `server.py` and `cli.py` are thin wrappers over it.

## Non-negotiables

- Project files are the source of truth; the index is disposable and rebuilt from them.
- Every path argument goes through `safe_path`; nothing is read outside the resolved project root.
- S1 writes nothing in a project except `.agent/spine/`.
- Tasks are read, never written (agent-coordination-mcp and `.agent-tasks.json` own them).
- Every brief line cites `path:line`; sections that miss the budget are named in `omitted`, never cut silently.
- Hooks (`--hook`) never fail or block a session.

## What not to do

- Do not add an LLM call inside the server; summarising is the calling agent's job.
- Do not add a file watcher or daemon; refresh is lazy, on each call.
- Do not edit README/CONTEXT/source in target projects.
- Do not upgrade to `mcp` 2.x here alone; migrate with the siblings.

## Current plan

S2 per SPEC.md §8: `checkpoint`, `record_memory` with a dedupe gate, `distill`,
mechanical SessionEnd checkpoint, Stop-hook nudge. Open decisions are in SPEC.md §12.
