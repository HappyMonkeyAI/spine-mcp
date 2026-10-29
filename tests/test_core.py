import json
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
    assert "_for budget, omitted: " in tight["brief"]


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
    assert adrs.index("0003: Terrain v2 [Proposed — awaiting decision]") < adrs.index("0002: Worktree coordination [Accepted]")
    assert "`TASKS.md` last changed" in brief and "3 merges ago" in brief


def test_brief_generalises_to_differently_shaped_repos(project: Path) -> None:
    # A repo shaped like xeno-gate-tactical: direct commits on main, lowercase tasks.md,
    # undated PROGRESS.md, rules under other headings, "- **Status:**" ADRs, extra root docs.
    from conftest import git
    (project / "TASKS.md").unlink()
    write(project, "tasks.md", "# tasks\n\n## Next\n\n- [ ] Wire the Intel wing\n")
    write(project, "PROGRESS.md", "# Progress\n\nUpdated: 2026-10-01\n\n## Standing goal\n\nShip it.\n\n"
                                  "## Next concrete step\n\nConnect the Gate Analysis wing.\n")
    write(project, "CONTEXT.md", "# Context\n\n## Architectural rules\n\n- Keep geometry in `packages/geometry-kernel`.\n\n"
                                 "## Current state\n\nPublic alpha. The editor works.\n")
    write(project, "ROADMAP.md", "# Roadmap\n\n## Later\n\nMultiplayer lobbies.\n")
    write(project, "docs/adr/0003-los.md", "# ADR 0003: Line of sight\n\n- **Status:** Accepted\n- **Date:** 2026-08-21\n\n## Decision\n\nWalls block.\n")
    commit(project, "refactor: extract the turn flow")
    brief = core.session_brief(str(project), budget_tokens=6000)["brief"]

    history = brief.split("## recent history", 1)[1].split("\n## ", 1)[0]
    assert "refactor: extract the turn flow" in history  # direct commits, not only merges
    assert "## current state\n- (CONTEXT.md:" in brief and "Public alpha" in brief
    assert "Wire the Intel wing (tasks.md:5)" in brief
    assert "`PROGRESS.md` (updated 2026-10-01): Standing goal · Next concrete step" in brief
    assert "Connect the Gate Analysis wing" in brief
    assert "**Architectural rules**" in brief and "`packages/geometry-kernel`" in brief
    assert "0003: Line of sight [Accepted]" in brief
    assert core.recall(str(project), "multiplayer lobbies")["results"][0]["source"].startswith("ROADMAP.md")


def test_brief_prefers_breadth_compact_forms_before_dropping(project: Path) -> None:
    rules = "".join(f"- Rule number {i} says something long enough to need clipping in the brief today.\n" for i in range(20))
    write(project, "CONTEXT.md", f"# Context\n\n## Non-negotiables\n\n{rules}")
    for n in range(1, 9):
        write(project, f"docs/adr/000{n}-x.md", f"# ADR 000{n}: Decision {n}\n\n## Status\n\nAccepted\n\n## Decision\n\n"
                                               f"Use approach {n} because of reason {n} which is explained at length here.\n")
    commit(project, "many rules and adrs")
    full = core.session_brief(str(project), budget_tokens=6000)
    assert not full["shortened"] and "because of reason 8" in full["brief"]
    tight_budget = full["tokens"] - 150
    tight = core.session_brief(str(project), budget_tokens=tight_budget)
    assert "decisions (ADRs)" not in tight["omitted"] and tight["shortened"]
    assert "## decisions (ADRs)" in tight["brief"] and tight["tokens"] <= tight_budget


def test_adr_drift_warning_and_qualified_status(project: Path) -> None:
    write(project, "docs/adr/0001-plan.md", "# ADR 0001: Plan\n\n- **Status:** Accepted (in progress: packets P2 onward)\n\n## Decision\n\n| a | b |\n|---|---|\n\nState is plain data.\n")
    commit(project, "adr")
    for i in range(5):
        write(project, "src/world/stream.ts", f"export const x = {i + 10};\n")
        commit(project, f"work {i}")
    brief = core.session_brief(str(project), budget_tokens=6000)["brief"]
    assert "0001: Plan [Accepted (in progress: packets P2 onward)]: State is plain data." in brief
    assert '`docs/adr/0001-plan.md` status "Accepted (in progress: packets P2 onward)" unchanged for 5 commits' in brief


