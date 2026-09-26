"""`run_next(guidance=)`: opt-in, usable-dispatch-only, selected-leaf, confined writes."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.guidance import assembly
from graph_works_core.work import commands as work
from graph_works_core.work.commands import GuidanceRequest
from okf_io import load_bundle

TODAY = date(2026, 9, 25)
EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/feature-a"
ANSWERED = "# Decisions\n\n## D-001 — Which?\nstatus: answered\n\n**Answer:** This one.\n"


def _layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Next")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write(layout, path: str, *, type: str = "Feature", phase: str = "design") -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\nwork_status: open\n"
        f"phase: {phase}\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
        "affects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="\n",
    )


def _ledger(layout, owner: str, text: str = ANSWERED) -> None:
    ledger = layout.bundle_dir / owner / "references" / "00-decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(text, encoding="utf-8", newline="\n")


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_no_request_assembles_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    _ledger(layout, CHILD)
    result = work.run_next(layout, CHILD, dry_run=False)
    assert result.guidance is None and result.guidance_file is None
    assert not (layout.bundle_dir / CHILD / "references" / "guidance-design.md").exists()


def test_auto_writes_only_the_guidance_file(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    _ledger(layout, CHILD)
    before = _files(layout.root)
    result = work.run_next(layout, CHILD, dry_run=False, guidance=GuidanceRequest("auto"))
    target = layout.bundle_dir / CHILD / "references" / "guidance-design.md"
    assert result.guidance is not None and result.guidance.phase == "design"
    assert [e.id for e in result.guidance.entries] == ["D-001"]
    assert result.guidance_file == target
    after = _files(layout.root)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    cache = layout.cache_dir.relative_to(layout.root).as_posix() + "/claims/"
    assert {k for k in changed if not k.startswith(cache)} == {target.relative_to(layout.root).as_posix()}


def test_none_target_returns_entries_and_writes_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    _ledger(layout, CHILD)
    result = work.run_next(layout, CHILD, dry_run=True, guidance=GuidanceRequest(None))
    assert result.guidance is not None and len(result.guidance.entries) == 1
    assert result.guidance_file is None
    assert not list((layout.bundle_dir / CHILD / "references").glob("guidance-*.md"))


def test_explicit_path_target(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    _ledger(layout, CHILD)
    target = tmp_path / "out" / "g.md"
    result = work.run_next(layout, CHILD, dry_run=False, guidance=GuidanceRequest(target))
    assert result.guidance_file == target and target.exists()


def test_zero_admitted_writes_no_file(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    result = work.run_next(layout, CHILD, dry_run=False, guidance=GuidanceRequest("auto"))
    assert result.guidance is not None and result.guidance.entries == ()
    assert result.guidance_file is None


def test_an_unwritable_target_is_a_warning(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    _ledger(layout, CHILD)
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8", newline="\n")
    result = work.run_next(layout, CHILD, dry_run=False, guidance=GuidanceRequest(blocker / "g.md"))
    assert result.guidance is not None and len(result.guidance.entries) == 1
    assert result.guidance_file is None
    assert any(w.startswith(f"guidance file not written: {blocker / 'g.md'}") for w in result.guidance.warnings)


def test_a_held_item_assembles_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD)
    _ledger(layout, CHILD, f"# Decisions\n\n## D-001 — question\nstatus: open\naffects: [{CHILD}]\n")
    result = work.run_next(layout, CHILD, guidance=GuidanceRequest("auto"))
    assert result.route.dispatch is None and result.guidance is None


def test_an_epic_waiting_on_children_assembles_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD)
    result = work.run_next(layout, EPIC, guidance=GuidanceRequest("auto"))
    assert result.guidance is None


def test_a_dispatch_preflight_assembles_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-a", phase="plan")
    (layout.root / "dispatch.yaml").write_text(
        "version: 1\npipeline:\n  rules:\n  - match: {}\n    colour: red\n", encoding="utf-8", newline="\n"
    )
    result = work.run_next(layout, "work/feature-a", guidance=GuidanceRequest("auto"))
    assert result.dispatch_preflight is not None and result.guidance is None


def test_descend_assembles_for_the_selected_leaf(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, EPIC, type="Epic", phase="execute")
    _write(layout, CHILD)
    _ledger(layout, CHILD)
    result = work.run_next(layout, EPIC, descend=True, dry_run=False, guidance=GuidanceRequest("auto"))
    assert result.selected_path == CHILD
    assert result.guidance_file == layout.bundle_dir / CHILD / "references" / "guidance-design.md"
    assert result.guidance is not None and result.guidance.entries[0].why == "own ledger"


def test_a_selected_path_absent_from_items_assembles_nothing(tmp_path: Path) -> None:
    # Unreachable through `run_next` (`_plan_route` and `state_for` both
    # guarantee the selected item exists); pins `_with_guidance`'s own guard.
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    _ledger(layout, CHILD)
    result = work.run_next(layout, CHILD)
    assert result.route.dispatch is not None and result.dispatch_resolution is not None
    bundle = load_bundle(layout.bundle_dir)
    assert work._with_guidance(layout, result, bundle, (), GuidanceRequest("auto")) is result


def _no_tokenizer(text: str) -> int:
    raise OSError("ProxyError: cannot fetch o200k_base")


def test_a_tokenizer_failure_is_a_warning_and_leaves_routing_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Design spec section 3.6: guidance never raises and never changes routing.
    layout = _layout(tmp_path)
    _write(layout, CHILD)
    _ledger(layout, CHILD)
    baseline = work.run_next(layout, CHILD)
    monkeypatch.setattr(assembly, "count_tokens", _no_tokenizer)
    result = work.run_next(layout, CHILD, guidance=GuidanceRequest("auto"))
    assert result.guidance is not None
    assert result.guidance.entries == () and result.guidance.rendered == "" and result.guidance.tokens == 0
    assert result.guidance.warnings == (
        "guidance unavailable: token counting failed: ProxyError: cannot fetch o200k_base",
    )
    assert result.guidance_file is None
    assert not list((layout.bundle_dir / CHILD / "references").glob("guidance-*.md"))
    assert result.route == baseline.route
    assert result.dispatch_resolution == baseline.dispatch_resolution
    assert result.dispatch_preflight == baseline.dispatch_preflight
