import subprocess
from pathlib import Path

import pytest


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "--short", "HEAD")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "core.autocrlf", "false")
    write(root, "README.md", "# Proj\n\nA test project.\n")
    write(
        root,
        "CONTEXT.md",
        "# Context\n\n## Runtime\n\nPython.\n\n## Non-negotiables\n\n- Never write source files\n- Keep state in plain files\n",
    )
    write(
        root,
        "PROGRESS.md",
        "# Progress\n\n## 2026-10-07 — rejoin fix\n\n- identity resume works\n\n"
        "## 2026-10-06 — stuck keys\n\n- held keys released on blur\n",
    )
    write(root, "TASKS.md", "# TASKS\n\n## Next\n\n- [ ] Tune terrain\n- [x] Repair smoke\n")
    write(
        root,
        ".agent-tasks.json",
        '{"tasks": [{"id": "crown", "title": "Final siege", "status": "in_progress", "branch": "agent/crown"},'
        ' {"id": "old", "title": "Done thing", "status": "complete"},'
        ' {"id": "older", "title": "Shipped thing", "status": "completed"}]}',
    )
    write(root, "src/world/stream.ts", "export const x = 1;\n")
    write(
        root,
        ".agent/memories/codebase_insights/world-streaming.md",
        "# World streaming\n\n- Chunks stream from `src/world/stream.ts`.\n- Old file `src/gone/legacy.ts` was removed.\n",
    )
    commit(root, "init")
    return root
