# AGENTS.md — spine-mcp

Behavior notes for agents working in this repo. Shared protocol law lives in [AgentsProtocol](https://github.com/HappyMonkeyAI/AgentsProtocol) (ADR-0005 Spine warm-start).

## Session start and end (Spine)
If the `spine` MCP is available, call `session_brief(project="spine-mcp")` (or the absolute path of this repo) first and use `get_section` / `recall` instead of reading PROGRESS.md or CONTEXT.md whole. Without the MCP, run `spine brief` from the repo root. If neither works, ground normally.
Before you stop, call `checkpoint` with your agent name (e.g. `cursor`), what was done, what's next and any blockers. Save durable why/gotcha/decision facts with `record_memory`.

**`render_progress`:** dry-run by default. Do **not** call `write=true` (or rely on `SPINE_RENDER_PROGRESS=1`) unless the owner asked or `PROGRESS.md` already says it was rendered by Spine. Prefer `checkpoint` / `record_memory` over rewriting the human journal. Hand-authored `PROGRESS.md` is preserved (Spine writes `PROGRESS.spine.md`); `force=true` overwrites only after `PROGRESS.md.bak`.