def test_section_adr_shorthand_whole_file_grep_and_recall_path(project: Path) -> None:
    write(project, "docs/adr/0007-authority.md", "# ADR 0007: Authority\n\n## Decision\n\nProgress owns resources.\n")
    write(project, "apps/game/docs/smoke-test.md", "# Smoke\n\n## Known limits\n\nRight-click cancels the path preview instead of facing.\n")
    write(project, "apps/game/package.json", '{"scripts": {"dev": "vite", "typecheck": "tsc", "smoke": "node smoke.mjs"}}')
    commit(project, "app docs")
    assert core.get_section(str(project), "adr:7", "Decision")["text"].endswith("Progress owns resources.")
    assert core.get_section(str(project), "adr:9", "Decision")["found"] is False
    hits = core.get_section(str(project), "PROGRESS.md", grep="held keys")
    assert hits["matches"] == 1 and "[PROGRESS.md:7 2026-10-06" in hits["text"]
    found = core.recall(str(project), "right-click preview", path="apps/game")["results"]
    assert [r["source"] for r in found] == ["apps/game/docs/smoke-test.md:5"]
    assert core.recall(str(project), "right-click preview", path="src")["results"] == []
    assert "- `apps/game` package scripts: typecheck, smoke" in core.session_brief(str(project), budget_tokens=6000)["brief"]


def test_adr_status_is_its_first_line_not_its_history(project: Path) -> None:
    write(project, "docs/adr/0001-terrain.md", "# ADR-0001: Terrain\n\n## Status\n\n**Accepted — option 1, 2026-10-16.**\n\n"
                                               "_Previously: Proposed — awaiting decision._\n")
    commit(project, "adr")
    for i in range(5):
        write(project, "src/world/stream.ts", f"export const x = {i + 20};\n")
        commit(project, f"work {i}")
    brief = core.session_brief(str(project), budget_tokens=6000)["brief"]
    assert "0001: Terrain [Accepted — option 1, 2026-10-16]" in brief
    assert "unchanged for" not in brief


@pytest.mark.parametrize("budget", [120, 300, 600, 1200])
def test_brief_never_exceeds_its_budget(project: Path, budget: int) -> None:
    assert core.session_brief(str(project), budget_tokens=budget)["tokens"] <= budget


def test_registry_lookup_matches_launcher_registry_shape(project: Path, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    # Shape written by launcher-project-registry's registry_ops.upsert_project (schema v1.1).
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"projects": [
        {"slug": "other", "name": "Other", "path": str(tmp_path / "missing")},
        {"slug": "mrpg-realms", "name": "MRPG Realms", "path": str(project), "transport": "http",
         "catalogue_name": "mrpg_realms", "has_context_md": True, "adr_count": 16},
    ]}), encoding="utf-8")
    monkeypatch.setenv("SPINE_REGISTRY", str(registry))
    from spine_core.resolve import resolve_project
    for spelling in ("MRPGRealms", "mrpg-realms", "MRPG Realms", "mrpg_realms"):
        assert resolve_project(spelling) == project.resolve()
    with pytest.raises(SpinePathError, match="not found"):
        resolve_project("other")  # registered, but its path doesn't exist here
    assert "Spine brief" in core.session_brief("MRPGRealms")["brief"]


