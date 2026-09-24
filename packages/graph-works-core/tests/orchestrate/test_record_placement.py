"""`run_record_placement`: the observed pair, and nothing else, under the owner lock."""

from __future__ import annotations

import difflib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import TypedDict

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import placement
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.work import commands as work
from graph_works_core.workspace import decision_owner as owners
from graph_works_core.workspace import transactions
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import Bundle, load, load_bundle
from work_tracker_okf.decisions import ledger_ref
from work_tracker_okf.items import IGNORE, Stamp, WorkItem, load_items
from work_tracker_okf.mutation import WorkMutationPlan
from work_tracker_okf.placement import PlacementPlan
from work_tracker_okf.sources import upsert

TODAY = date(2026, 9, 14)
EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/feature-a"
NESTED = f"{EPIC}/children/feature-b"
GRANDCHILD = f"{NESTED}/children/bug-c"
SOLO = "work/feature-solo"
BR = "psprowls/feature-a-1a2b3c4d"


class _RecordOverrides(TypedDict, total=False):
    path: str
    root: str
    phase: str
    branch: str


def _abs(*parts: str) -> str:
    """Host-absolute on every platform: `/wt/fork` is not absolute on win32."""
    return str(Path(Path.cwd().anchor, *parts))


WT = _abs("wt", "fork")


def _write(
    layout: WorkspaceLayout, path: str, *, type: str, phase: str | None, work_status: str, owner: str | None = None
) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    phase_line = f"phase: {phase}\n" if phase is not None else ""
    owner_line = f"owner: {owner}\n" if owner is not None else ""
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\n"
        f"work_status: {work_status}\n{owner_line}{phase_line}effort: medium\nopened: 2026-08-01\n"
        "updated: 2026-08-01\naffects:\n- packages/a\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="",
    )


def _vault(tmp_path: Path) -> WorkspaceLayout:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Placement")).layout
    _write(layout, EPIC, type="Epic", phase="execute", work_status="in-progress")
    _write(layout, CHILD, type="Feature", phase="execute", work_status="in-progress", owner="pat")
    _write(layout, NESTED, type="Feature", phase="plan", work_status="open")
    _write(layout, GRANDCHILD, type="Bug", phase="execute", work_status="in-progress", owner="pat")
    _write(layout, SOLO, type="Feature", phase="plan", work_status="open")
    regenerated = work.run_regen_indexes(layout, dry_run=False)
    assert regenerated.application is not None and regenerated.application.ok
    return layout


def _record(
    layout: WorkspaceLayout,
    path: str = CHILD,
    *,
    root: str = EPIC,
    phase: str = "execute",
    worktree: str = WT,
    branch: str = BR,
    dry_run: bool = False,
) -> placement.PlacementRecord:
    return placement.run_record_placement(
        layout, path, root=root, phase=phase, worktree=worktree, branch=branch, today=TODAY, dry_run=dry_run
    )


def _snapshot(layout: WorkspaceLayout) -> dict[str, bytes]:
    return {
        p.relative_to(layout.bundle_dir).as_posix(): p.read_bytes() for p in sorted(layout.bundle_dir.rglob("*.md"))
    }


