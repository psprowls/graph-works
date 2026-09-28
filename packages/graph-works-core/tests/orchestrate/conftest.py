"""The gate-verb workspace: a real code repo, a manifest declaring it, one execute-phase item."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from _gate_helpers import TODAY
from _transaction_helpers import _init_git
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate.gate import runs_dir
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_ext.locking import locked

ITEM = "work/feature-a"
GATE = "    gate:\n      full: 'true'\n      scoped:\n        roots: packages/*\n        command: 'echo {name}'\n"


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def make_repo(path: Path) -> Path:
    (path / "packages/a").mkdir(parents=True)
    (path / "packages/a/x.py").write_text("x", encoding="utf-8", newline="\n")
    _init_git(path)
    return path


@dataclass
class GateEnv:
    layout: WorkspaceLayout
    root: Path
    repo: Path
    path: str
    spawned: list[Path] = field(default_factory=list)
    stack: ExitStack = field(default_factory=ExitStack)
    state: dict[str, object] = field(default_factory=dict)
    page_text: str = ""

    @property
    def tree(self) -> str:
        return git(self.repo, "rev-parse", "HEAD^{tree}")

    @property
    def spawn(self) -> Callable[[Path], None]:
        return self.spawned.append

    def set_manifest(self, *, gate: str) -> None:
        (self.root / "workspace.yaml").write_text(
            f"version: 1\nrepositories:\n  code:\n    path: ../code\n{gate}", encoding="utf-8", newline="\n"
        )

    def _write_page(self) -> None:
        lines = [
            "type: Feature",
            "title: A",
            f"work_status: {self.state['work_status']}",
            f"phase: {self.state['phase']}",
            "owner: someone",
            "repo: code",
        ]
        if self.state["worktree"] is not None:
            lines.append(f"worktree: {self.state['worktree']}")
        lines.append("affects:")
        lines.extend(f"  - {entry}" for entry in self.state["affects"])  # type: ignore[attr-defined]
        self.page_text = "---\n" + "\n".join(lines) + "\n---\n\nBody.\n"
        (self.layout.bundle_dir / f"{self.path}.md").write_text(self.page_text, encoding="utf-8", newline="\n")

    def set_affects(self, affects: list[str]) -> None:
        self.state["affects"] = affects
        self._write_page()

    def set_worktree(self, worktree: Path | None) -> None:
        self.state["worktree"] = None if worktree is None else str(worktree)
        self._write_page()

    def set_status(self, status: str) -> None:
        self.state["work_status"] = status
        self._write_page()

    def set_phase(self, phase: str) -> None:
        self.state["phase"] = phase
        self._write_page()

    def reset_to_execute(self) -> None:
        self.state.update(work_status="in-progress", phase="execute")
        self._write_page()

    def page_unchanged(self) -> bool:
        return (self.layout.bundle_dir / f"{self.path}.md").read_text(encoding="utf-8") == self.page_text

    def mark_runner_alive(self, run_id: str) -> None:
        self.stack.enter_context(locked(runs_dir(self.layout, self.path) / f"{run_id}.lock"))


@pytest.fixture
def env(tmp_path: Path) -> Iterator[GateEnv]:
    root = tmp_path / "workspace"
    root.mkdir()
    layout = apply_init(plan_init(root, today=TODAY, topic="Gate")).layout
    repo = make_repo(tmp_path / "code")
    made = GateEnv(layout, root, repo, ITEM)
    made.set_manifest(gate=GATE)
    made.state.update(work_status="in-progress", phase="execute", worktree=str(repo), affects=["packages/a"])
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    made._write_page()
    yield made
    made.stack.close()
