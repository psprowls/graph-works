"""Fixture, inventory reader and oracle for the end-to-end placement conformance suite.

The oracle derives the expected worktree and branch inventory from driver events alone
(which item was placed, removed or archived, in which repository), through
`gw_pipeline.placement.branch_name` and the layout's `worktrees_dir`. The inventory reader
observes the real one with git. `assert_conforms` holds the two equal after every step.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gw_cli.cli import app
from gw_kernel.testing.transactions import git, init_git
from gw_pipeline.placement import branch_name
from helpers import initialized_workspace
from typer.testing import CliRunner

REPOS = ("core", "ui")
EPIC_TITLE = "Conformance epic"
_PLACEMENT_KEYS = ("worktree", "branch", "repo_stamps")
_runner = CliRunner()


def item_type(path: str) -> str:
    """The work type a canonical path names through its basename prefix."""
    return "Epic" if path.rsplit("/", 1)[-1].startswith("epic-") else "Feature"


@dataclass(frozen=True)
class ChildSpec:
    key: str
    title: str
    repo: str
    affects: str


CHILDREN: tuple[ChildSpec, ...] = (
    ChildSpec("a", "Child A", "core", "packages/core-a"),
    ChildSpec("b", "Child B", "core", "packages/core-b"),
    ChildSpec("c", "Child C", "ui", "apps/ui"),
)


@dataclass
class Fixture:
    root: Path
    workspace: Path
    repos: dict[str, Path]
    worktrees_dir: Path
    epic: str | None = None
    epic_archived_path: str | None = None
    children: dict[str, str] = field(default_factory=dict)

    def repo_of(self, path: str) -> str:
        """The repository an item resolves to: the epic's own, or its child spec's."""
        for spec in CHILDREN:
            if self.children.get(spec.key) == path:
                return spec.repo
        return "core"


def build_fixture(root: Path) -> Fixture:
    """A bootstrapped git workspace declaring two real git repositories, `core` and `ui`."""
    workspace = initialized_workspace(root / "works")
    repos: dict[str, Path] = {}
    seeds = {"core": ("packages/core-a", "packages/core-b"), "ui": ("apps/ui",)}
    for name, dirs in seeds.items():
        repo = root / name
        for directory in dirs:
            (repo / directory).mkdir(parents=True)
            (repo / directory / "x.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
        repos[name] = init_git(repo)
    manifest = workspace / "workspace.yaml"
    text = manifest.read_text(encoding="utf-8")
    assert "repositories: {}\n" in text
    declared = "repositories:\n" + "".join(
        f"  {name}:\n    path: {json.dumps(str(path))}\n    gate: {{full: 'true'}}\n" for name, path in repos.items()
    )
    manifest.write_text(text.replace("repositories: {}\n", declared), encoding="utf-8", newline="\n")
    init_git(workspace)
    fx = Fixture(root=root, workspace=workspace, repos=repos, worktrees_dir=workspace / ".gw" / "worktrees")
    return fx


def gw(fx: Fixture, *args: str, ok: bool = True, cwd: Path | None = None) -> dict[str, Any]:
    """Invoke the CLI in-process with `--workspace` and `--json`; return the parsed stdout."""
    result = _runner.invoke(app, [*args, "--workspace", str(fx.workspace), "--json"])
    if ok:
        assert result.exit_code == 0, f"gw {' '.join(args)} exited {result.exit_code}:\n{result.output}"
    payload: dict[str, Any] = json.loads(result.stdout) if result.stdout.strip() else {}
    payload.setdefault("_exit", result.exit_code)
    return payload


@dataclass(frozen=True)
class Inventory:
    worktrees: frozenset[tuple[str, str]]
    branches: frozenset[str]


def observe(repo: Path) -> Inventory:
    """The real worktree and local-branch inventory of *repo*."""
    entries: set[tuple[str, str]] = set()
    path = ""
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = str(Path(line.removeprefix("worktree ")).resolve())
        elif line.startswith("branch "):
            entries.add((path, line.removeprefix("branch refs/heads/")))
        elif line == "detached":
            entries.add((path, "(detached)"))
    branches = frozenset(git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())
    return Inventory(frozenset(entries), branches)


class Oracle:
    """Expected inventory per repository, from driver events only."""

    def __init__(self, fx: Fixture) -> None:
        self._fx = fx
        self._placed: dict[str, dict[str, str]] = {repo: {} for repo in REPOS}
        self._removed: dict[str, set[str]] = {repo: set() for repo in REPOS}
        self._archived: set[str] = set()

    def place(self, path: str, repo: str) -> None:
        """*path* gets a worktree in *repo*, and so does every owner above it (epic §4.1)."""
        parts = path.split("/")
        for end in range(2, len(parts) + 1, 2):
            owner = "/".join(parts[:end])
            self._placed[repo][owner] = item_type(owner)
            self._removed[repo].discard(owner)

    def remove(self, path: str, repo: str) -> None:
        assert path in self._placed[repo], f"oracle: {path} was never placed in {repo}"
        self._removed[repo].add(path)

    def prune(self, root: str) -> None:
        """Prune over a finished subtree removes every worktree still placed under *root*."""
        for repo in REPOS:
            for path in self._placed[repo]:
                if path == root or path.startswith(f"{root}/"):
                    self._removed[repo].add(path)

    def archive(self, paths: Iterable[str]) -> None:
        self._archived.update(paths)

    def expected(self, repo: str) -> Inventory:
        checkout = self._fx.repos[repo].resolve()
        worktrees = {(str(checkout), "main")}
        branches = {"main"}
        for path, type_ in self._placed[repo].items():
            branch = branch_name(path, type_)
            if path in self._archived:
                continue
            branches.add(branch)
            if path not in self._removed[repo]:
                worktrees.add((str((self._fx.worktrees_dir / repo / branch).resolve()), branch))
        return Inventory(frozenset(worktrees), frozenset(branches))


def _placement_keys_in_pages(fx: Fixture) -> list[str]:
    found = []
    for page in sorted((fx.workspace / "okf" / "work").rglob("*.md")):
        text = page.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        frontmatter = text.split("---", 2)[1]
        for key in _PLACEMENT_KEYS:
            if re.search(rf"^{key}:", frontmatter, re.MULTILINE):
                found.append(f"{page.relative_to(fx.workspace)}: {key}")
    return found


def assert_conforms(fx: Fixture, oracle: Oracle, step: str) -> None:
    """The observed inventory equals the oracle's, and the standing invariants hold."""
    for repo in REPOS:
        observed, expected = observe(fx.repos[repo]), oracle.expected(repo)
        assert observed == expected, (
            f"[{step}] {repo} inventory drifted\n"
            f"  unexpected worktrees: {sorted(observed.worktrees - expected.worktrees)}\n"
            f"  missing worktrees: {sorted(expected.worktrees - observed.worktrees)}\n"
            f"  unexpected branches: {sorted(observed.branches - expected.branches)}\n"
            f"  missing branches: {sorted(expected.branches - observed.branches)}"
        )
        assert git(fx.repos[repo], "rev-parse", "--abbrev-ref", "HEAD").strip() == "main", (
            f"[{step}] {repo} checkout left main"
        )
        assert git(fx.repos[repo], "status", "--porcelain") == "", f"[{step}] {repo} main checkout is dirty"
    workspace = observe(fx.workspace)
    primary = str(fx.workspace.resolve())
    assert workspace.branches == frozenset({"main"}), f"[{step}] workspace branches: {sorted(workspace.branches)}"
    assert {p for p, _b in workspace.worktrees} == {primary}, (
        f"[{step}] workspace worktrees: {sorted(workspace.worktrees)}"
    )
    stored = _placement_keys_in_pages(fx)
    assert stored == [], f"[{step}] stored placement keys on work pages: {stored}"
    expected_roots = {path for repo in REPOS for path, _b in oracle.expected(repo).worktrees}
    if fx.worktrees_dir.exists():
        for marker in fx.worktrees_dir.rglob(".git"):
            assert str(marker.parent.resolve()) in expected_roots, f"[{step}] stray checkout at {marker.parent}"