def test_recording_changes_only_the_pair_and_updated(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{CHILD}.md"
    before = page.read_text(encoding="utf-8")
    others = {k: v for k, v in _snapshot(layout).items() if k != f"{CHILD}.md"}

    record = _record(layout)

    assert record.plan.refusal is None
    assert record.written
    after = page.read_text(encoding="utf-8")
    changed = [line for line in difflib.ndiff(before.splitlines(), after.splitlines()) if line[:2] in ("- ", "+ ")]
    assert {line[2:].split(":", 1)[0] for line in changed} == {"worktree", "branch", "updated"}
    assert [line for line in changed if line.startswith("- ")] == ["- updated: 2026-08-01"]
    written = load(page).fm_data()
    assert (written["worktree"], written["branch"]) == (WT, BR)
    assert (written["phase"], written["work_status"], written["owner"]) == ("execute", "in-progress", "pat")
    assert {k: v for k, v in _snapshot(layout).items() if k != f"{CHILD}.md"} == others
    assert not (layout.cache_dir / "active-work.json").exists()


def test_an_identical_replay_writes_nothing(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    assert _record(layout).written
    before = _snapshot(layout)

    replay = _record(layout)

    assert replay.plan.refusal is None and not replay.plan.changed
    assert replay.application is None and not replay.written
    assert _snapshot(layout) == before


def test_a_dry_run_plans_the_change_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _vault(tmp_path)
    before = _snapshot(layout)
    monkeypatch.setattr(placement, "locked_decision_owner", lambda *a, **k: pytest.fail("dry run must not lock"))

    record = _record(layout, dry_run=True)

    assert record.plan.changed and record.application is None
    assert _snapshot(layout) == before


@pytest.mark.parametrize(
    ("kwargs", "refusal"),
    [
        ({"path": NESTED, "root": EPIC, "phase": "plan"}, "read-only-descendant"),
        ({"phase": "finish"}, "phase-mismatch"),
        ({"root": SOLO}, "outside-root"),
        ({"root": "work/nope"}, "unknown-root"),
        ({"path": "work/nope"}, "unknown-path"),
        ({"branch": "refs/heads/" + BR}, "invalid-pair"),
        ({"phase": "done"}, "invalid-phase"),
    ],
)
def test_every_refusal_writes_nothing(tmp_path: Path, kwargs: _RecordOverrides, refusal: str) -> None:
    layout = _vault(tmp_path)
    before = _snapshot(layout)

    record = _record(layout, **kwargs)

    assert record.plan.refusal == refusal
    assert record.application is None
    assert _snapshot(layout) == before


def test_a_terminal_item_refuses(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    _write(layout, CHILD, type="Feature", phase="done", work_status="resolved")
    assert _record(layout, phase="finish").plan.refusal == "terminal"


def test_a_nested_root_and_a_lone_root_record(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    assert _record(layout, NESTED, root=NESTED, phase="plan", worktree=_abs("wt", "nested"), branch="b/nested").written
    assert _record(layout, GRANDCHILD, root=NESTED, phase="execute", worktree=_abs("wt", "gc"), branch="b/gc").written
    assert _record(layout, SOLO, root=SOLO, phase="plan", worktree=_abs("wt", "solo"), branch="b/solo").written


def test_an_open_hold_is_left_untouched_and_still_blocks_advance(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    ledger_ref_epic = ledger_ref(EPIC)
    ledger_path = ledger_ref_epic.path(layout.bundle_dir)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("", encoding="utf-8", newline="")
    epic_page = layout.bundle_dir / f"{EPIC}.md"
    document = load(epic_page)
    upsert(document, ledger_ref_epic, title="Decisions")
    epic_page.write_text(document.serialize(), encoding="utf-8", newline="")
    filed = work.run_decision_add(
        layout, CHILD, question="Stop?", hold="skip", phase="execute", on=TODAY, decided_by="coordinator", dry_run=False
    )
    assert filed.application is not None and filed.application.ok
    ledger = owners.decision_context(layout, CHILD).owner.ledger
    ledger_before = ledger.read_bytes()

    assert _record(layout).written

    assert ledger.read_bytes() == ledger_before
    blocked = stage.run_stage_advance(layout, CHILD, today=TODAY, infer_worktree=False, dry_run=True)
    assert blocked.outcome.plan.refusal == "blocked"


def test_a_stale_preimage_refuses_without_a_partial_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{CHILD}.md"
    before = _snapshot(layout)
    external_edit = page.read_bytes() + b"\nout-of-band\n"
    original = transactions.apply_mutation

    def edited_out_of_band(
        layout: WorkspaceLayout,
        plan: WorkMutationPlan,
        *,
        repo_root: Path | None = None,
        repo_roots: tuple[Path, ...] = (),
        baseline_bundle: Bundle | None = None,
        allowed_new_findings: tuple[tuple[str, str], ...] = (),
        validate_read_set=None,
    ) -> transactions.MutationApplication:
        page.write_bytes(external_edit)
        return original(
            layout,
            plan,
            repo_root=repo_root,
            repo_roots=repo_roots,
            baseline_bundle=baseline_bundle,
            allowed_new_findings=allowed_new_findings,
            validate_read_set=validate_read_set,
        )

    monkeypatch.setattr(placement, "apply_mutation", edited_out_of_band)
    record = _record(layout)

    assert record.application is not None and not record.application.ok
    assert "changed since planning" in record.application.failures[0]
    assert not record.application.rolled_back
    assert not record.application.written
    assert not record.written
    assert _snapshot(layout) == {**before, f"{CHILD}.md": external_edit}
    text = page.read_text(encoding="utf-8")
    assert "worktree:" not in text and "branch:" not in text


def test_a_failed_postcondition_rolls_back_the_written_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{CHILD}.md"
    before = _snapshot(layout)
    observed: list[tuple[str, str]] = []

    def fail_after_write(*args: object, **kwargs: object) -> tuple[str, ...]:
        written = load(page).fm_data()
        observed.append((written["worktree"], written["branch"]))
        return ("forced placement validation failure",)

    monkeypatch.setattr(transactions, "_validate_postconditions", fail_after_write)

    record = _record(layout)

    assert observed == [(WT, BR)]
    assert record.application is not None
    assert not record.application.ok
    assert record.application.rolled_back
    assert record.application.failures == ("validation failed: forced placement validation failure",)
    assert not record.written
    assert _snapshot(layout) == before
    text = page.read_text(encoding="utf-8")
    assert "worktree:" not in text and "branch:" not in text


def test_the_lock_held_baseline_stays_unmutated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{CHILD}.md"
    before = page.read_bytes()
    original = transactions.apply_mutation

    def check_baseline(
        layout: WorkspaceLayout,
        plan: WorkMutationPlan,
        *,
        repo_root: Path | None = None,
        repo_roots: tuple[Path, ...] = (),
        baseline_bundle: Bundle | None = None,
        allowed_new_findings: tuple[tuple[str, str], ...] = (),
        validate_read_set=None,
    ) -> transactions.MutationApplication:
        assert baseline_bundle is not None
        document = baseline_bundle.concepts[CHILD]
        assert document.serialize().encode("utf-8") == before
        return original(
            layout,
            plan,
            repo_root=repo_root,
            repo_roots=repo_roots,
            baseline_bundle=baseline_bundle,
            allowed_new_findings=allowed_new_findings,
            validate_read_set=validate_read_set,
        )

    monkeypatch.setattr(placement, "apply_mutation", check_baseline)

    assert _record(layout).written


def test_an_edit_after_the_locked_projection_cannot_become_the_preimage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _vault(tmp_path)
    page = layout.bundle_dir / f"{CHILD}.md"
    external_edit = page.read_bytes().replace(b"phase: execute\n", b"phase: finish\n")
    before = _snapshot(layout)
    original = placement._mutation

    def edit_before_building_mutation(bundle: Bundle, plan: PlacementPlan) -> WorkMutationPlan:
        page.write_bytes(external_edit)
        return original(bundle, plan)

    monkeypatch.setattr(placement, "_mutation", edit_before_building_mutation)

    record = _record(layout)

    assert not record.written
    assert record.application is not None and not record.application.ok
    assert "changed since planning" in record.application.failures[0]
    assert not record.application.rolled_back
    assert not record.application.written
    assert page.read_bytes() == external_edit
    assert _snapshot(layout) == {**before, f"{CHILD}.md": external_edit}
    text = page.read_text(encoding="utf-8")
    assert "worktree:" not in text and "branch:" not in text


@pytest.mark.parametrize("bad_path", [CHILD, EPIC])
@pytest.mark.parametrize("field", ["phase", "effort"])
@pytest.mark.parametrize("raw", ["17", "[execute]", "{}", "false", '""'])
@pytest.mark.parametrize("identical_pair", [False, True])
@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "live"])
def test_raw_malformed_item_or_root_never_applies_even_on_replay(
    tmp_path: Path, bad_path: str, field: str, raw: str, identical_pair: bool, dry_run: bool
) -> None:
    layout = _vault(tmp_path)
    _write(layout, CHILD, type="TestGap", phase="execute", work_status="open")
    if identical_pair:
        assert _record(layout).written
    page = layout.bundle_dir / f"{bad_path}.md"
    old = "execute" if field == "phase" else "medium"
    page.write_bytes(page.read_bytes().replace(f"{field}: {old}\n".encode(), f"{field}: {raw}\n".encode()))
    before = _snapshot(layout)

    record = _record(layout, dry_run=dry_run)

    assert record.plan.refusal == "invalid-item"
    assert bad_path in record.plan.detail and field in record.plan.detail
    assert record.plan.changes == () and not record.plan.changed
    assert record.application is None and not record.written
    assert _snapshot(layout) == before


@pytest.mark.parametrize(
    ("phase", "effort", "status", "requested"),
    [
        ("17", "medium", "open", "design"),
        ("[execute]", "small", "open", "design"),
        ("execute", "17", "in-progress", "execute"),
    ],
)
@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "live"])
def test_raw_bug_repros_refuse_without_entry_or_write(
    tmp_path: Path, phase: str, effort: str, status: str, requested: str, dry_run: bool
) -> None:
    layout = _vault(tmp_path)
    _write(layout, SOLO, type="Bug", phase=phase, work_status=status)
    page = layout.bundle_dir / f"{SOLO}.md"
    page.write_bytes(page.read_bytes().replace(b"effort: medium\n", f"effort: {effort}\n".encode()))
    before = _snapshot(layout)

    record = _record(layout, SOLO, root=SOLO, phase=requested, dry_run=dry_run)

    assert record.plan.refusal == "invalid-item"
    assert record.plan.current_phase == ("execute" if phase == "execute" else None)
    assert record.plan.changes == ()
    assert record.application is None and not record.written
    assert _snapshot(layout) == before


@pytest.mark.parametrize("bad_path", [CHILD, EPIC])
@pytest.mark.parametrize("field", ["phase", "effort"])
def test_live_record_rejects_raw_invalidity_from_the_refreshed_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_path: str, field: str
) -> None:
    layout = _vault(tmp_path)
    original = owners.locked_decision_owner
    page = layout.bundle_dir / f"{bad_path}.md"
    old = "execute" if field == "phase" else "medium"
    external_edit = page.read_bytes().replace(f"{field}: {old}\n".encode(), f"{field}: 17\n".encode())
    before = _snapshot(layout)

    @contextmanager
    def edit_before_lock(layout: WorkspaceLayout, path: str) -> Iterator[owners.DecisionContext]:
        page.write_bytes(external_edit)
        with original(layout, path) as context:
            yield context

    monkeypatch.setattr(placement, "locked_decision_owner", edit_before_lock)
    record = _record(layout)

    assert record.plan.refusal == "invalid-item"
    assert bad_path in record.plan.detail and field in record.plan.detail
    assert record.plan.changes == ()
    assert record.application is None and not record.written
    assert _snapshot(layout) == {**before, f"{bad_path}.md": external_edit}


