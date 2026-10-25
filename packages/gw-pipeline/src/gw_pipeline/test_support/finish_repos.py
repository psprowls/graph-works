"""Shared test-only real-Git finish support for independent package suites.

Application modules never import this support. Consumers provide their own test
environment; no package suite or conftest is imported here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from gw_kernel.testing.transactions import git as _git
from gw_kernel.workspace.init import apply_init, plan_init
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.repos import ItemRepo

from gw_pipeline.finish import FinishTarget, VerifiedIntegration, receipt_entry_data

TODAY = date(2026, 9, 28)
OWNER = "work/feature-x"


def git(path: Path, *args: str) -> str:
    return _git(path, *args).strip()


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
    write(
        layout.manifest_path,
        f"version: 1\nworkflow_config: workflow.yaml\nrepositories:\n  code:\n    path: {repo}\n{finish_config}",
    )
    page = layout.bundle_dir / (OWNER + ".md")
    page.parent.mkdir(parents=True, exist_ok=True)
    write(
        page,
        "---\ntype: Feature\ntitle: Example feature\ndescription: d\nstatus: stable\n"
        f"work_status: {status}\nphase: {phase}\neffort: medium\nopened: 2026-09-28\nupdated: 2026-09-28\n"
        f"repo: code\nworktree: {source}\nbranch: feature\naffects: [src/a.py]\n---\n\nAuthored content.\n",
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
