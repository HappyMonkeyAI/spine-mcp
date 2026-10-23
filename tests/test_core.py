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
    assert not missing["found"] and "Runtime" in missing["headings"]


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