def test_registry_lookup_supports_utf8_bom(project: Path, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    registry = tmp_path / "registry_bom.json"
    content = json.dumps({"projects": [
        {"slug": "mrpg-realms", "name": "MRPG Realms", "path": str(project)}
    ]})
    registry.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    monkeypatch.setenv("SPINE_REGISTRY", str(registry))
    from spine_core.resolve import resolve_project
    assert resolve_project("MRPGRealms") == project.resolve()


def test_registry_lookup_uses_default_spine_path_when_env_unset(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3: default registry paths are consulted when SPINE_REGISTRY is unset."""
    monkeypatch.delenv("SPINE_REGISTRY", raising=False)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"projects": [
        {"slug": "mrpg-realms", "name": "MRPG Realms", "path": str(project)}
    ]}), encoding="utf-8")
    import spine_core.resolve as resolve_mod
    monkeypatch.setattr(resolve_mod, "_default_registry_paths", lambda: [registry])
    assert resolve_mod.resolve_project("MRPGRealms") == project.resolve()


def test_adr_brief_includes_reason_and_limitation(project: Path) -> None:
    write(project, "docs/adr/0010-los.md",
          "# ADR 0010: Line of sight\n\n## Status\n\nAccepted\n\n"
          "## Decision\n\nWalls block line of sight.\n\n"
          "## Rationale\n\nTo prevent see-through walls.\n\n"
          "## Limitations\n\nOnly straight wall segments are supported.\n")
    commit(project, "adr 10")
    brief = core.session_brief(str(project), budget_tokens=6000)["brief"]
    assert "0010: Line of sight [Accepted]: Walls block line of sight." in brief
    assert "(reason: To prevent see-through walls.)" in brief
    assert "(limit: Only straight wall segments are supported.)" in brief


def test_stale_shipped_next_items_flagged(project: Path) -> None:
    (project / "TASKS.md").unlink(missing_ok=True)
    write(project, "tasks.md", "# tasks\n\n## Next\n\n- [ ] citadel/keep\n- [ ] Unrelated feature\n")
    write(project, "PROGRESS.md", "# Progress\n\nUpdated: 2026-10-23\n\n## Next concrete step\n\nWire the Intel wing\n")
    commit(project, "setup tasks")
    _merge_branch(project, "agent/citadel-keep", 88, "add citadel/keep")
    write(project, "src/intel.ts", "x\n")
    commit(project, "feat: Wire the Intel wing")
    brief = core.session_brief(str(project), budget_tokens=6000)["brief"]
    assert "citadel/keep (tasks.md:5) — merged #88" in brief
    assert "Wire the Intel wing — shipped in" in brief
    assert "Unrelated feature" in brief and "— merged" not in brief.split("Unrelated feature")[1].split("\n")[0]


def test_rules_and_verify_preserve_conventions_gates_and_env_vars(project: Path) -> None:
    rule = "All unit tests must pass with >= 95% coverage threshold and V0=1 environment variable set."
    clipped = core.clip_rule(rule, 40)
    assert "V0=1" in clipped or "95%" in clipped

    write(project, "AGENTS.md", "# Agents\n\n## Verification\n\n- Run `V0=1 uv run pytest -q` for quick checks\n")
    commit(project, "agents verify")
    brief = core.session_brief(str(project), budget_tokens=6000)["brief"]
    assert "V0=1 uv run pytest -q" in brief


def test_adr_plan_completion_drift_warning(project: Path) -> None:
    write(
        project,
        "docs/plans/2026-10-02-decomp.md",
        "# Decomp\n\n**Status:** approved; P0 done; P1 done; P2 done; P8 done. "
        "The decomposition is complete. **Date:** 2026-10-02.\n\n## Packets\n\n| # | Packet |\n",
    )
    write(
        project,
        "docs/adr/0008-composition.md",
        "# ADR 0008: Composition root\n\n"
        "- **Status:** Accepted (in progress: decomposition packets P2 onward)\n"
        "- **Date:** 2026-10-02\n\n"
        "## Decision\n\nState is plain data. See `docs/plans/2026-10-02-decomp.md`.\n",
    )
    commit(project, "adr + complete plan")
    brief = core.session_brief(str(project), budget_tokens=6000)["brief"]
    assert "`docs/adr/0008-composition.md` status" in brief
    assert "docs/plans/2026-10-02-decomp.md` says the work is complete" in brief


def test_brief_surfaces_app_verify_and_known_issues(project: Path) -> None:
    write(
        project,
        "apps/xeno-tactical/docs/smoke-test.md",
        "# Smoke\n\n## Run\n\n```bash\ncd apps/xeno-tactical\nnpm run build\nnpm run smoke\n```\n\n"
        "## Known limits\n\n"
        "- Right-click-to-turn only works when no path preview is showing; existing behaviour.\n"
        "- Attack animation playback is not asserted.\n",
    )
    write(
        project,
        "apps/xeno-tactical/package.json",
        '{"scripts": {"build": "tsc", "smoke": "node scripts/smoke.mjs", "dev": "vite"}}',
    )
    write(
        project,
        "PROGRESS.md",
        "# Progress\n\nUpdated: 2026-10-23\n\n## Notes\n\n"
        "- Known: `scripts/smoke-persistence.mjs` fails in its write phase; not touched here.\n"
        "- Known: the default world file is launch-directory dependent; not changed here.\n"
        "- Shipped something unrelated.\n"
        "- Decomposition P2: extracted camera. Pre-existing, not changed: "
        "`dispose()` never removes the resize listener; worth fixing when the input packet touches it. "
        "Tests 24/24 pass. Next: P6.\n",
    )
    commit(project, "app smoke + known")
    brief = core.session_brief(str(project), budget_tokens=6000)["brief"]
    assert "- from `apps/xeno-tactical/docs/smoke-test.md` (Run)" in brief
    assert "`npm run smoke`" in brief
    assert "## known issues" in brief
    assert "Right-click-to-turn only works when no path preview" in brief
    assert "smoke-persistence.mjs` fails in its write phase; not touched here" in brief
    assert "Pre-existing, not changed: `dispose()` never removes the resize listener" in brief
