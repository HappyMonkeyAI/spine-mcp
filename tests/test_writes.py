from pathlib import Path

import pytest

from conftest import commit, git, write
from spine_core.markdown import split_frontmatter
from spine_core.resolve import SpinePathError
from spine_core.scrub import scrub
from spine_mcp import core, writes


def _read(project: Path, rel: str) -> str:
    return (project / rel).read_text(encoding="utf-8")


def test_checkpoint_writes_anchored_file_and_leads_the_brief(project: Path) -> None:
    result = writes.checkpoint(
        str(project), "codex", "Crown review fixes applied",
        done=["set lookup for contenders"], next=["Chaos Sessions R4b"], blockers=["none"],
        paths=["src/world/stream.ts"], verification={"V0": "pass", "V3": "skip"}, task_id="crown",
    )
    rel = result["written"]
    assert rel.startswith(".agent/memories/history/") and rel.endswith("-crown-review-fixes-applied.md")
    meta, body, _ = split_frontmatter(_read(project, rel))
    assert meta["agent"] == "codex" and meta["branch"] == "main" and meta["task_id"] == "crown"
    assert meta["sha"] == git(project, "rev-parse", "--short", "HEAD")
    assert meta["paths"] == ["src/world/stream.ts"] and meta["verification"] == "{V0: pass, V3: skip}"
    assert "## Next\n\n- Chaos Sessions R4b" in body

    brief = core.session_brief(str(project), budget_tokens=5000)["brief"]
    assert f"`{rel}` by codex on `main`" in brief and "Chaos Sessions R4b" in brief
    assert "Since then" not in brief

    commit(project, "checkpoint")
    write(project, "src/world/stream.ts", "export const x = 3;\n")
    commit(project, "feat: after the checkpoint")
    brief = core.session_brief(str(project), budget_tokens=5000)["brief"]
    assert "**Since then:** 2 commits without a checkpoint" in brief
    assert "feat: after the checkpoint" in brief


def test_checkpoints_never_overwrite(project: Path) -> None:
    first = writes.checkpoint(str(project), "grok", "same summary")["written"]
    second = writes.checkpoint(str(project), "grok", "same summary")["written"]
    assert first != second and second.endswith("-1.md")


def test_parallel_worktree_checkpoints_merge_without_conflict(project: Path, tmp_path: Path) -> None:
    trees = {}
    for name in ("a", "b"):
        tree = tmp_path / f"wt-{name}"
        git(project, "worktree", "add", "-q", "-b", f"agent/{name}", str(tree))
        trees[name] = tree
        writes.checkpoint(str(tree), "hermes" if name == "a" else "codex", f"work on {name}")
        commit(tree, f"checkpoint {name}")
    git(project, "merge", "-q", "--no-ff", "agent/a", "-m", "merge a")
    git(project, "merge", "-q", "--no-ff", "agent/b", "-m", "merge b")  # raises on conflict
    assert len(list((project / ".agent/memories/history").glob("*.md"))) == 2


@pytest.mark.parametrize("bad", [
    {"agent": ""},
    {"agent": "has\nnewline"},
    {"summary": "  "},
    {"paths": ["../outside.ts"]},
    {"paths": ["a,b.ts"]},
    {"verification": {"V9": "pass"}},
    {"verification": {"V0": "maybe"}},
])
def test_checkpoint_rejects_bad_input(project: Path, bad: dict) -> None:
    args = {"agent": "codex", "summary": "ok", **bad}
    with pytest.raises((writes.SpineWriteError, SpinePathError)):
        writes.checkpoint(str(project), **args)
    assert not (project / ".agent/memories/history").exists()


