"""Real single-repository finish fixtures shared by the strategy test modules.

A plain module, not a conftest: only test modules in this directory import it
(pytest's prepend mode puts only a test module's own directory on sys.path).
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.finish import FinishTarget, VerifiedIntegration, receipt_entry_data
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import ItemRepo

TODAY = date(2026, 9, 28)
OWNER = "work/feature-x"


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def commit_file(checkout: Path, name: str, text: str, message: str) -> str:
    write(checkout / name, text)
    git(checkout, "add", name)
    git(checkout, "commit", "-m", message)
    return git(checkout, "rev-parse", "HEAD")


def code_repo(tmp_path: Path) -> tuple[Path, Path]:
    """`code` on main with a.txt; `code-source` on feature adding b.txt."""
    repo = tmp_path / "code"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.hooksPath", str(tmp_path / "no-hooks")),
    ):
        git(repo, "config", key, value)
    commit_file(repo, "a.txt", "base\n", "base")
    source = tmp_path / "code-source"
    git(repo, "worktree", "add", "-b", "feature", str(source))
    commit_file(source, "b.txt", "feature\n", "source")
    return repo, source


def squash(repo: Path) -> tuple[str, str]:
    """Squash-merge feature into main by hand; returns (target_before, squash commit)."""
    before = git(repo, "rev-parse", "HEAD")
    git(repo, "merge", "--squash", "feature")
    git(repo, "commit", "-m", "squash feature")
    return before, git(repo, "rev-parse", "HEAD")


def target(repo: Path, source: Path) -> FinishTarget:
    return FinishTarget(
        ItemRepo("code", repo, "frontmatter"), str(source.resolve()), "feature", "main", str(repo.resolve())
    )


def workspace(
    tmp_path: Path,
    repo: Path,
    source: Path,
    *,
    finish_config: str = "",
    phase: str = "finish",
    status: str = "in-progress",
) -> WorkspaceLayout:
    """A workspace declaring `code`, owning OWNER stamped on the source worktree.

    *finish_config* is appended under `repositories.code`, e.g.
    `"    finish:\\n      strategy: merge\\n"`.
    """
    root = tmp_path / "ws"
    root.mkdir()
    layout = apply_init(plan_init(root, today=TODAY, topic="Integrate")).layout
    write(layout.manifest_path, f"version: 1\nrepositories:\n  code:\n    path: {repo}\n{finish_config}")
    page = layout.bundle_dir / (OWNER + ".md")
    page.parent.mkdir(parents=True, exist_ok=True)
    write(
        page,
        "---\ntype: Feature\ntitle: Example feature\ndescription: d\nstatus: stable\n"
        f"work_status: {status}\nphase: {phase}\neffort: medium\nopened: 2026-09-28\nupdated: 2026-09-28\n"
        f"repo: code\nworktree: {source}\nbranch: feature\naffects: []\n---\n\nAuthored content.\n",
    )
    return layout


def write_receipt(layout: WorkspaceLayout, entries: list[VerifiedIntegration]) -> Path:
    lines: list[str] = []
    for entry in entries:
        for index, (key, value) in enumerate(receipt_entry_data(entry).items()):
            lines.append(f"  {'- ' if index == 0 else '  '}{key}: {value}")
    path = layout.bundle_dir / OWNER / "references/04-finish-receipt.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    write(
        path,
        f"---\ntype: Explanation\nreceipt_version: 2\nowner: {OWNER}\nintegrations:\n" + "\n".join(lines) + "\n---\n",
    )
    return path
