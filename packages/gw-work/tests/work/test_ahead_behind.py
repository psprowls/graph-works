"""`run_work_ahead_behind`: live counts against the computed merge target, one row per placed item."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from gitrepo import git
from gw_kernel import apply_init, plan_init
from gw_kernel.workspace import provenance
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_pipeline.placement import branch_name
from gw_work.ahead_behind import AheadBehindRow, run_work_ahead_behind
from gw_work.commands import run_work_list


def _layout(tmp_path: Path, repos: tuple[str, ...]) -> WorkspaceLayout:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(root / ".works", today=date(2026, 9, 18), topic="Ahead")).layout
    head = layout.manifest_path.read_text(encoding="utf-8").split("repositories:")[0]
    entries = "".join(f'  "{name}":\n    path: "{(tmp_path / name).as_posix()}"\n' for name in repos)
    layout.manifest_path.write_text(f"{head}repositories:\n{entries}", encoding="utf-8", newline="\n")
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _item(layout: WorkspaceLayout, path: str, *, type_: str = "Feature", repo: str | None = "code") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    repo_line = f"repo: {repo}\n" if repo else ""
    page.write_text(
        f"---\ntype: {type_}\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: open\n"
        f"phase: design\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n{repo_line}"
        "affects:\n- src\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="\n",
    )


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    _commit(root, "base")
    return root


def _commit(root: Path, name: str) -> None:
    (root / f"{name}.txt").write_text(name, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", name)


def _rows(layout: WorkspaceLayout, **kwargs: str) -> tuple[AheadBehindRow, ...]:
    return run_work_ahead_behind(layout, run_work_list(layout), **kwargs)


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    _init_repo(tmp_path / "code")
    return _layout(tmp_path, ("code",))


def test_top_level_item_counts_against_the_default_branch(layout: WorkspaceLayout, tmp_path: Path) -> None:
    code = tmp_path / "code"
    _item(layout, "work/feature-x")
    branch = branch_name("work/feature-x", "Feature")
    git(code, "checkout", "-b", branch)
    _commit(code, "f1")
    _commit(code, "f2")
    git(code, "checkout", "main")
    _commit(code, "m1")
    assert _rows(layout) == (AheadBehindRow("work/feature-x", "code", branch, "main", 2, 1, "ok"),)


def test_child_of_an_epic_counts_against_the_epic_branch(layout: WorkspaceLayout, tmp_path: Path) -> None:
    code = tmp_path / "code"
    _item(layout, "work/epic-e", type_="Epic")
    _item(layout, "work/epic-e/children/feature-c")
    epic = branch_name("work/epic-e", "Epic")
    child = branch_name("work/epic-e/children/feature-c", "Feature")
    git(code, "branch", epic)
    git(code, "checkout", "-b", child, epic)
    _commit(code, "c1")
    git(code, "checkout", "main")
    rows = {row.path: row for row in _rows(layout)}
    row = rows["work/epic-e/children/feature-c"]
    assert row.merge_target == epic
    assert (row.ahead, row.behind, row.status) == (1, 0, "ok")


def test_unplaced_item_reports_branch_missing(layout: WorkspaceLayout) -> None:
    _item(layout, "work/feature-x")
    [row] = _rows(layout)
    assert (row.status, row.ahead, row.behind, row.merge_target) == ("branch-missing", None, None, "main")


def test_child_of_an_unplaced_epic_reports_target_missing(layout: WorkspaceLayout, tmp_path: Path) -> None:
    code = tmp_path / "code"
    _item(layout, "work/epic-e", type_="Epic")
    _item(layout, "work/epic-e/children/feature-c")
    git(code, "branch", branch_name("work/epic-e/children/feature-c", "Feature"))
    rows = {row.path: row for row in _rows(layout)}
    row = rows["work/epic-e/children/feature-c"]
    assert row.status == "target-missing"
    assert row.merge_target == branch_name("work/epic-e", "Epic")


def test_path_narrows_to_one_row_or_none(layout: WorkspaceLayout) -> None:
    _item(layout, "work/feature-x")
    _item(layout, "work/feature-y")
    _item(layout, "work/_archive/feature-old")
    assert [row.path for row in _rows(layout, path="work/feature-x")] == ["work/feature-x"]
    assert _rows(layout, path="work/nope") == ()
    assert _rows(layout, path="work/_archive/feature-old") == ()


def test_an_item_that_resolves_no_repository_is_omitted(tmp_path: Path) -> None:
    _init_repo(tmp_path / "code")
    _init_repo(tmp_path / "ui")
    layout = _layout(tmp_path, ("code", "ui"))
    _item(layout, "work/feature-x", repo="code")
    _item(layout, "work/feature-y", repo=None)
    assert [row.path for row in _rows(layout)] == ["work/feature-x"]


def test_no_declared_repository_means_no_rows(tmp_path: Path) -> None:
    layout = _layout(tmp_path, ())
    _item(layout, "work/feature-x", repo=None)
    assert _rows(layout) == ()


def test_a_repository_path_that_is_not_git_reports_branch_missing(tmp_path: Path) -> None:
    (tmp_path / "code").mkdir()
    layout = _layout(tmp_path, ("code",))
    _item(layout, "work/feature-x")
    assert [row.status for row in _rows(layout)] == ["branch-missing"]


def test_git_unavailable_is_reported_per_row(layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    _item(layout, "work/feature-x")
    monkeypatch.setattr(provenance, "local_branch_exists", lambda *args: None)
    [row] = _rows(layout)
    assert (row.status, row.ahead, row.behind) == ("git-unavailable", None, None)


def test_a_failed_count_is_git_unavailable(
    layout: WorkspaceLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _item(layout, "work/feature-x")
    git(tmp_path / "code", "branch", branch_name("work/feature-x", "Feature"))
    monkeypatch.setattr(provenance, "ahead_behind", lambda *args: None)
    assert [row.status for row in _rows(layout)] == ["git-unavailable"]


def test_git_unavailable_when_the_target_probe_fails(
    layout: WorkspaceLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _item(layout, "work/feature-x")
    git(tmp_path / "code", "branch", branch_name("work/feature-x", "Feature"))
    real = provenance.local_branch_exists
    monkeypatch.setattr(
        provenance, "local_branch_exists", lambda root, name: real(root, name) if name != "main" else None
    )
    assert [row.status for row in _rows(layout)] == ["git-unavailable"]


def test_default_base_is_resolved_once_per_repository(layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    _item(layout, "work/feature-x")
    _item(layout, "work/feature-y")
    calls: list[object] = []
    real = provenance.default_base
    monkeypatch.setattr(provenance, "default_base", lambda *args, **kwargs: calls.append(args) or real(*args, **kwargs))
    assert len(_rows(layout)) == 2
    assert len(calls) == 1
