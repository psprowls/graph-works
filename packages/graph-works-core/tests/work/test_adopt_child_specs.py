"""`run_adopt_child_specs`: compose the domain plan over a workspace layout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import graph_works_core
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands
from graph_works_core.work import commands as work
from graph_works_core.workspace.layout import WorkspaceLayout
from work_tracker_okf.adoption import AdoptionApplication

TODAY = date(2026, 8, 18)
EPIC = "2026-08-18-epic-parent"
CHILD = "2026-08-18-epic-feature-alpha-beta"


@dataclass
class CallCount:
    count: int = 0


def count_calls(monkeypatch, module, name: str) -> CallCount:
    original = getattr(module, name)
    counter = CallCount()

    def wrapped(*args, **kwargs):
        counter.count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapped)
    return counter


def adoption_workspace(tmp_path: Path) -> WorkspaceLayout:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    work_dir = layout.bundle_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    write_core_item(work_dir, EPIC, type="Epic", parent=None)
    write_core_item(work_dir, CHILD, type="Feature", parent=EPIC)
    draft = work_dir / EPIC / "references/child-specs/alpha-beta.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("# Design spec — alpha-beta\n", encoding="utf-8")
    return layout


def write_core_item(work_dir: Path, slug: str, *, type: str, parent: str | None) -> None:
    parent_line = f"parent: {parent}\n" if parent is not None else ""
    (work_dir / f"{slug}.md").write_text(
        "---\n"
        f"type: {type}\ntitle: {slug}\ndescription: d\nstatus: draft\nworkflow_status: open\n"
        f"{parent_line}opened: 2026-08-18\nupdated: 2026-08-18\n---\n",
        encoding="utf-8",
    )


def snapshot_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_adoption_command_and_result_stay_qualified_only() -> None:
    for name in ("AdoptChildSpecsResult", "run_adopt_child_specs"):
        assert name not in graph_works_core.__all__
        assert not hasattr(graph_works_core, name)


def test_core_adoption_loads_once_and_preserves_typed_groups(tmp_path, monkeypatch) -> None:
    layout = adoption_workspace(tmp_path)
    before = snapshot_bytes(layout.bundle_dir)
    loads = count_calls(monkeypatch, commands, "load_bundle")
    result = work.run_adopt_child_specs(layout, EPIC)
    move = result.plan.adopted[0]
    assert loads.count == 1
    assert result.plan.epic_slug == EPIC
    assert move.child_slug == CHILD
    assert move.draft == layout.bundle_dir / f"work/{EPIC}/references/child-specs/alpha-beta.md"
    assert move.destination == layout.bundle_dir / f"work/{CHILD}/references/01-design-spec.md"
    assert move.source_ref.resource == f"/work/{CHILD}/references/01-design-spec.md"
    assert result.application == AdoptionApplication()
    assert snapshot_bytes(layout.bundle_dir) == before


def test_core_adoption_apply_and_second_run_are_idempotent(tmp_path) -> None:
    layout = adoption_workspace(tmp_path)
    donor = layout.bundle_dir / f"work/{EPIC}/references/child-specs/alpha-beta.md"
    destination = layout.bundle_dir / f"work/{CHILD}/references/01-design-spec.md"
    first = work.run_adopt_child_specs(layout, EPIC, dry_run=False)
    second = work.run_adopt_child_specs(layout, EPIC, dry_run=False)
    assert first.application.moved == (destination,)
    assert first.application.registered == (CHILD,)
    assert destination.read_text(encoding="utf-8") == "# Design spec — alpha-beta\n"
    assert not donor.exists()
    assert second.plan.adopted == ()
    assert second.application == AdoptionApplication()
