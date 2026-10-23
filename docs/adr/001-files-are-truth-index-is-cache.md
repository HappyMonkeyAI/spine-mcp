# ADR 001: Files Are Truth, the Index Is a Cache

## Status

Accepted

## Context

Agents re-ground every session by reading the Documentation Spine, which grows without
bound (MRPGRealms `PROGRESS.md`: 54k tokens). A server-owned database would make
grounding cheap, but would hide project knowledge from git and from people, and
would compete with the files the protocol already mandates.

## Decision

Spine is a query layer over the existing spine files. It keeps only a disposable
SQLite/FTS5 index under `<project>/.agent/spine/` and refreshes it lazily by stat and
content hash on every call. Recorded knowledge is anchored to commits (frontmatter
`sha`, else the file's last commit) so staleness is reported, not hidden.

## Consequences

- Deleting `.agent/spine/` loses nothing; git remains the audit trail.
- Memory written by Spine (S2) goes to `.agent/memories/` as plain markdown, readable without the server.
- Staleness costs one or two `git` calls per memory, which is fine at protocol memory counts
  but should be revisited if a project holds hundreds of memories.