def test_read_only_host_refuses_writes(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_READONLY", "1")
    with pytest.raises(writes.SpineWriteError, match="read-only"):
        writes.checkpoint(str(project), "codex", "x")
    with pytest.raises(writes.SpineWriteError, match="read-only"):
        writes.record_memory(str(project), "codex", "insight", "t", "b")


FAKE_SECRETS = [
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "ghp_" + "a" * 36,
    "sk-ant-" + "b" * 30,
    "xoxb-" + "1234567890-abcdef",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
]


@pytest.mark.parametrize("secret", FAKE_SECRETS)
def test_scrub_catches_seeded_secrets(project: Path, secret: str) -> None:
    result = writes.checkpoint(str(project), "codex", "deploy notes", done=[f"used {secret} to deploy"])
    text = _read(project, result["written"])
    assert secret not in text and "[REDACTED]" in text and result["redactions"] >= 1


def test_scrub_keeps_key_names_and_ordinary_text() -> None:
    text, n = scrub("set DATABASE_PASSWORD=hunter2hunter2 and api_key: 'abcdefgh12345' then build tokens(text)")
    assert n == 2
    assert text == "set DATABASE_PASSWORD=[REDACTED] and api_key: [REDACTED] then build tokens(text)"
    assert scrub("budget_tokens = 2000; the token budget is small")[1] == 0


def test_record_memory_dedupe_gate(project: Path) -> None:
    first = writes.record_memory(
        str(project), "claude-code", "insight", "Chunk streaming radius",
        "View radius comes from the graphics preset, not WORLD.loadRadius.", paths=["src/world/stream.ts"],
    )
    rel = first["written"]
    assert rel == ".agent/memories/codebase_insights/chunk-streaming-radius.md"

    dup = writes.record_memory(
        str(project), "codex", "insight", "Chunk streaming radius rules",
        "The view radius comes from the graphics preset.",
    )
    assert dup["written"] is None and dup["candidates"][0]["update_of"] == rel

    updated = writes.record_memory(
        str(project), "codex", "insight", "Chunk streaming radius",
        "View radius comes from render/quality.ts presets.", update_of=rel,
    )
    meta, body, _ = split_frontmatter(_read(project, rel))
    assert updated["updated"] and meta["agent"] == "codex" and "render/quality.ts" in body
    assert meta["created"] == meta["updated"]  # created preserved (same day here)

    forced = writes.record_memory(
        str(project), "codex", "insight", "Chunk streaming radius",
        "A genuinely different fact about streaming.", force_new=True,
    )
    assert forced["written"].endswith("chunk-streaming-radius-2.md")


def test_record_memory_rejects_update_outside_its_folder(project: Path) -> None:
    with pytest.raises(writes.SpineWriteError):
        writes.record_memory(str(project), "codex", "insight", "t", "b", update_of="README.md")


def test_lessons_append_and_update_in_place(project: Path) -> None:
    writes.record_memory(str(project), "hermes", "lesson", "Rejections must be visible",
                         "Show server rejections on the open panel, not only in chat.")
    writes.record_memory(str(project), "codex", "lesson", "Eye line effects",
                         "Keep first-person attack arcs at or below the eye line.", force_new=True)
    text = _read(project, ".agent/memories/patterns_and_lessons.md")
    assert text.startswith("# Patterns and lessons — proj")
    assert text.index("## Rejections must be visible") < text.index("## Eye line effects")

    writes.record_memory(str(project), "codex", "lesson", "Rejections must be visible",
                         "Show rejections where the player is looking.",
                         update_of=".agent/memories/patterns_and_lessons.md#Rejections must be visible")
    text = _read(project, ".agent/memories/patterns_and_lessons.md")
    assert "where the player is looking" in text and "not only in chat" not in text
    assert text.count("## Rejections must be visible") == 1 and "## Eye line effects" in text


def test_distill_pages_oldest_first_and_marker_advances(project: Path) -> None:
    report = writes.distill(str(project))
    assert report["since"] is None and [e["date"] for e in report["entries"]] == ["2026-10-06", "2026-10-07"]
    assert {m["heading"] for m in report["memories"]} == {"World streaming"}

    # A fresh memory must not hide older undistilled entries (the old date heuristic did).
    writes.record_memory(str(project), "codex", "insight", "Identity resume", "Newest tab wins.")
    assert writes.distill(str(project))["pending_count"] == 2

    marked = writes.distill(str(project), done_through="2026-10-06")
    assert marked["written"] == ".agent/memories/.distilled-through"
    after = writes.distill(str(project))
    assert after["since"] == "2026-10-06" and [e["date"] for e in after["entries"]] == ["2026-10-07"]
    assert core.audit(str(project))["undistilled"] == {
        "count": 1, "distilled_through": "2026-10-06", "oldest": "2026-10-07"}

    with pytest.raises(writes.SpineWriteError, match="before the current marker"):
        writes.distill(str(project), done_through="2026-10-01")
    with pytest.raises(writes.SpineWriteError):
        writes.distill(str(project), done_through="last tuesday")


def test_nudge_fires_once_only_when_work_is_unrecorded(project: Path) -> None:
    writes.start_session(str(project), "sess-1")
    assert writes.nudge(str(project), "sess-1") is None  # nothing changed

    write(project, "src/world/stream.ts", "export const x = 9;\n")
    assert writes.nudge(str(project), "sess-1", stop_hook_active=True) is None
    reason = writes.nudge(str(project), "sess-1")
    assert reason and "checkpoint" in reason
    assert writes.nudge(str(project), "sess-1") is None  # once per session

    writes.start_session(str(project), "sess-2")
    writes.checkpoint(str(project), "claude-code", "recorded")
    assert writes.nudge(str(project), "sess-2") is None  # a checkpoint was written
    assert writes.nudge(str(project), "unknown-session") is None


def test_cli_hook_round_trip(project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    import io
    import json
    from spine_mcp import cli

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "s9"})))
    assert cli.main(["--project", str(project), "brief", "--hook"]) == 0
    assert "Spine brief" in capsys.readouterr().out

    write(project, "src/world/stream.ts", "export const x = 7;\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "s9", "stop_hook_active": False})))
    assert cli.main(["--project", str(project), "nudge"]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert cli.main(["--project", str(project), "nudge"]) == 0
    assert capsys.readouterr().out == ""
