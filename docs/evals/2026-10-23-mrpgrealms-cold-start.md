# Eval: cold-start grounding on MRPGRealms (2026-10-23)

Spec §8 S1 acceptance: a fresh agent grounded only by Spine answers questions about
the project. To keep it independent (V2), separate agents wrote the questions, took the test, and graded it.

## Method

1. **Question writer:** read the repo in full (docs, task board, ADRs, plans, git) and wrote 10
   questions with an answer key. Categories: current state ×2, next ×2, rules ×2, workflow,
   ADR, deep detail ×2. It never saw Spine output.
2. **Cold agent, run A:** given only the `session_brief` text.
3. **Cold agent, run B:** the brief plus at most 15 `spine` CLI calls; no direct file reads.
4. **Grader:** a separate agent scored each answer 0/1/2 against the key's `must_include` facts and flagged
   stale or confidently wrong claims.

## Results

| Round | Brief | Run A (brief only) | Run B (brief + tools) | Confidently wrong |
|---|---|---|---|---|
| 1 | v1, ~1.3k tokens | 3/20 | 14/20 (15 calls) | 0 |
| 2 | v2, ~1.9k tokens | **8/20** | **16/20** (13 calls) | 0 |

Grounding cost above each agent's ~55k baseline: full manual read ~52k tokens (question writer),
run A ~4k, run B ~21–26k.

## What round 1 exposed, fixed in round 2

- **Git wasn't treated as the record of what shipped.** The brief now lists recent merges, marks journal
  headlines "merged #N", and flags task-board entries whose branch already merged.
- **Lines were cut mid-sentence.** Clipping now happens at sentence or clause boundaries.
- **Rules were missing.** The brief now shows every rule from `CONTEXT.md` and all agent
  instruction files (`AGENTS.md`, `HERMES.md`, ...), including `###` subsections.
- **Tools were awkward.** `get_section` gained an outline (no heading), `--grep` and `--max-tokens`. Recall
  now tops up all-term matches with any-term matches, and snippets are longer.

## Caveat

Round 2 used the **same questions** as round 1, and the fixes were informed by round 1's misses.
Some of the gain is probably fitting to this test. A fresh question set on another project is the
honest next measurement.

## Remaining gaps (grader, round 2)

- The brief doesn't carry each ADR's decision with its reason and limitation, and doesn't say why
  each known bug has no obvious fix.
- Stale *content* still gets through: "next" items that already shipped are listed as upcoming
  (citadel/keep). Only stale *labels* are detected so far.
- Exact conventions and numbers (worktree path pattern, V0 commands, numeric gates) are hard to
  find, even with search.

## Follow-up (2026-10-29)
Addressed in PR #5 / Jules: ADR decision+reason/limitation in brief; shipped next-items flagged against merges/commits; conventions/gates (V0, numerics) preserved in clip_rule/verify. Known-bug why-unfixed narratives remain open.