@pytest.mark.parametrize("optional", ["", "phase: null\n", "phase:\n"])
@pytest.mark.parametrize(("kind", "requested"), [("Bug", "design"), ("TestGap", "execute")])
@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "live"])
def test_raw_absent_or_null_phase_still_records_routing_entry(
    tmp_path: Path, optional: str, kind: str, requested: str, dry_run: bool
) -> None:
    layout = _vault(tmp_path)
    _write(layout, SOLO, type=kind, phase=None, work_status="open")
    page = layout.bundle_dir / f"{SOLO}.md"
    page.write_bytes(page.read_bytes().replace(b"effort: medium\n", f"effort: small\n{optional}".encode()))
    before = _snapshot(layout)

    record = _record(layout, SOLO, root=SOLO, phase=requested, dry_run=dry_run)

    assert record.plan.refusal is None and record.plan.current_phase == requested
    assert record.plan.changed and record.written is (not dry_run)
    if dry_run:
        assert record.application is None and _snapshot(layout) == before
    else:
        assert load(page).fm_data().get("phase") is None


@pytest.mark.parametrize("optional", ["", "effort: null\n", "effort:\n"])
@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "live"])
def test_raw_absent_or_null_effort_still_records_existing_phase(tmp_path: Path, optional: str, dry_run: bool) -> None:
    layout = _vault(tmp_path)
    for path in (CHILD, EPIC):
        page = layout.bundle_dir / f"{path}.md"
        page.write_bytes(page.read_bytes().replace(b"effort: medium\n", optional.encode()))
    before = _snapshot(layout)

    record = _record(layout, dry_run=dry_run)

    assert record.plan.refusal is None and record.plan.current_phase == "execute"
    assert record.plan.changed and record.written is (not dry_run)
    if dry_run:
        assert record.application is None and _snapshot(layout) == before
    else:
        assert load(layout.bundle_dir / f"{CHILD}.md").fm_data().get("effort") is None


