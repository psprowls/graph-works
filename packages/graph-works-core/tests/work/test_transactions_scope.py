"""`_gate_scope` -- the member set the postcondition gate's validate() call is
narrowed to.

Reuses `_transaction_helpers._workspace`/`_plan`, the same plan-construction
helpers `test_transactions.py` itself builds every fixture plan from, rather
than reinventing bundle/plan setup here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from _transaction_helpers import _plan, _workspace
from graph_works_core.work import apply_mutation
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.transactions import (
    EMPTY_TRANSACTION_ID,
    _affected_index_members,
    _baseline_scope,
    _gate_scope,
    _map_member,
)
from okf_io import load_bundle
from work_tracker_okf.indexes import plan_indexes
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.mutation import DirectoryPrecondition, PlannedWrite, WorkMutationPlan
from work_tracker_okf.reparent import plan_reparent


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def a_workspace(tmp_path: Path) -> WorkspaceLayout:
    return _workspace(tmp_path)


@pytest.fixture
def a_write_plan(a_workspace: WorkspaceLayout) -> WorkMutationPlan:
    """A real, applicable mutation: a conformant `work/feature` page already on
    disk, plus the lane-index update it requires -- built the same way
    `run_regen_indexes` builds one, so `apply_mutation` actually succeeds
    rather than merely exercising the scope-computation functions."""
    _write_item(a_workspace.bundle_dir, "work/feature", type="Feature")
    bundle = load_bundle(a_workspace.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    (index_plan,) = plan_indexes(bundle.root, items, lanes=("work",))
    return _plan(
        a_workspace,
        writes=(
            PlannedWrite(
                "work/index.md",
                _digest(index_plan.before.encode("utf-8")) if index_plan.before is not None else None,
                index_plan.after.encode("utf-8"),
            ),
        ),
        validate_paths=("work/feature",),
    )


@pytest.fixture
def an_empty_plan(a_workspace: WorkspaceLayout) -> WorkMutationPlan:
    return _plan(a_workspace)


@pytest.fixture
def an_empty_plan_with_directory_preconditions(a_workspace: WorkspaceLayout) -> WorkMutationPlan:
    """`run_regen_indexes` attaches `directory_preconditions` for lanes absent
    when the planner read them, even when it writes nothing itself."""
    return _plan(
        a_workspace,
        directory_preconditions=(DirectoryPrecondition("work/new", None),),
    )


def test_gate_scope_is_the_targeted_member_set(a_write_plan: WorkMutationPlan) -> None:
    """Same set the post-filter already compares against: every validate path as
    a `.md` member, plus every lane index the plan writes or moves into."""
    plan = a_write_plan
    expected = {f"{path}.md" for path in plan.validate_paths}
    expected.update(_affected_index_members(plan))
    assert _gate_scope(plan) == frozenset(expected)


def test_gate_scope_of_an_empty_plan_is_empty(an_empty_plan: WorkMutationPlan) -> None:
    assert _gate_scope(an_empty_plan) == frozenset()


def _write_item(
    root: Path,
    path: str,
    *,
    type: str,
    status: str = "stable",
) -> None:
    """Same shape as `test_transactions.py`'s own `_write_item`, duplicated here
    rather than imported -- that module is read-only for this task, and this
    fixture style is the one Task 5 established: build directly from the
    shared `_transaction_helpers.py` primitives rather than reaching into a
    sibling test module's private helpers."""
    page = root / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\n"
        f"type: {type}\n"
        f"title: {path.rsplit('/', 1)[-1]}\n"
        "description: D\n"
        f"status: {status}\n"
        "work_status: open\n"
        "phase: design\n"
        "effort: small\n"
        "affects: []\n"
        "opened: 2026-08-22\n"
        "updated: 2026-08-22\n"
        "---\n\n"
        "## Plan\n\n"
        "| Action | Done when | Rationale |\n"
        "| --- | --- | --- |\n",
        encoding="utf-8",
        newline="",
    )


@pytest.fixture
def a_move_plan(tmp_path: Path) -> WorkMutationPlan:
    """A single reparent-shaped move: `work/x` -> `work/p/children/x`."""
    layout = _workspace(tmp_path)
    return _plan(
        layout,
        path_mapping={"work/x": "work/p/children/x"},
        validate_paths=("work/p/children/x",),
    )


@pytest.fixture
def workspace_with_a_broken_child(tmp_path: Path) -> tuple[WorkspaceLayout, WorkMutationPlan]:
    """A real reparent plan over a bundle where the child being moved already
    carries a pre-existing validation error (an unknown `status:` value)."""
    layout = _workspace(tmp_path)
    release = "work/release-cutover"
    source = "work/bug-source"
    _write_item(layout.bundle_dir, release, type="Release")
    _write_item(layout.bundle_dir, source, type="Bug", status="not-a-real-status")
    (layout.bundle_dir / release / "children").mkdir(parents=True)
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    plan = plan_reparent(bundle, load_items(bundle), source, release)
    assert plan.ok is True
    return layout, plan


