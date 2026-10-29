---
kind: checkpoint
created: 2026-10-29T15:46:01Z
agent: cursor
sha: 2f35ebc
branch: feat/brief-remaining-eval-gaps
paths: [src/spine_mcp/core.py, tests/test_core.py, docs/evals/2026-10-23-xeno-gate-tactical-cold-start.md, docs/evals/2026-10-23-mrpgrealms-cold-start.md]
verification: {V0: pass, V1: pass}
id: 2026-10-29T1546Z-closed-remaining-cold-start-eval-gaps-adr-status
---
# Checkpoint: Closed remaining cold-start eval gaps: ADR status vs completed plan drift, app-…

## Summary

Closed remaining cold-start eval gaps: ADR status vs completed plan drift, app-level verify steps, known-issues section.

## Done

- ADR plan-completion drift warning when unsettled status links a plan that says complete
- Brief verify section pulls apps/*/docs Run/smoke commands
- Brief known issues from Known:/Found-not-caused/Observed bullets and Known limits sections
- Tests + dogfood on xeno-gate-tactical and MRPGRealms

## Next

- Open PR and merge feat/brief-remaining-eval-gaps
- Optional: re-run cold-start eval grader on fresh questions

## Blockers

- (none)

## Decisions

- Prefer newest Known: bullets first so the brief cap surfaces recent why-unfixed notes
- Do not match bare 'limit' in headings (false-positive on Sonar complexity-limit journal titles)
