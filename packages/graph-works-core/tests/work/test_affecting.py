"""`run_work_affecting`: directory overlap in both directions, one repository, active items only."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.work.affecting import run_work_affecting
from graph_works_core.workspace.layout import WorkspaceLayout


def _layout(tmp_path: Path, repos: tuple[str, ...]) -> WorkspaceLayout:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(root / ".works", today=date(2026, 9, 18), topic="Affecting")).layout
    head = layout.manifest_path.read_text(encoding="utf-8").split("repositories:")[0]
    entries = "".join(f'  "{name}":\n    path: "{(tmp_path / name).as_posix()}"\n' for name in repos)
    layout.manifest_path.write_text(f"{head}repositories:\n{entries}", encoding="utf-8", newline="\n")
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _item(
    layout: WorkspaceLayout, path: str, *, affects: list[str], repo: str | None, work_status: str = "open"
) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    repo_line = f"repo: {repo}\n" if repo else ""
    listed = "".join(f"- {entry}\n" for entry in affects)
    page.write_text(
        f"---\ntype: Feature\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: {work_status}\n"
        f"phase: design\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n{repo_line}"
        f"affects:\n{listed}---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="\n",
    )


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    return _layout(tmp_path, ("code", "ui"))


def test_affecting_matches_both_directions_in_one_repository(layout: WorkspaceLayout) -> None:
    _item(layout, "work/a", affects=["packages/core"], repo="code")
    _item(layout, "work/b", affects=["packages/core/src/x.py"], repo="code")
    _item(layout, "work/c", affects=["packages/core"], repo="ui")
    _item(layout, "work/d", affects=["packages/core"], repo="code", work_status="resolved")
    _item(layout, "work/e", affects=["packages/other"], repo="code")
    _item(layout, "work/f", affects=["packages/core"], repo=None)
    _item(layout, "work/_archive/g", affects=["packages/core"], repo="code")
    result = run_work_affecting(layout, "code", "packages/core/src")
    assert result.refusal is None
    assert [(r.item.path, r.matching) for r in result.rows] == [
        ("work/a", ("packages/core",)),
        ("work/b", ("packages/core/src/x.py",)),
    ]


def test_unknown_repository(layout: WorkspaceLayout) -> None:
    result = run_work_affecting(layout, "nope", "x")
    assert result.refusal == "unknown-repository"
    assert result.rows == ()


def test_sole_repository_is_the_fallback(tmp_path: Path) -> None:
    single = _layout(tmp_path, ("code",))
    _item(single, "work/a", affects=["src"], repo=None)
    assert [r.item.path for r in run_work_affecting(single, "code", "src/a.py").rows] == ["work/a"]