def test_baseline_scope_of_a_plan_with_no_moves_equals_the_gate_scope(a_write_plan: WorkMutationPlan) -> None:
    assert _baseline_scope(a_write_plan) == _gate_scope(a_write_plan)


def test_baseline_scope_is_the_pre_image_of_the_gate_scope_under_a_move(a_move_plan: WorkMutationPlan) -> None:
    """Every member in the baseline scope maps into the gate scope, and every
    member of the gate scope is the image of something in the baseline scope."""
    gate = _gate_scope(a_move_plan)
    baseline = _baseline_scope(a_move_plan)
    assert {_map_member(a_move_plan.path_mapping, member) for member in baseline} == gate


def test_the_baseline_scope_names_the_source_not_the_destination(a_move_plan: WorkMutationPlan) -> None:
    """A reparent moves `work/x.md` to `work/p/children/x.md`. The gate scope
    names the destination; the baseline, captured pre-move, must name the
    source -- otherwise the pre-existing failure on `work/x.md` is never
    counted and the mutation is blamed for it."""
    source = "work/x.md"
    dest = "work/p/children/x.md"
    assert source in _baseline_scope(a_move_plan)
    assert dest in _gate_scope(a_move_plan)
    assert source not in _gate_scope(a_move_plan)


def test_a_pre_existing_error_on_a_moved_document_is_excused_not_reported(
    workspace_with_a_broken_child: tuple[WorkspaceLayout, WorkMutationPlan],
) -> None:
    """Design Verification 3. The child page carries a validation error before
    the mutation; reparenting it must excuse that error, not roll back."""
    layout, plan = workspace_with_a_broken_child
    application = apply_mutation(layout, plan)
    assert application.ok, application.failures
    assert not application.rolled_back
    assert any("pre-existing, not caused by this operation" in note for note in application.warnings)


def test_a_supplied_baseline_bundle_feeds_the_findings_half_but_not_the_conditions_half(
    monkeypatch, a_workspace, a_write_plan
):
    """What `baseline_bundle=` actually still guarantees, now that the
    conditions half always reloads fresh under the lock (see
    `_capture_validation_state`'s docstring): the **findings** half's
    `validate()` call is fed the exact supplied bundle object, by identity --
    no second load happens for that half specifically. The total *load count*
    can no longer distinguish "reuse happened" from "reuse was silently
    ignored", because both the reused and unreused paths now perform exactly
    two `_load_bundle_through` calls overall (the conditions-half load plus
    the postcondition pass's load; see `test_without_a_supplied_bundle_both_
    passes_still_load`) -- so this test asserts object identity, not a count.
    """
    from graph_works_core.workspace import transactions

    seen_bundles: list[object] = []
    real_validate = transactions.validate

    def recording_validate(bundle, **kwargs):
        seen_bundles.append(bundle)
        return real_validate(bundle, **kwargs)

    monkeypatch.setattr(transactions, "validate", recording_validate)
    layout = a_workspace
    reused = load_bundle(layout.bundle_dir, ignore=IGNORE)
    transactions.apply_mutation(layout, a_write_plan, baseline_bundle=reused)

    assert len(seen_bundles) == 2, "one validate() call for the baseline findings half, one for the postcondition pass"
    findings_half_bundle, postcondition_bundle = seen_bundles
    assert findings_half_bundle is reused, (
        "the findings-half validate() call must be fed the exact supplied bundle object -- "
        "this is the one thing baseline_bundle= still saves a load for"
    )
    assert postcondition_bundle is not reused, (
        "the postcondition pass validates the post-mutation bundle, which is never the "
        "pristine pre-mutation bundle the caller supplied"
    )


def test_without_a_supplied_bundle_both_passes_still_load(monkeypatch, a_workspace, a_write_plan):
    from graph_works_core.workspace import transactions

    loads: list[str] = []
    real = transactions._load_bundle_through

    def counted(root, path, *, ignore):
        loads.append(path.as_posix())
        return real(root, path, ignore=ignore)

    monkeypatch.setattr(transactions, "_load_bundle_through", counted)
    transactions.apply_mutation(a_workspace, a_write_plan)
    assert len(loads) == 2


def test_a_reused_bundle_produces_the_same_gate_outcome(a_workspace, a_write_plan):
    reused = load_bundle(a_workspace.bundle_dir, ignore=IGNORE)
    with_reuse = apply_mutation(a_workspace, a_write_plan, baseline_bundle=reused)
    assert with_reuse.ok, with_reuse.failures


