import os
from pathlib import Path

import pytest

from conftest import commit, write
from spine_core.index import SpineIndex
from spine_core.markdown import sections
from spine_core.resolve import SpinePathError, safe_path
from spine_mcp import core


def test_sections_skip_fenced_headings_and_track_frontmatter_lines() -> None:
    text = "---\nkind: insight\npaths: [a/b.ts, c.ts]\n---\n# Title\n\nintro\n\n## One\n\n```\n## not a heading\n```\n\n## Two\nbody\n"
    meta, parts = sections(text)
    assert meta == {"kind": "insight", "paths": ["a/b.ts", "c.ts"]}
    assert [p.heading for p in parts] == ["Title", "One", "Two"]
    assert parts[1].line_start == 9  # '## One' is line 9 of the original file
    assert "## not a heading" in parts[1].text


def test_journal_entries_are_dated_newest_first(project: Path) -> None:
    with SpineIndex(project) as index:
        index.refresh()
        assert [r["date"] for r in index.journal(5)] == ["2026-10-07", "2026-10-06"]


def test_refresh_only_reindexes_changed_docs(project: Path) -> None:
    with SpineIndex(project) as index:
        first = index.refresh()
        assert first["reindexed"] == first["scanned"] > 3
        assert index.refresh()["reindexed"] == 0
        write(project, "TASKS.md", "# TASKS\n\n## Next\n\n- [ ] Something else\n")
        assert index.refresh()["reindexed"] == 1
        (project / "TASKS.md").unlink()
        assert index.refresh()["removed"] == 1


def test_crlf_only_change_does_not_reindex(project: Path) -> None:
    path = project / "README.md"
    path.write_bytes(b"# Proj\n\nA test project.\n")
    with SpineIndex(project) as index:
        index.refresh()
        path.write_bytes(b"# Proj\r\n\r\nA test project.\r\n")
        assert index.refresh()["reindexed"] == 0


def test_recall_falls_back_to_any_term(project: Path) -> None:
    hits = core.recall(str(project), "identity unrelatedword")["results"]
    assert hits and hits[0]["heading"].startswith("2026-10-07")


def test_recall_reports_staleness_for_memories(project: Path) -> None:
    hits = core.recall(str(project), "chunks stream", kinds=["insight"])["results"]
    assert hits[0]["source"].startswith(".agent/memories/codebase_insights/world-streaming.md")
    assert "missing" in hits[0]["staleness"]


@pytest.mark.parametrize("bad", ["../outside.md", "/etc/passwd", "C:\\Windows\\win.ini", "docs/../../x.md"])
def test_safe_path_rejects_escapes(project: Path, bad: str) -> None:
    with pytest.raises(SpinePathError):
        safe_path(project, bad)


def test_safe_path_rejects_symlink_out(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.md"
    outside.write_text("## s\nsecret\n", encoding="utf-8")
    try:
        os.symlink(outside, project / "link.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this host")
    with pytest.raises(SpinePathError):
        core.get_section(str(project), "link.md", "s")


def test_get_section_exact_then_substring(project: Path) -> None:
    result = core.get_section(str(project), "CONTEXT.md", "non-neg")
    assert result["found"] and "Never write source files" in result["text"]
    missing = core.get_section(str(project), "CONTEXT.md", "nope")
    assert not missing["found"] and "Runtime" in [o["heading"] for o in missing["outline"]]


def test_tasks_merges_board_and_checklist(project: Path) -> None:
    view = core.tasks(str(project), status="open")
    assert view["checklist"]["Next"] == [{"done": False, "text": "Tune terrain", "source": "TASKS.md:5"}]
    assert core.tasks(str(project), status="in_progress")["board"][0]["id"] == "crown"


def test_audit_flags_memory_when_its_paths_change(project: Path) -> None:
    report = core.audit(str(project))
    stale = {m["path"]: m for m in report["stale"]}
    entry = stale[".agent/memories/codebase_insights/world-streaming.md"]
    assert entry["missing"] == ["src/gone/legacy.ts"] and entry["changed"] == []

    write(project, "src/world/stream.ts", "export const x = 2;\n")
    commit(project, "change stream")
    entry = {m["path"]: m for m in core.audit(str(project))["stale"]}[
        ".agent/memories/codebase_insights/world-streaming.md"
    ]
    assert entry["commits_since"] == 1 and entry["changed"] == ["src/world/stream.ts"]


def test_brief_respects_budget_and_names_omissions(project: Path) -> None:
    full = core.session_brief(str(project), budget_tokens=5000)
    assert full["omitted"] == []
    for expected in ("branch `main`", "`crown`", "Tune terrain", "2026-10-07", "Never write source files"):
        assert expected in full["brief"]
    assert "Done thing" not in full["brief"] and "Shipped thing" not in full["brief"]

    tight = core.session_brief(str(project), budget_tokens=120)
    assert tight["tokens"] <= 140 and tight["omitted"]
    assert "omitted for budget" in tight["brief"]


def test_brief_cites_latest_journal_when_no_checkpoints(project: Path) -> None:
    brief = core.session_brief(str(project), budget_tokens=5000)["brief"]
    assert "no checkpoints yet; latest journal entry (PROGRESS.md:3)" in brief


def test_index_cache_is_self_ignored(project: Path) -> None:
    core.session_brief(str(project))
    assert (project / ".agent/spine/.gitignore").read_text() == "*\n"
    from conftest import git
    assert ".agent/spine" not in git(project, "status", "--porcelain")


def _brief_in_subprocess(project: str) -> int:
    from spine_mcp import core as worker_core
    return worker_core.session_brief(project)["tokens"]


def test_concurrent_agents_share_one_index(project: Path) -> None:
    # Claude, Codex and Hermes starting together: cold index, parallel processes.
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_brief_in_subprocess, [str(project)] * 6))
    assert all(r > 0 for r in results) and len(set(results)) == 1
    with SpineIndex(project) as index:
        paths = [r["path"] for r in index.docs()]
        assert len(paths) == len(set(paths))
        assert index.refresh()["reindexed"] == 0


