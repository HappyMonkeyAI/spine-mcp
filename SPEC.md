# Spine MCP — v1 Spec

Status: **Draft for review** · 2026-10-23
Home: `~/happymonkey/spine-mcp` (sibling of `agent-coordination-mcp`, `Resource-Sentinel-MCP`)
Stack: Python 3.10+, `mcp.server.fastmcp.FastMCP`, `uv`, SQLite FTS5 — same as the siblings.

## 1. Problem

Every session re-grounds by reading the Documentation Spine, and the spine grows
without bound. Measured on MRPGRealms (2026-10-23):

| File | Size | ≈ tokens |
|---|---|---|
| `PROGRESS.md` | 220 KB (202 entries) | ~55k — no agent can read it whole |
| `CONTEXT.md` | 18 KB | ~4.5k |
| `README.md` | 8 KB | ~2k |
| `AGENTS.md` + `MCP.md` + `TASKS.md` | 12 KB | ~3k |
| `.agent-tasks.json` | 20 KB | ~5k |
| `.agent/memories/` | **2 KB** | ~0.5k |

The protocol's `[Sanity]` rule costs about 10k tokens before any work starts, and the
episodic journal outgrew what an agent can read. LTM synthesis (protocol §2:
"Post-task: synthesize + compress") barely happens: 202 journal entries
produced 3 memory files.

## 2. What it is

A **query and write layer over the protocol's existing spine**. It is not a new
store. It answers "what were we doing, what's next, what's true, where is it"
in about 1.5k tokens, and makes writing memory cheap enough that agents actually do it.

### Principles

1. **Files are truth; SQLite is a disposable cache.** Everything Spine knows comes
   from git-tracked markdown and JSON in the project. Delete `.agent/spine/` and
   it rebuilds. Git stays the audit trail, and humans keep reading the same files.