def test_the_conditions_half_is_never_derived_from_a_supplied_bundle(monkeypatch, tmp_path: Path) -> None:
    """Finding 1 of the final whole-branch review.

    A `baseline_bundle=` may be loaded before the lock is taken -- possibly by
    a different process -- and is only safe to reuse for the per-document
    findings half, which is scoped-and-preflight-sound. The whole-corpus
    `conditions` Counter (`load_items`/`_item_conditions`, which checks
    `parent-missing`/`dependency-missing` against the *full* item map) is not
    scoped that way: a concurrent mutation to some *other* item's parent,
    between when the supplied bundle was loaded and when
    `_capture_validation_state` runs under the lock, must still be picked up.

    This is set up directly against `_capture_validation_state` (rather than
    threading a real race through `apply_mutation`) because the property is
    about which bundle object feeds `load_items`, not about timing.
    """
    from graph_works_core.workspace import transactions

    layout = _workspace(tmp_path)
    _write_item(layout.bundle_dir, "work/parent", type="Release")
    (layout.bundle_dir / "work/parent/children").mkdir(parents=True)
    _write_item(layout.bundle_dir, "work/parent/children/child", type="Feature")
    _write_item(layout.bundle_dir, "work/other", type="Feature")

    # A bundle loaded while the parent still exists -- a stand-in for a
    # `baseline_bundle` loaded before the lock, by a different call.
    stale_bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    assert not any(
        kind == "parent-missing"
        for item in load_items(stale_bundle)
        for kind, _message in transactions._item_conditions(item, {i.path: i for i in load_items(stale_bundle)})
    ), "sanity check: the stale bundle must not itself show the concurrent condition"

    # A concurrent mutation to a *different* item than the one this plan
    # validates: delete the parent, leaving its child's `parent_path` dangling.
    (layout.bundle_dir / "work/parent.md").unlink()

    plan = _plan(layout, validate_paths=("work/other",))

    captured_bundles: list[object] = []
    real_load_items = transactions.load_items

    def recording_load_items(bundle):  # type: ignore[no-untyped-def]
        captured_bundles.append(bundle)
        return real_load_items(bundle)

    monkeypatch.setattr(transactions, "load_items", recording_load_items)

    root = transactions._open_root(layout.bundle_dir)
    try:
        state = transactions._capture_validation_state(layout, plan, root, repo_root=None, bundle=stale_bundle)
    finally:
        root.close()

    assert stale_bundle not in captured_bundles, (
        "the conditions half must never call load_items with the caller-supplied bundle"
    )
    assert state.conditions.get(("work/parent/children/child", "parent-missing")) == 1, (
        "the conditions half must reflect the fresh, post-concurrent-change state, "
        "not the stale supplied bundle's item map"
    )


def test_a_wholly_empty_plan_opens_the_lock_but_no_transaction_directory(
    a_workspace: WorkspaceLayout, an_empty_plan: WorkMutationPlan
) -> None:
    """The narrowed short-circuit still opens `work-mutations/` (the cache
    directory) and takes the executor lock through it -- two existing tests in
    `test_transactions.py` depend on that for an all-default, wholly-empty
    plan, and this run itself leaves an `executor.lock` file behind. What it
    skips is the per-mutation transaction *subdirectory* -- the only kind of
    entry `_new_transaction_directory` ever creates there -- so this checks
    for the absence of a new directory, not for `work-mutations/` being
    untouched altogether."""
    transaction_root = a_workspace.cache_dir / "work-mutations"

    def transaction_directories() -> set[Path]:
        if not transaction_root.exists():
            return set()
        return {entry for entry in transaction_root.glob("*") if entry.is_dir()}

    before = transaction_directories()
    application = apply_mutation(a_workspace, an_empty_plan)
    after = transaction_directories()
    assert after == before
    assert application.ok
    assert application.transaction_id == EMPTY_TRANSACTION_ID
    assert application.written == () and application.moved == () and application.created_directories == ()


def test_a_wholly_empty_plan_runs_no_validation(
    monkeypatch: pytest.MonkeyPatch, a_workspace: WorkspaceLayout, an_empty_plan: WorkMutationPlan
) -> None:
    from graph_works_core.workspace import transactions

    monkeypatch.setattr(
        transactions,
        "validate",
        lambda *a, **k: pytest.fail("empty plan must not validate"),
    )
    assert apply_mutation(a_workspace, an_empty_plan).ok


def test_a_plan_with_only_directory_preconditions_is_not_short_circuited(
    a_workspace: WorkspaceLayout, an_empty_plan_with_directory_preconditions: WorkMutationPlan
) -> None:
    """`run_regen_indexes` builds `directory_preconditions` for lanes absent
    before the planner read them. Those are a claim on the filesystem and must
    still be enforced, so such a plan is not `wholly` empty.

    Asserting on `transaction_id` directly (rather than diffing
    `work-mutations/` contents, as the sibling short-circuit tests do) is
    deliberate here: `_executor_lock` unconditionally creates
    `work-mutations/executor.lock` regardless of whether the plan is
    short-circuited, so an unfiltered before/after diff of that directory
    would pass even if `directory_preconditions` were dropped from
    `_is_wholly_empty` entirely -- it wouldn't actually guard the property
    this test claims to."""
    application = apply_mutation(a_workspace, an_empty_plan_with_directory_preconditions)
    assert application.transaction_id != EMPTY_TRANSACTION_ID, "a plan carrying preconditions must open a transaction"