def test_mcp_tools_refuse_to_guess_the_project(monkeypatch: pytest.MonkeyPatch, project: Path) -> None:
    from spine_mcp import server
    monkeypatch.delenv("SPINE_PROJECT", raising=False)
    with pytest.raises(SpinePathError, match="pass project="):
        server._project(None)
    monkeypatch.setenv("SPINE_PROJECT", str(project))
    assert server._project(None) == str(project)
    assert "Spine brief" in server.brief_resource()


def _merge_branch(project: Path, branch: str, pr: int, title: str) -> None:
    from conftest import git
    git(project, "checkout", "-q", "-b", branch)
    write(project, f"src/{branch.replace('/', '_')}.ts", "x\n")
    commit(project, f"feat: {title}")
    git(project, "checkout", "-q", "main")
    git(project, "merge", "-q", "--no-ff", branch, "-m",
        f"Merge pull request #{pr} from Org/{branch}", "-m", title)


def test_brief_trusts_git_merges_over_stale_labels(project: Path) -> None:
    write(project, "PROGRESS.md",
          "# Progress\n\n## 2026-10-22 — final siege (agent/crown, in review)\n\n- crown\n\n"
          "## 2026-10-21 — armies (agent/armies-s4b, in review)\n\n- s4b\n")
    commit(project, "journal")
    _merge_branch(project, "agent/crown", 83, "The final siege and the crown")
    _merge_branch(project, "agent/armies-s4", 70, "Armies S4")
    brief = core.session_brief(str(project), budget_tokens=5000)["brief"]
    assert "#83 `agent/crown` — The final siege and the crown" in brief
    assert "final siege (agent/crown, in review) — merged #83" in brief
    # armies-s4 merged, armies-s4b did not: no prefix false positive.
    assert "(agent/armies-s4b, in review) (" in brief
    # The board's in_progress `crown` task is now flagged stale, not listed as open.
    assert "`crown` says in_progress but merged #83" in brief
    assert "[in_progress] `crown`" not in brief


def test_clip_sentence_keeps_whole_clauses() -> None:
    text = "Line of sight uses floor(y). A straight fix would make steps block sight; mobs would flicker."
    assert core.clip_sentence(text, 70) == "Line of sight uses floor(y). …"
    assert core.clip_sentence(text, 500) == text


def test_get_section_outline_grep_and_cap(project: Path) -> None:
    outline = core.get_section(str(project), "CONTEXT.md")["outline"]
    assert [o["heading"] for o in outline] == ["Context", "Runtime", "Non-negotiables"]
    hit = core.get_section(str(project), "CONTEXT.md", "Non-negotiables", grep="plain files")
    assert hit["text"] == "- Keep state in plain files"
    capped = core.get_section(str(project), "CONTEXT.md", "Non-negotiables", max_tokens=5)
    assert capped["truncated_from"] and "truncated" in capped["text"]


def test_git_children_never_inherit_stdin(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Under MCP, stdin is the JSON-RPC pipe; a git child reading it hung the server.
    import subprocess
    from spine_core import git as spine_git
    seen = {}
    real_run = subprocess.run

    def spy(*args, **kwargs):
        seen["stdin"] = kwargs.get("stdin", "inherited")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(spine_git.subprocess, "run", spy)
    assert spine_git.head(project)
    assert seen["stdin"] is subprocess.DEVNULL


def test_clip_rule_keeps_conventions_clipping_would_drop() -> None:
    rule = ("Use branch mode for implementation work: one integration feature branch plus one isolated "
            "worktree per task under `.worktrees/<task-id>` on `agent/<task-id>`.")
    out = core.clip_rule(rule, 64)
    assert out.endswith("[`.worktrees/<task-id>`, `agent/<task-id>`]")
    assert core.clip_rule("Short rule with `x_y`.", 64) == "Short rule with `x_y`."


def test_brief_has_verify_commands_adr_index_and_stale_plan_warning(project: Path) -> None:
    write(project, "AGENTS.md", "# Agents\n\n## Verification\n\n```bash\nnpm run typecheck && npm test\n# comment\n```\n")
    write(project, "docs/adr/0002-worktrees.md", "# ADR 0002: Worktree coordination\n\n## Status\n\nAccepted — 2026-09-05\n\n## Decision\n\nUse worktrees.\n")
    write(project, "docs/adr/0003-terrain.md", "# ADR-0003: Terrain v2\n\n## Status\n\n**Proposed — awaiting decision.**\n")
    commit(project, "docs")
    from test_core import _merge_branch  # noqa: F401  (same module; keeps the helper local)
    for n, name in enumerate(("a", "b", "c"), start=1):
        _merge_branch(project, f"agent/{name}", n, f"Feature {name}")
    brief = core.session_brief(str(project), budget_tokens=6000)["brief"]
    assert "- from AGENTS.md:3 (Verification)\n  - `npm run typecheck && npm test`" in brief
    assert "comment" not in brief.split("## verify", 1)[1].split("##", 1)[0]
    adrs = brief.split("## decisions (ADRs)", 1)[1]
    assert adrs.index("0003: Terrain v2 [Proposed]") < adrs.index("0002: Worktree coordination [Accepted]")
    assert "`TASKS.md` last changed" in brief and "3 merges ago" in brief
