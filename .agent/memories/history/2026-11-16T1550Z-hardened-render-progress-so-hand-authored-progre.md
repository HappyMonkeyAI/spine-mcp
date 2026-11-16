---
kind: checkpoint
created: 2026-11-16T15:50:40Z
agent: cursor
sha: 4459433
branch: main
paths: [src/spine_mcp/writes.py, src/spine_mcp/server.py, tests/test_writes.py, AGENTS.md, AGENT-PROMPT.md]
verification: {V0: pass, V1: pass, V2: skip, V3: skip, V4: skip}
id: 2026-11-16T1550Z-hardened-render-progress-so-hand-authored-progre
---
# Checkpoint: Hardened render_progress so hand-authored PROGRESS.md is preserved (PROGRESS.sp…

## Summary

Hardened render_progress so hand-authored PROGRESS.md is preserved (PROGRESS.spine.md / force+.bak).

## Done

- Hand-authored PROGRESS → PROGRESS.spine.md; force writes .bak then replaces
- AGENTS/AGENT-PROMPT/README/CONTEXT + AgentsProtocol AGENTS guidance
- Tests 28/28 writes; .215 has no SPINE_RENDER_PROGRESS
- Pushed spine-mcp 4459433; kb_vault log; AgentsProtocol commit

## Next

- Reload Spine MCP clients so tool schema picks up force=
- Optional: pull spine-mcp on .215 if agents use that checkout

## Blockers

- (none)

## Decisions

- (none)
