# Jules task: Native Cursor hook JSON from `spine brief`

## Context
Cursor `sessionStart` hooks must print JSON `{"additional_context":"..."}` on stdout (stdin is the hook event). Today `spine brief --hook` prints the brief as plain text for Claude Code SessionStart. Pilots wrap this in PowerShell (Roadmap / vibes-desktop `.cursor/hooks/spine-brief.ps1`). Spine should emit Cursor-shaped JSON natively so hooks stay one line and fail-open without a wrapper.

## Exact files/paths
- Touch: `src/spine_mcp/cli.py` — add `--cursor-hook` on `brief` (compatible with `--hook`). When set, print a single JSON object `{"additional_context": "<brief>"}` (UTF-8), never fail the process on brief errors (print `{}` and exit 0). Still honor `--hook` session-start recording when both are set / when `--cursor-hook` alone and stdin has `session_id`.
- Touch: `tests/` — cover JSON shape, fail-open on bad project, and that `additional_context` contains the brief title line.
- Touch: `README.md` — short Cursor hooks subsection: example `.cursor/hooks.json` calling `spine --project . brief --cursor-hook` (Windows PowerShell one-liner or `uv run` equivalent); note Agents Window may need editor Agent panel for `sessionStart` delivery.
- Optional: `examples/cursor-hooks/hooks.json` + `README` snippet only if kept tiny.

Do **not** change MCP tool schemas, design adapters, or registry lookup beyond what’s needed for the CLI flag.

## Required behavior
1. `spine --project <path> brief --cursor-hook` writes one JSON object to stdout; exit 0.
2. On resolve/brief failure: stdout `{}`, exit 0 (fail-open).
3. Existing `brief --hook` plain-text behavior unchanged when `--cursor-hook` is absent.
4. Brief body in `additional_context` is capped reasonably (e.g. respect `--budget` as today).

## Hard constraints
- Smallest complete PR; no drive-by refactors or new dependencies.
- No secrets; do not commit host `~/.spine` paths or `.env`.
- Keep `mcp<2` / Python 3.11+.

## Acceptance criteria
- [ ] `uv run pytest -q` exits 0 (includes cursor-hook cases)
- [ ] Manual: `echo {} | uv run spine --project . brief --cursor-hook` prints parseable JSON with `additional_context`
- [ ] README documents the Cursor hook one-liner
- [ ] PR lists files changed and how to test

## Deliverable
Open a **GitHub pull request** with the code changes (not plan-only). Smallest complete change; list files changed + how to test.