2. **Every record is anchored to a commit.** Records carry the HEAD SHA and the paths
   they describe. Spine reports staleness ("written 14 commits ago; 3 of its paths
   changed since") rather than serving old context with confidence.
3. **Store intent, not maps.** Where code lives is answered live (grep/LSP).
   Spine stores what search can't recover: why, what failed, what's next,
   what was verified.
4. **Don't own what siblings own.** Tasks belong to `.agent-tasks.json`/`TASKS.md`
   (agent-coordination), project locations to launcher-registry, the user to
   user-context. Spine reads them and does not fork them.

### Boundary with siblings

| Concern | Owner | Spine's role |
|---|---|---|
| Project paths / ports | launcher-project-registry | resolve project name → root (reads `registry.json` if `SPINE_REGISTRY` set) |
| Task board, assignments | agent-coordination-mcp, `.agent-tasks.json`, `TASKS.md` | read-only merged view in the brief |
| Agent messaging | agent-communication-mcp | none |
| User prefs/stack | user-context-mcp | none |
| Episodic + semantic project memory | **Spine** | read + write |
| Visual/UI memory | future design collector (§9) | shares Spine core |

## 3. Storage

```
<project>/
  .agent/memories/                      # protocol LTM — git-tracked, Spine reads+writes
    codebase_insights/<slug>.md
    architectural_decisions/<slug>.md
    patterns_and_lessons.md
    history/<YYYY-MM-DDTHHMMZ>-<slug>.md   # NEW: checkpoints, one file each
  .agent/spine/                         # gitignored, rebuildable
    index.sqlite
```

Checkpoints are **one file per entry**, not an appended log. That way parallel
worktrees merge without conflicts, and the files land where the protocol already puts
episodic memory (`history/`).

### Checkpoint file

```markdown
---
kind: checkpoint
id: 2026-10-23T1412Z-crown-review
created: 2026-10-23T14:12:00Z
agent: claude-code            # or hermes, codex, ...
sha: e5ae99a
branch: agent/crown
task_id: crown                # optional, links to .agent-tasks.json
paths: [server/src/world/season.ts, client/src/crown.ts]
verification: {V0: pass, V1: skip, V2: pass, V3: pass}
mechanical: false             # true = written by hook from git, no narrative
---
## Summary
Final siege + crown merged (#83). Review fixes applied.
## Done
- ...
## Next
- Chaos Sessions (R4b) or merit for more war deeds — undecided
## Blockers / open questions
- ...
## Decisions
- Crown contenders use a set lookup (perf) — not ADR-worthy
```

### Memory file (insight / decision / lesson)

The existing protocol files gain optional frontmatter. Files without it still
index, and their anchor falls back to the file's own last commit.

```markdown
---
kind: insight
title: World streaming
tags: [render, chunks]
paths: [client/src/render/chunkWorker.ts, shared/noiseWorld.ts]
sha: 3f1c2aa
updated: 2026-09-28
---
```

### Index (SQLite)

- `docs(path, kind, content_sha256, mtime, git_last_sha)`
- `chunks(id, path, heading, line_start, line_end, text, date, anchor_sha, anchor_paths_json)`
  where every `##` section of every spine doc is one chunk. `PROGRESS.md`'s
  `## YYYY-MM-DD — title` entries become individually dated chunks.
- `chunks_fts` (FTS5 over heading + text)
- Refresh is lazy: on each call, re-stat the tracked files and re-chunk only those
  whose hash changed. There is no daemon and no watcher.

Indexed sources: `README.md`, `CONTEXT.md`, `AGENTS.md`, `MCP.md`, `TASKS.md`,
`PROGRESS.md`, `MANIFEST.md`, `docs/adr/*.md`, `docs/plans/*.md`,
`.agent/memories/**`, `.agent-tasks.json`. You can extend the list with `.agent/spine.toml`.

## 4. Tools (v1)

Eight tools, chosen to keep the catalogue cheap. Each takes `project` (a path or a registry
name, defaulting to cwd) and returns compact JSON or markdown with `source: path:line`
on every fact.

| Tool | Slice | Purpose |
|---|---|---|
| `session_brief(project, focus?, budget_tokens=1500)` | S1 | The warm start (below). |
| `get_section(project, path, heading)` | S1 | Return one `##` section of any indexed doc. Replaces reading all of `CONTEXT.md` for one rule. |
| `recall(project, query, kinds?, since?, limit=8)` | S1 | FTS search across all chunks → snippets + source + date + staleness. |
| `tasks(project, status?)` | S1 | Merged read-only view of `.agent-tasks.json` (status, owner, worktree, verification) and `TASKS.md` checkboxes. |
| `audit(project)` | S1 | The Truth Audit (protocol §2): stale memories, memories naming paths that no longer exist, oversized docs, journal entries never distilled into memory. |
| `checkpoint(project, summary, done[], next[], blockers[], decisions[], paths[], verification?, task_id?)` | S2 | Write a `history/` file. Stamps SHA, branch, agent and time. Returns path. |
| `record_memory(project, kind, title, body, paths[], tags[], update_of?)` | S2 | Write or update an insight, decision or lesson. Returns near-duplicates **first** (FTS on title+body). If any exist, it requires `update_of` or `force_new=true`, so memories don't fork. |
| `distill(project, since?)` | S2 | Return journal/checkpoint entries since the last distillation plus current memories, so the calling agent can propose `record_memory` calls. Spine never summarises with its own LLM; the agent does the thinking. |

Resource: `spine://brief` mirrors `session_brief` with defaults, for clients that
pull resources at start.

### `session_brief` contents (in priority order, cut to fit the budget)

1. **Git state:** branch, HEAD, dirty file count, active `.worktrees/*` and their branches.
2. **Last checkpoint(s):** summary, next, blockers, plus a staleness line.
3. **Active tasks:** non-complete `.agent-tasks.json` entries and the top `TASKS.md` "Next" items, each truncated to one line.
4. **Recent journal:** the last 5 `PROGRESS.md` headlines with dates, titles only.
5. **Non-negotiables:** headings of `CONTEXT.md` "Non-negotiables" / "What not to do" plus a `get_section` pointer. Rules are named here and fetched on demand.
6. **Focus hits:** if `focus` is given, the top 3 `recall` results.
7. **Doc map:** each spine file with its size and token estimate, so the agent knows what's expensive.
8. **Warnings:** stale memories touching dirty or recent paths, and docs over budget.

Token estimate is chars/4. If a section doesn't fit, it's dropped and listed by
name under `omitted: [...]`, so nothing silently disappears.

## 5a. Agent neutrality

You use Claude Code, Codex, Grok and Hermes on the same projects, so no part of Spine may
depend on one vendor:

- **The interfaces are MCP over stdio plus the `spine` CLI**, which run the same code.
  Anything that can call an MCP tool or run a shell command can use Spine.
- **There is no implicit project.** MCP tools require `project` (or `SPINE_PROJECT` in that
  client's server config), because clients launch servers from their own cwd.
- **Server `instructions`** tell any client to call `session_brief` first, so the server's
  own handshake does the work and no per-vendor prompt is needed.
- **Every agent's instruction files are indexed and searchable** (`AGENTS.md`, `CLAUDE.md`, `HERMES.md`,
  `GEMINI.md`, `GROK.md`, `.github/copilot-instructions.md`), so Codex can see a rule that
  was only written in `CLAUDE.md`.
- **Concurrent use is expected.** SQLite runs in WAL mode, and refresh and schema creation take
  the write lock up front. Tested with 6 parallel processes on a cold index.
- **Records name their writer** (S2). `checkpoint`/`record_memory` take a required
  free-text `agent` (`claude-code`, `codex`, `grok`, `hermes`, ...). The server can't know
  which client called it.
- **Hooks are an optimisation, not a dependency.** Only Claude Code has SessionStart/Stop
  hooks today. The tiers in §5 must hold for agents without them.

## 5. Getting agents to write

Agents won't write to Spine reliably on their own, so there are two tiers:

| Tier | Trigger | Writes | Needs agent? |
|---|---|---|---|
| **Mechanical (lazy)** | Next `session_brief` from *any* agent sees commits since the last checkpoint with no checkpoint covering them | `history/` file from `git log`/`diff --stat` for that range. No narrative. Works for every agent, with no hooks. | No |
| **Mechanical (hook)** | `SessionEnd` hook → `spine checkpoint --mechanical` (Claude Code only, an optimisation) | Same, captured at session end so uncommitted work is noted too | No |
| **Narrative** | Agent calls `checkpoint` | Full summary, next steps, decisions | Yes |

The nudge is a `Stop` hook running `spine nudge`. It blocks the stop **once** with
"call spine.checkpoint" only when source files changed since the last
checkpoint *and* there is no narrative checkpoint for this session. It's silent otherwise.

Warm start is a `SessionStart` hook running `spine brief --project "$CLAUDE_PROJECT_DIR"`,
which injects the brief as context. Non-Claude agents (Hermes, Codex) get the
same through one AGENTS.md line plus the MCP. The CLI and MCP share one code path.

The CLI entry points (`spine brief|checkpoint|nudge|audit|reindex`) mean hooks
work even when the MCP isn't mounted ("listed ≠ mounted").

## 6. Protocol amendment (proposed ADR in AgentsProtocol)

- `[Sanity]`: when Spine is mounted, `session_brief` satisfies grounding. Read
  full spine files only on demand via `get_section`/`recall`.
- Post-task: `checkpoint` replaces appending to `PROGRESS.md`. `PROGRESS.md`
  stays as the human-readable journal, and Spine can render it from `history/`
  later (S3, opt-in).
- Truth Audit every ~10 tasks becomes `audit` + `distill`.

## 7. Security

- Operates only inside the resolved project root. Path arguments are normalised and
  rejected if they escape it, and symlinks are resolved before the check.
- Writes are limited to `.agent/memories/**` and `.agent/spine/**`. It never touches
  source, `CONTEXT.md`, `README.md` or task boards.
- A secret scrub runs on every write (common token/key patterns, `.env`-style
  `KEY=value`). Matches are redacted and the response says so.
- v1 is stdio only. A LAN/HTTP transport would reuse agent-communication-mcp's auth
  model and is out of scope here.
- Returned memory is project data, not instructions. The brief is framed as such.

## 8. Slices and acceptance

**S1 — read-only (no write risk, useful on day one)**
index, `session_brief`, `get_section`, `recall`, `tasks`, `audit`, CLI `brief`,
SessionStart hook.

Acceptance, on MRPGRealms:
- `session_brief` ≤ 1.5k tokens (vs ~10k grounding today). It reports the
  correct branch, active worktrees, open `.agent-tasks.json` tasks and last 5 progress entries.
- Blind test: a fresh agent given only the brief answers 5 written questions
  ("what shipped last?", "what's next on Realm War?", "is wildlife active?",
  "what must not be done to block visuals?", "which branch is integration?").
  V2 means someone other than the builder writes the questions and grades them.
- Reindex after editing one doc touches only that doc. A cold index of MRPGRealms takes < 2 s.
- `audit` flags `world-streaming.md` as stale if its paths changed since its anchor.
- Path-escape attempts (`../`, absolute paths, symlinks out) are rejected (V2).

**S2 — writes and nudges**
`checkpoint`, `record_memory` (with dedupe), `distill`, mechanical
SessionEnd checkpoint, Stop nudge.

Acceptance:
- Two worktrees checkpoint concurrently, then merge with no conflict.
- `record_memory` on a near-duplicate title returns the existing file and doesn't create one.
- Secret scrub catches seeded fake keys (V2).
- Ending a session that edited files always leaves a `history/` file, mechanical or narrative.

**S3 — integration**
DynamicMCPProxy catalogue entry (`spine`), launcher-registry name resolution,
opt-in PROGRESS.md rendering from `history/`, AgentsProtocol ADR + BOOTSTRAP step.

## 9. Seam for the UI Design MCP

The design MCP reuses Spine core: project resolution, SHA anchoring and staleness,
the FTS index, the secret and PII scrub, and retention. Its records are captures
keyed by `(route, viewport, theme, sha)` plus extracted tokens/components, stored
under `.agent/design/` with images gitignored and metadata tracked. To keep that
possible, package layout in v1 is `spine_core/` (index, anchors, resolve, scrub)
and `spine_mcp/` (dev tools). There is no other design work in v1.

## 10. Non-goals (v1)

Owning tasks · editing source or spine docs other than memories · embeddings/vector
search (FTS5 first, measure, then decide) · LLM summarisation inside the server ·
LAN transport · cross-project search.

## 11. Pre-mortem

| Failure | Mitigation |
|---|---|
| Brief is confidently wrong after a big merge | SHA anchors and staleness on every item. Brief leads with git state. |
| Agents ignore `checkpoint` | Mechanical tier guarantees a floor. Stop nudge, once per session. |
| Memory sprawl / duplicates | `record_memory` dedupe gate. `audit` reports near-duplicates. |
| Becomes a third task system | Tasks are read-only (§2 boundary). |
| Brief grows until it's the new CONTEXT.md | Hard token budget, `omitted:` list, pointers instead of content. |
| Index corrupt or out of date | Disposable cache. `spine reindex`, and hash check on every call. |
| Windows/Linux path and CRLF issues (you run both) | Normalise to POSIX paths in the index. Hash content with line endings normalised. Test on both. |

## 12. Open questions for you

1. **Name:** `spine-mcp` (ties to the protocol's "Documentation Spine") or something else?
2. **Commit `history/`?** Recommended yes: it travels across your machines and
   worktrees. The alternative is gitignored, local-only history.
3. **Stop-hook nudge:** on by default, or opt-in?
4. **First pilot project:** MRPGRealms (follows the protocol, huge journal), or a smaller one?