def assert_refreshed(fx: Fixture, path: str, payload: dict[str, Any]) -> None:
    """D-003: after a place, each chain step's branch contains its parent branch."""
    for step in payload.get("chain", []):
        parent = step.get("parent_branch") or step.get("base_branch")
        if not parent or not step.get("branch"):
            continue
        repo = _repo_of(fx, step)
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", parent, step["branch"]], cwd=repo, capture_output=True
        )
        assert proc.returncode == 0, f"{path}: {step['branch']} does not contain {parent}"


def _repo_of(fx: Fixture, step: dict[str, Any]) -> Path:
    worktree = Path(step["worktree"]).resolve()
    for repo, checkout in fx.repos.items():
        if worktree.is_relative_to((fx.worktrees_dir / repo).resolve()):
            return checkout
    raise AssertionError(f"chain step outside any repository worktree dir: {worktree}")


def normalized(fx: Fixture) -> dict[str, str]:
    """Per-repository git state with machine-local noise removed, for cross-driver comparison (D-011)."""
    out: dict[str, str] = {}
    for repo, checkout in fx.repos.items():
        text = git(checkout, "worktree", "list", "--porcelain") + git(checkout, "branch")
        text = text.replace(str(fx.root.resolve()), "<root>").replace(str(fx.root), "<root>")
        out[repo] = re.sub(r"\b[0-9a-f]{40}\b", "<sha>", text)
        out[f"{repo}:tree"] = git(checkout, "rev-parse", "main^{tree}").strip()
    return out
