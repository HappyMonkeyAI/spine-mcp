# Jules task: Accept UTF-8 BOM in SPINE_REGISTRY JSON

## Context
`spine_core.resolve._lookup_registry` reads `SPINE_REGISTRY` with `encoding="utf-8"` then `json.loads`. On Windows, editors and PowerShell `Set-Content -Encoding utf8` often write a UTF-8 BOM (`\ufeff`). That makes `json.loads` raise `ValueError`, which the function swallows, so registry name lookup silently returns “project not found” even when the file is valid. Confirmed locally: the same `registry.json` parses with `utf-8-sig` and fails under plain `utf-8`.

## Exact files/paths
- Touch: `src/spine_core/resolve.py` — read the registry with `encoding="utf-8-sig"` (or equivalent BOM-safe decode before `json.loads`).
- Touch: `tests/test_core.py` — extend `test_registry_lookup_matches_launcher_registry_shape` (or add a sibling test) that writes a BOM-prefixed registry file and asserts `resolve_project("MRPGRealms")` (or the fixture project name) still resolves.

Do **not** change MCP server tools, CLI UX, design adapters, or launcher-registry schema.

## Required behavior
1. A `SPINE_REGISTRY` file that is otherwise valid JSON but starts with UTF-8 BOM resolves project names the same as a BOM-free file.
2. Missing / invalid registry files still return `None` / raise as today (no new crash paths).
3. Existing loose name matching (`MRPGRealms`, `mrpg-realms`, etc.) remains unchanged.

## Hard constraints
- Smallest complete change; no drive-by refactors or new dependencies.
- No secrets; do not commit `.env`, tokens, or host-local registry paths under `~/.spine/`.
- Do not rewrite resolve.py beyond the read/decode path and tests.
- Keep Python 3.11+ compatibility.

## Acceptance criteria
- [ ] `uv run pytest -q` exits 0 (includes a BOM registry case)
- [ ] With a BOM-prefixed temp `registry.json` and `SPINE_REGISTRY` set, `resolve_project(<registered name>)` returns the project path
- [ ] PR description lists files changed and how to test

## Deliverable
Open a **GitHub pull request** with the code changes (not a plan-only session). Smallest complete change; list files changed + how to test.