def _declare_two_repos(layout: WorkspaceLayout, tmp_path: Path) -> None:
    code, ui = tmp_path / "code", tmp_path / "ui"
    (code / "packages/a").mkdir(parents=True)
    ui.mkdir()
    text = layout.manifest_path.read_text(encoding="utf-8")
    seeded = 'repositories:\n  "repo":\n    path: ".."\n'
    assert seeded in text
    declared = f'repositories:\n  code:\n    path: "{code.as_posix()}"\n  ui:\n    path: "{ui.as_posix()}"\n'
    layout.manifest_path.write_text(text.replace(seeded, declared), encoding="utf-8")


def test_several_declared_repos_still_refuse_without_a_repo_name(tmp_path: Path) -> None:
    """Placement keeps `resolve_repo`'s strict resolution: no inference."""
    from graph_works_core.workspace.errors import WorkspaceError

    layout = _vault(tmp_path)
    _declare_two_repos(layout, tmp_path)
    with pytest.raises(WorkspaceError, match="repo_name"):
        _record(layout)


def test_a_repo_name_selects_among_several_declared_repos(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    _declare_two_repos(layout, tmp_path)
    record = placement.run_record_placement(
        layout, CHILD, root=EPIC, phase="execute", worktree=WT, branch=BR, today=TODAY, repo_name="code", dry_run=False
    )
    assert record.written
    assert record.repo_note is None


def test_a_selected_repo_lacking_affects_needs_every_declared_repo_when_the_baseline_gate_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`repo_name` selects strictly (no inference), but postcondition
    validation must still check `affects` against every declared repo, not
    only the selected one -- matching `work file`/`work advance`
    (`stage_advance._advance`'s `repo_root=resolved_repo, repo_roots=declared`).

    The differential postcondition gate excuses a pre-existing
    `targets.affects-missing` as "not caused by this operation" either way --
    placement never touches `affects`, so the finding (if any) is identical
    in the baseline and post-mutation passes and never blocks by itself
    (`test_pre_existing_error_on_a_targeted_item_is_excused_and_reported` in
    `test_transactions.py` pins that general behaviour). The selected-vs-every
    distinction this fix makes only surfaces when baseline capture itself
    fails and `_validate_postconditions` falls back to its documented
    absolute form (no excusal at all) -- forced here the same way
    `test_a_stale_preimage_refuses_without_a_partial_pair` forces a related
    edge. There, validating only the selected `ui` repo (which lacks
    `CHILD.affects`'s `packages/a`) raises `affects-missing`; validating
    against every declared repo, as `stage_advance` does, does not.
    """
    layout = _vault(tmp_path)
    _declare_two_repos(layout, tmp_path)
    monkeypatch.setattr(
        transactions,
        "_capture_validation_state",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("forced baseline capture failure")),
    )

    record = placement.run_record_placement(
        layout, CHILD, root=EPIC, phase="execute", worktree=WT, branch=BR, today=TODAY, repo_name="ui", dry_run=False
    )

    assert record.repo_note is None
    assert record.application is not None, record.plan.refusal
    assert record.application.ok, record.application.failures
    assert record.written


def _tag(layout: WorkspaceLayout, path: str, repo: str) -> None:
    document = load(layout.bundle_dir / f"{path}.md")
    document.set("repo", repo)
    document.save()


def _child(layout: WorkspaceLayout) -> WorkItem:
    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    return next(item for item in items if item.path == CHILD)


def test_a_tagged_item_records_without_a_repo_name(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    _declare_two_repos(layout, tmp_path)
    _tag(layout, EPIC, "code")
    record = _record(layout)
    assert record.written and record.plan.repo is None


def test_repo_naming_another_repository_writes_repo_stamps(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    _declare_two_repos(layout, tmp_path)
    _tag(layout, EPIC, "code")
    record = placement.run_record_placement(
        layout, CHILD, root=EPIC, phase="execute", worktree=WT, branch=BR, today=TODAY, repo="ui", dry_run=False
    )
    assert record.written and record.plan.repo == "ui"
    child = _child(layout)
    assert child.worktree is None
    assert dict(child.repo_stamps) == {"ui": Stamp(WT, BR)}


def test_repo_naming_the_item_s_own_repository_writes_the_scalar_pair(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    _declare_two_repos(layout, tmp_path)
    _tag(layout, EPIC, "code")
    record = placement.run_record_placement(
        layout, CHILD, root=EPIC, phase="execute", worktree=WT, branch=BR, today=TODAY, repo="code", dry_run=False
    )
    assert record.written and record.plan.repo is None
    child = _child(layout)
    assert (child.worktree, child.branch) == (WT, BR)
    assert dict(child.repo_stamps) == {}


def test_repo_naming_an_undeclared_repository_refuses(tmp_path: Path) -> None:
    from graph_works_core.workspace.errors import WorkspaceError

    layout = _vault(tmp_path)
    _declare_two_repos(layout, tmp_path)
    _tag(layout, EPIC, "code")
    with pytest.raises(WorkspaceError, match="nope"):
        placement.run_record_placement(
            layout, CHILD, root=EPIC, phase="execute", worktree=WT, branch=BR, today=TODAY, repo="nope", dry_run=False
        )


def test_a_dry_run_with_repo_plans_the_foreign_target(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    _declare_two_repos(layout, tmp_path)
    _tag(layout, EPIC, "code")
    record = placement.run_record_placement(
        layout, CHILD, root=EPIC, phase="execute", worktree=WT, branch=BR, today=TODAY, repo="ui", dry_run=True
    )
    assert record.plan.repo == "ui" and record.application is None


def test_preparation_guard_records_only_unchanged_owner(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    guard = placement.preparation_guard(layout, EPIC)
    result = placement.run_record_placement(
        layout,
        EPIC,
        root=EPIC,
        phase="execute",
        worktree=WT,
        branch=BR,
        today=TODAY,
        dry_run=False,
        expected_preparation=guard,
    )
    assert result.written
    with pytest.raises(placement.WorkspaceError, match="preparation changed"):
        placement.run_record_placement(
            layout,
            EPIC,
            root=EPIC,
            phase="execute",
            worktree=WT,
            branch="different",
            today=TODAY,
            dry_run=False,
            expected_preparation=guard,
        )


@pytest.mark.parametrize("change", ["phase", "work_status", "repo", "stamp", "manifest"])
def test_preparation_guard_refuses_changed_inputs(tmp_path: Path, change: str) -> None:
    layout = _vault(tmp_path)
    guard = placement.preparation_guard(layout, NESTED)
    page = layout.bundle_dir / f"{NESTED}.md"
    document = load(page)
    if change == "manifest":
        with layout.manifest_path.open("a", encoding="utf-8", newline="") as stream:
            stream.write("\n# changed\n")
    else:
        key, value = {
            "phase": ("phase", "execute"),
            "work_status": ("work_status", "resolved"),
            "repo": ("repo", "other"),
            "stamp": ("branch", "other"),
        }[change]
        document.set(key, value)
        page.write_text(document.serialize(), encoding="utf-8", newline="")
    with pytest.raises(placement.WorkspaceError, match="preparation changed"):
        placement.run_record_placement(
            layout,
            NESTED,
            root=NESTED,
            phase="plan",
            worktree=WT,
            branch=BR,
            today=TODAY,
            dry_run=False,
            expected_preparation=guard,
        )


def test_guarded_preparation_binds_inherited_assignment(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    guard = placement.preparation_guard(layout, NESTED)
    page = layout.bundle_dir / f"{EPIC}.md"
    document = load(page)
    document.set("repo", "other")
    page.write_text(document.serialize(), encoding="utf-8", newline="")
    with pytest.raises(placement.WorkspaceError, match="preparation changed"):
        placement.run_record_placement(
            layout,
            NESTED,
            root=NESTED,
            phase="plan",
            worktree=WT,
            branch=BR,
            today=TODAY,
            dry_run=False,
            expected_preparation=guard,
        )


def test_preparation_adapter_records_foreign_anchor_with_owner_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    import runpy
    import sys

    layout = _vault(tmp_path)
    _declare_two_repos(layout, tmp_path)
    _tag(layout, EPIC, "code")
    adapter = Path(__file__).resolve().parents[4] / "plugins/gw/skills/auto-drive/references/record-preparation.py"
    common = [str(adapter), "snapshot", "--workspace", str(layout.root), "--owner", EPIC]
    monkeypatch.setattr(sys, "argv", common)
    runpy.run_path(str(adapter), run_name="__main__")
    guard = json.loads(capsys.readouterr().out)["guard"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(adapter),
            "record",
            *common[2:],
            "--root",
            EPIC,
            "--phase",
            "execute",
            "--repo",
            "ui",
            "--worktree",
            WT,
            "--branch",
            BR,
            "--expected",
            guard,
        ],
    )
    runpy.run_path(str(adapter), run_name="__main__")
    assert json.loads(capsys.readouterr().out)["written"] is True
    owner = next(item for item in load_items(load_bundle(layout.bundle_dir, ignore=IGNORE)) if item.path == EPIC)
    assert owner.repo_stamps["ui"] == Stamp(WT, BR)
    assert owner.worktree is None


def test_preparation_runtime_runs_real_adapter_from_external_cwd(tmp_path: Path) -> None:
    import json
    import runpy
    import subprocess

    layout = _vault(tmp_path)
    helper = Path(__file__).resolve().parents[4] / "plugins/gw/skills/auto-drive/references/launch-worker.py"
    functions = runpy.run_path(str(helper))
    argv = functions["preparation_runtime"]()
    result = subprocess.run(
        [*argv, "snapshot", "--workspace", str(layout.root), "--owner", EPIC],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["guard"] == placement.preparation_guard(layout, EPIC)
    recorded = subprocess.run(
        [
            *argv,
            "record",
            "--workspace",
            str(layout.root),
            "--owner",
            EPIC,
            "--root",
            EPIC,
            "--phase",
            "execute",
            "--repo",
            "repo",
            "--worktree",
            WT,
            "--branch",
            BR,
            "--expected",
            json.loads(result.stdout)["guard"],
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert recorded.returncode == 0, recorded.stderr
    assert json.loads(recorded.stdout)["written"] is True
