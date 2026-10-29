# Eval: cold-start grounding on xeno-gate-tactical (2026-10-23)

This is the held-out check on the [MRPGRealms eval](2026-10-23-mrpgrealms-cold-start.md): a
different project and new questions, and Spine was **not tuned on either**. The brief was
generalised (commit `03467b4`) before the question writer started, using only structural gaps
visible from briefing the repo. The questions were written afterwards, and Spine was not
changed between the questions and the grading.

## Method

Same as MRPGRealms. An independent agent read the repo in full and wrote 10 questions with
an answer key (current state ×2, next ×2, rules ×2, workflow, ADR, deep detail ×2).
**Run A** had the brief only. **Run B** had the brief plus at most 15 `spine` CLI calls. A separate
grader scored each answer 0/1/2 against `must_include` facts.

## Result

| | Run A (brief only) | Run B (brief + tools) | Confidently wrong | Stale stated as fact |
|---|---|---|---|---|
| xeno-gate-tactical | **6/20** | **18/20** | 0 | 0 |
| MRPGRealms round 2, for comparison (questions reused, tuned) | 8/20 | 16/20 | 0 | 1 |

Grounding cost: brief ~1.4k tokens. Run B used ~18k tokens above an agent's ~55k baseline,
against ~50k for the question writer's full read.

Read: the brief alone is a map (current work, next step, rules, verify commands). With the
tools, an agent reaches about 90% on questions nobody tuned for, at about a third of the cost of reading
everything, and with no confident errors.

## Remaining gaps (grader)

- **ADR titles aren't enough.** One rule line per ADR would have answered several brief-only
  misses (for example "FULL cover blocks movement and ranged fire").
- **Doc drift isn't flagged.** ADR 0008's status still says "in progress" though its plan shows
  P0–P8 done. Neither run noticed.
- **App-level operating notes are missing.** The brief has the root verify commands, but not
  `apps/xeno-tactical`'s own steps or PROGRESS's known-behaviour and awaiting-confirmation items.

Tool feedback from Run B:
- recall cites a section's start rather than the matching bullet
- `section --grep` needs a heading (it can't grep a whole file)
- no path filter on recall (e.g. `apps/xeno-tactical`)
- ADR filenames aren't in the brief


## Follow-up (2026-10-29)
Addressed in PR #5 / Jules: ADR lines now include decision reason/limitation; verify/convention clipping improved.

Remaining gaps closed in PR #6: unsettled ADR status vs linked plan that claims complete; app-level verify steps from `apps/*/docs`; known-issues section from Known: / Known limits / inline findings.

Honest next score: [2026-10-29 held-out cold-start](2026-10-29-held-out-cold-start.md) (fresh questions, different project).

