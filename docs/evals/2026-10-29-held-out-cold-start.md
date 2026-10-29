# Eval: held-out cold-start (next measurement)

Honest re-measure after PRs #5–#6 closed the 2026-10-23 grader gaps.
**Do not reuse** the [MRPGRealms](2026-10-23-mrpgrealms-cold-start.md) or
[xeno-gate-tactical](2026-10-23-xeno-gate-tactical-cold-start.md) question sets.

## Target

| Field | Value |
|---|---|
| Project | **TBD** — pick one Agents Protocol repo that is *not* MRPGRealms or xeno-gate-tactical (candidates: `virtual-office`, `AgentsProtocol`, or another registry entry with CONTEXT/PROGRESS/ADRs) |
| Spine | current `main` (post #6) |
| Date run | _pending_ |

## Method

Same four-role protocol as 2026-10-23:

1. **Question writer** — full repo read; 10 questions with `must_include` keys (current ×2, next ×2, rules ×2, workflow, ADR, deep ×2). Never sees Spine output.
2. **Run A** — `session_brief` only.
3. **Run B** — brief + at most 15 `spine` CLI / MCP calls; no direct file reads.
4. **Grader** — scores 0/1/2 per answer; flags stale or confidently wrong claims.

## Results (empty until run)

| | Run A (brief only) | Run B (brief + tools) | Confidently wrong | Stale as fact |
|---|---|---|---|---|
| _target_ | _/_20 | _/_20 | _ | _ |

Grounding cost: brief ~_k tokens. Run B used ~_k above baseline.

## Structural checks already green (2026-10-29)

Against the gaps those graders named (not a scored re-run):

| Gap (2026-10-23) | Signal on current brief |
|---|---|
| ADR status vs completed plan | xeno ADR 0008 status updated; drift warning clears after index refresh |
| App-level verify / smoke Run | `apps/xeno-tactical/docs/smoke-test.md` Run commands in verify |
| Known-bug why-unfixed | MRPG `## known issues` carries newest `Known:` / `Found, not caused here` bullets |

## Operator notes

- Wipe or refresh `.agent/spine/` on the target if ADR/docs just changed and the brief looks stale.
- Prefer a project Spine was **not** tuned against while landing #5/#6.
