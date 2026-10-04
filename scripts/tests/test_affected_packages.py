"""Acceptance tests for `scripts/affected_packages.py`.

Run with `uv run pytest scripts/tests`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from affected_packages import (  # noqa: E402
    changed_paths,
    dependency_graph,
    main,
    render,
    reverse_closure,
    select,
)
from package_units import UNITS  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
GRAPH = dependency_graph(REPO)
DIRS = {p.parent.name for p in (REPO / "packages").glob("*/pyproject.toml")}
ALL = {u.name for u in UNITS}


def _sel(*paths: str):
    return select(list(paths), GRAPH, DIRS)


def test_graph_keys_are_units_and_okf_ext_folds() -> None:
    assert set(GRAPH) == ALL
    assert "okf-ext" not in {d for deps in GRAPH.values() for d in deps}
    assert "okf-io" in GRAPH["code-wiki-okf"]
    assert "okf-io" not in GRAPH["okf-io"]


def test_okf_io_closure_is_every_declared_dependent() -> None:
    assert reverse_closure(GRAPH, {"okf-io"}) == {
        "okf-io", "code-wiki-okf", "doc-wiki-okf", "work-tracker-okf", "repositories-okf",
        "graph-works-core", "graph-works-wire", "graph-works-cli", "graph-works-serve",
    }


def test_test_only_dependencies_widen_the_closure() -> None:
    assert "graph-works-serve" in reverse_closure(GRAPH, {"graph-works-cli"})


def test_stdout_has_no_carriage_returns() -> None:
    done = subprocess.run([sys.executable, str(REPO / "scripts" / "affected_packages.py")], cwd=REPO, capture_output=True)
    assert done.returncode == 0
    assert b"\r" not in done.stdout


def test_subagents_io_closure() -> None:
    assert reverse_closure(GRAPH, {"subagents-io"}) == {
        "subagents-io", "workflow-local", "workflow-orca",
        "graph-works-core", "graph-works-wire", "graph-works-cli", "graph-works-serve",
    }


def test_leaf_package_selects_itself() -> None:
    sel = _sel("packages/graph-works-serve/src/graph_works_serve/app.py")
    assert (sel.mode, sel.plugin, sel.units) == ("packages", False, ("graph-works-serve",))


def test_okf_ext_path_maps_to_okf_io_unit() -> None:
    sel = _sel("packages/okf-ext/src/okf_ext/x.py")
    assert "okf-io" in sel.units and "okf-ext" not in sel.units


def test_plugin_path_sets_plugin_only() -> None:
    sel = _sel("plugins/gw/skills/workflow/SKILL.md")
    assert (sel.mode, sel.plugin, sel.units) == ("repo-only", True, ())


def test_root_markdown_is_repo_only() -> None:
    sel = _sel("AGENTS.md", "docs/notes.md")
    assert (sel.mode, sel.plugin, sel.units) == ("repo-only", False, ())


@pytest.mark.parametrize("path", ["justfile", "scripts/check_line_endings.py", "pyproject.toml", "uv.lock", ".github/workflows/ci.yml", "packages/README"])
def test_other_paths_fall_back_to_full(path: str) -> None:
    assert _sel(path).mode == "full"


def test_unknown_package_dir_is_full() -> None:
    assert _sel("packages/deleted-pkg/src/x.py").mode == "full"


def test_mixed_classes_resolve_to_heaviest() -> None:
    assert _sel("AGENTS.md", "packages/config-io/src/x.py").mode == "packages"
    assert _sel("packages/config-io/src/x.py", "justfile").mode == "full"


def test_no_changes_is_none() -> None:
    assert _sel().mode == "none"


def test_render_shape() -> None:
    out = render(_sel("packages/config-io/src/x.py", "plugins/gw/x.sh"))
    lines = out.splitlines()
    assert lines[0] == "mode: packages"
    assert lines[1] == "plugin: yes"
    assert [line for line in lines if line.startswith("unit: ")] == [f"unit: {u}" for u in sorted(reverse_closure(GRAPH, {"config-io"}))]
    assert any(line.startswith("reason: packages/config-io/src/x.py -> config-io") for line in lines)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "packages/config-io").mkdir(parents=True)
    (root / "packages/config-io/a.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    (root / "packages/okf-io").mkdir(parents=True)
    (root / "packages/okf-io/b.py").write_text("y = 1\n", encoding="utf-8", newline="\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "feature")
    return root


def test_changed_paths_merges_committed_worktree_and_untracked(repo: Path) -> None:
    (repo / "packages/config-io/a.py").write_text("x = 2\n", encoding="utf-8", newline="\n")
    _git(repo, "commit", "-qam", "edit")
    (repo / "packages/okf-io/b.py").write_text("y = 2\n", encoding="utf-8", newline="\n")
    (repo / "new.md").write_text("hi\n", encoding="utf-8", newline="\n")
    assert sorted(changed_paths(repo, None)) == ["new.md", "packages/config-io/a.py", "packages/okf-io/b.py"]


def test_rename_counts_both_sides(repo: Path) -> None:
    _git(repo, "mv", "packages/config-io/a.py", "packages/okf-io/a.py")
    _git(repo, "commit", "-qm", "move")
    assert sorted(changed_paths(repo, None)) == ["packages/config-io/a.py", "packages/okf-io/a.py"]


def test_explicit_base_overrides_merge_base(repo: Path) -> None:
    (repo / "c.md").write_text("c\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "c.md")
    _git(repo, "commit", "-qm", "c")
    assert changed_paths(repo, "HEAD") == []


def test_missing_main_is_a_clear_error(repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    _git(repo, "branch", "-q", "-m", "main", "trunk")
    monkeypatch.chdir(repo)
    assert main([]) == 2
    assert "--base" in capsys.readouterr().err
