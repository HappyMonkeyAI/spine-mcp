# Patterns and lessons — spine-mcp

## render_progress preserves hand PROGRESS.md

render_progress(write=true) used to replace PROGRESS.md entirely from checkpoint summaries. Hand-authored journals are now preserved: write goes to PROGRESS.spine.md unless force=true (then PROGRESS.md.bak + overwrite). Agents must not call write=true unless the owner asked or the file is already Spine-rendered; prefer checkpoint/record_memory.

_cursor, 2026-11-16, a41ac0b; paths: `src/spine_mcp/writes.py`, `AGENTS.md`, `AGENT-PROMPT.md`_
