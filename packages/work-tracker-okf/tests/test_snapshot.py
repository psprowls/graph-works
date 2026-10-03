"""`WorkSnapshot`: an immutable, indexed `Sequence[WorkItem]` (D-007, D-008)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import pytest
from okf_io import Bundle, Source, load_bundle
from work_helpers import CONFORMANT_ROOT, CONFORMANT_TODAY, NONCONFORMANT_ROOT, make_item, write_item
from work_tracker_okf import snapshot as snapshot_module
from work_tracker_okf.archive import _default_targets
from work_tracker_okf.decisions import HoldFact
from work_tracker_okf.dependencies import DependencyEdge, DependencyFact, resolve_facts
from work_tracker_okf.hierarchy import (
    active_nonterminal_descendants,
    child_gated,
    child_rollup,
    decision_owner,
    descend,
    nearest_epic,
    nearest_parent,
    sweep_eligible,
    unknown_depends_on,
)
from work_tracker_okf.items import IGNORE, WorkItem, item_index, load_items
from work_tracker_okf.projection import rollup
from work_tracker_okf.snapshot import WorkSnapshot, as_snapshot
from work_tracker_okf.workflow import RouteState, state_for


def _rows(bundle: Bundle) -> list[tuple[str, dict[str, object]]]:
    return [(concept_id, document.fm_data(dates="iso")) for concept_id, document in bundle.concepts.items()]


def _bundles(path_native_bundle: Bundle) -> list[Bundle]:
    return [
        path_native_bundle,
        load_bundle(CONFORMANT_ROOT, ignore=IGNORE),
        load_bundle(NONCONFORMANT_ROOT, ignore=IGNORE),
    ]


def test_load_items_returns_a_path_sorted_snapshot(path_native_bundle: Bundle) -> None:
    items = load_items(path_native_bundle)
    assert isinstance(items, WorkSnapshot)
    assert isinstance(items, Sequence)
    assert [item.path for item in items] == sorted(item.path for item in items)


def test_sequence_behaviour_matches_the_underlying_tuple(path_native_bundle: Bundle) -> None:
    items = load_items(path_native_bundle)
    plain = tuple(items)
    assert len(items) == len(plain)
    assert list(items) == list(plain)
    assert items[0] is plain[0]
    assert items[-1] is plain[-1]
    assert list(reversed(items)) == list(reversed(plain))
    assert plain[1] in items
    assert items.index(plain[1]) == 1


def test_slicing_returns_a_plain_tuple(path_native_bundle: Bundle) -> None:
    items = load_items(path_native_bundle)
    head = items[:2]
    assert type(head) is tuple
    assert head == tuple(items)[:2]


def test_snapshot_does_not_equal_a_tuple(path_native_bundle: Bundle) -> None:
    items = load_items(path_native_bundle)
    assert items != tuple(items)
    assert tuple(items) == tuple(load_items(path_native_bundle))


def test_snapshot_is_immutable() -> None:
    snap = WorkSnapshot((make_item("work/a"),))
    with pytest.raises(AttributeError):
        snap.by_path = {}  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snap.anything = 1  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        snap.by_path["work/b"] = make_item("work/b")  # type: ignore[index]


def test_as_snapshot_is_identity_for_a_snapshot_and_builds_once_otherwise(monkeypatch: pytest.MonkeyPatch) -> None:
    builds: list[int] = []
    real = snapshot_module._build_indexes

    def counting(items):  # type: ignore[no-untyped-def]
        builds.append(len(items))
        return real(items)

    monkeypatch.setattr(snapshot_module, "_build_indexes", counting)
    plain = (make_item("work/a"), make_item("work/b"))
    snap = as_snapshot(plain)
    assert builds == [2]
    assert as_snapshot(snap) is snap
    assert builds == [2]


def test_as_snapshot_preserves_hand_built_children() -> None:
    parent = make_item("work/release", type="Release", child_paths=("work/missing", "work/feature"))
    feature = make_item("work/feature", parent_path=parent.path, child_paths=("work/bug", "work/release"))
    plain = (parent, feature)
    snap = as_snapshot(plain)
    assert tuple(snap) == plain
    assert snap.by_path["work/feature"].child_paths == ("work/bug", "work/release")
    assert snap.child_items("work/release") == (feature,)
    assert snap.by_parent == {"work/release": (feature,)}


def test_duplicate_paths_keep_last_wins() -> None:
    first = make_item("work/a", title="first")
    second = make_item("work/a", title="second")
    assert as_snapshot((first, second)).by_path["work/a"] is second


def test_child_items_and_by_parent_agree_on_loaded_bundles(path_native_bundle: Bundle) -> None:
    items = load_items(path_native_bundle)
    for item in items:
        assert items.child_items(item.path) == items.by_parent.get(item.path, ())


def test_memo_computes_once_per_kind_and_key() -> None:
    snap = WorkSnapshot((make_item("work/a"),))
    calls: list[str] = []
    assert snap.memo("k", "work/a", lambda: calls.append("x") or 1) == 1
    assert snap.memo("k", "work/a", lambda: calls.append("y") or 2) == 1
    assert snap.memo("other", "work/a", lambda: calls.append("z") or 3) == 3
    assert calls == ["x", "z"]


def test_item_index_copies_a_snapshot_index(path_native_bundle: Bundle) -> None:
    items = load_items(path_native_bundle)
    index = item_index(items)
    assert type(index) is dict
    assert index == dict(items.by_path)
    index.clear()
    assert len(items.by_path) == len(items)


def test_from_rows_equals_from_bundle_on_every_fixture(path_native_bundle: Bundle) -> None:
    for bundle in _bundles(path_native_bundle):
        assert tuple(WorkSnapshot.from_rows(_rows(bundle))) == tuple(WorkSnapshot.from_bundle(bundle))


def test_from_rows_omits_the_v01_body_citation_fallback(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "work/feature-legacy",
        "type: Feature\nwork_status: open\nopened: 2026-09-01\nupdated: 2026-09-01\n",
        body="\n# Citations\n\n- [Old](/sources/2026-01-old.md)\n",
    )
    bundle = load_bundle(tmp_path, ignore=IGNORE)
    (from_bundle,) = WorkSnapshot.from_bundle(bundle)
    (from_rows,) = WorkSnapshot.from_rows(_rows(bundle))
    assert len(from_bundle.sources) == 1
    assert from_rows.sources == ()
    assert from_rows.has_design_artifact == from_bundle.has_design_artifact
    assert from_rows.has_plan_artifact == from_bundle.has_plan_artifact
    assert from_rows == type(from_rows)(**{**_fields(from_bundle), "sources": ()})


def _date_and_string_projections(tmp_path: Path, frontmatter: str) -> tuple[WorkItem, WorkItem, WorkItem]:
    """Identical plain rows can originate from different native YAML values."""
    bundles = []
    for name, value in (("native", "2026-09-01"), ("string", '"2026-09-01"')):
        root = tmp_path / name
        write_item(root, "work/feature-date", "type: Feature\n" + frontmatter.format(value=value))
        bundles.append(load_bundle(root, ignore=IGNORE))
    native, authored_string = bundles
    assert _rows(native) == _rows(authored_string)
    (native_item,) = WorkSnapshot.from_bundle(native)
    (string_item,) = WorkSnapshot.from_bundle(authored_string)
    (row_item,) = WorkSnapshot.from_rows(_rows(native))
    assert tuple(WorkSnapshot.from_rows(_rows(authored_string))) == (row_item,)
    return native_item, string_item, row_item


def test_from_rows_accepts_native_date_tags_as_iso_strings(tmp_path: Path) -> None:
    native, authored_string, row = _date_and_string_projections(tmp_path, "tags: [{value}]\n")
    assert native.tags == ()
    assert authored_string.tags == row.tags == ("2026-09-01",)
    assert row == authored_string == replace(native, tags=row.tags)


@pytest.mark.parametrize("field", ["id", "resource", "title"])
def test_from_rows_accepts_native_date_source_scalars_as_iso_strings(tmp_path: Path, field: str) -> None:
    native, authored_string, row = _date_and_string_projections(tmp_path, "sources:\n  - " + field + ": {value}\n")
    (native_source,) = native.sources
    (row_source,) = row.sources
    assert getattr(native_source, field) is None
    assert getattr(row_source, field) == "2026-09-01"
    assert row_source == replace(native_source, **{field: "2026-09-01"})
    assert row == authored_string == replace(native, sources=row.sources)


def test_from_rows_converts_native_dates_in_source_extra_to_iso_strings(tmp_path: Path) -> None:
    native, authored_string, row = _date_and_string_projections(
        tmp_path,
        "sources:\n  - title: Evidence\n    custom: {value}\n    nested:\n      dates: [{value}]\n",
    )
    (native_source,) = native.sources
    (row_source,) = row.sources
    assert native_source.extra == {"custom": date(2026, 9, 1), "nested": {"dates": [date(2026, 9, 1)]}}
    assert row_source.extra == {"custom": "2026-09-01", "nested": {"dates": ["2026-09-01"]}}
    assert row_source == replace(native_source, extra=row_source.extra)
    assert row == authored_string == replace(native, sources=row.sources)


@pytest.mark.parametrize("in_list", [False, True], ids=["mapping", "mapping-in-list"])
def test_from_rows_stringifies_native_numeric_source_extra_keys(tmp_path: Path, in_list: bool) -> None:
    counts = "[{1: numeric}]" if in_list else "{1: numeric}"
    path = "work/bug-key"
    write_item(tmp_path, path, f"type: Bug\nsources:\n  - title: Evidence\n    nested:\n      counts: {counts}\n")
    bundle = load_bundle(tmp_path, ignore=IGNORE)
    document = bundle.concepts[path]
    assert document.parse_error is None
    native_counts = [{1: "numeric"}] if in_list else {1: "numeric"}
    plain_counts = [{"1": "numeric"}] if in_list else {"1": "numeric"}
    native_extra = {"nested": {"counts": native_counts}}
    plain_extra = {"nested": {"counts": plain_counts}}
    assert document.fm.sources[0].extra == native_extra
    rows = _rows(bundle)
    assert rows[0][1]["sources"] == [{"title": "Evidence", **plain_extra}]
    expected = make_item(
        path,
        type="Bug",
        title="T",
        description="D",
        status="",
        work_status="",
        sources=(Source(title="Evidence", extra=native_extra),),
    )
    assert tuple(WorkSnapshot.from_bundle(bundle)) == (expected,)
    assert tuple(WorkSnapshot.from_rows(rows)) == (
        replace(expected, sources=(Source(title="Evidence", extra=plain_extra),)),
    )
    assert document.fm.sources[0].extra == native_extra


@pytest.mark.parametrize("in_list", [False, True], ids=["mapping", "mapping-in-list"])
@pytest.mark.parametrize("reverse", [False, True], ids=["string-last", "numeric-last"])
def test_from_rows_source_extra_key_collisions_keep_last_entry(tmp_path: Path, reverse: bool, in_list: bool) -> None:
    entries = '"1": string, 1: numeric' if reverse else '1: numeric, "1": string'
    counts = "{" + entries + "}"
    if in_list:
        counts = "[" + counts + "]"
    path = "work/bug-key-collision"
    write_item(tmp_path, path, f"type: Bug\nsources:\n  - title: Evidence\n    nested:\n      counts: {counts}\n")
    bundle = load_bundle(tmp_path, ignore=IGNORE)
    document = bundle.concepts[path]
    assert document.parse_error is None
    native = {"1": "string", 1: "numeric"} if reverse else {1: "numeric", "1": "string"}
    plain = {"1": "numeric" if reverse else "string"}
    native_extra = {"nested": {"counts": [native] if in_list else native}}
    plain_extra = {"nested": {"counts": [plain] if in_list else plain}}
    raw_counts = document.fm_raw["sources"][0]["nested"]["counts"]
    raw_mapping = raw_counts[0] if in_list else raw_counts
    assert list(raw_mapping.items()) == list(native.items())
    assert len(raw_mapping) == 2
    assert document.fm.sources[0].extra == native_extra
    rows = _rows(bundle)
    # Loss has already occurred in fm_data, before from_rows rebuilds sources.
    assert rows[0][1]["sources"] == [{"title": "Evidence", **plain_extra}]
    expected = make_item(
        path,
        type="Bug",
        title="T",
        description="D",
        status="",
        work_status="",
        sources=(Source(title="Evidence", extra=native_extra),),
    )
    assert tuple(WorkSnapshot.from_bundle(bundle)) == (expected,)
    assert tuple(WorkSnapshot.from_rows(rows)) == (
        replace(expected, sources=(Source(title="Evidence", extra=plain_extra),)),
    )
    assert list(raw_mapping.items()) == list(native.items())
    assert document.fm.sources[0].extra == native_extra


def _fields(item: object) -> dict[str, object]:
    from dataclasses import fields

    return {field.name: getattr(item, field.name) for field in fields(item)}  # type: ignore[arg-type]


def test_from_rows_skips_non_item_concepts_and_links_children() -> None:
    rows = [
        ("work/epic-a", {"type": "Epic", "work_status": "open"}),
        ("work/epic-a/children/bug-b", {"type": "Bug", "work_status": "open"}),
        ("docs/reference/x", {"type": "Reference"}),
    ]
    snap = WorkSnapshot.from_rows(rows)
    assert [item.path for item in snap] == ["work/epic-a", "work/epic-a/children/bug-b"]
    assert snap.by_path["work/epic-a"].child_paths == ("work/epic-a/children/bug-b",)


def test_unknown_paths_answer_empty() -> None:
    snap = WorkSnapshot(())
    assert snap.child_items("work/nope") == ()
    assert snap.by_path.get("work/nope") is None
    assert len(snap) == 0


def _hand_built() -> tuple[WorkItem, ...]:
    release = make_item("work/release", type="Release", child_paths=("work/missing", "work/feature"))
    feature = make_item("work/feature", parent_path=release.path, child_paths=("work/bug", "work/release"))
    bug = make_item("work/bug", type="Bug", parent_path=feature.path, archived=True)
    epic = make_item("work/epic", type="Epic", phase="execute", child_paths=("work/epic/children/a",))
    child = make_item(
        "work/epic/children/a",
        type="Bug",
        parent_path=epic.path,
        dependency_edges=(DependencyEdge("work/ghost", "execute", "resolved"),),
    )
    done = make_item("work/done", type="Bug", work_status="resolved")
    return (release, feature, bug, epic, child, done)


def _answers(items: Sequence[WorkItem]) -> dict[str, object]:
    paths = [item.path for item in items] + ["work/unknown"]
    edges = tuple(edge for item in items for edge in item.dependency_edges)
    return {
        "child_rollup": [child_rollup(items, path) for path in paths],
        "descendants": [active_nonterminal_descendants(items, path) for path in paths],
        "nearest_parent": [nearest_parent(items, path) for path in paths],
        "nearest_epic": [nearest_epic(items, path) for path in paths],
        "decision_owner": [decision_owner(items, path) for path in paths],
        "sweep": [sweep_eligible(items, item) for item in items],
        "gated": [child_gated(items, item) for item in items],
        "unknown": unknown_depends_on(items, edges),
        "descend": [descend(items, path) for path in paths],
    }


@pytest.mark.parametrize("source", ["hand-built", "path-native"])
def test_hierarchy_answers_match_on_tuple_and_snapshot(source: str, path_native_bundle: Bundle) -> None:
    items = _hand_built() if source == "hand-built" else tuple(load_items(path_native_bundle))
    assert _answers(as_snapshot(items)) == _answers(items)


def test_hierarchy_helpers_memoize_per_snapshot() -> None:
    snap = as_snapshot(_hand_built())
    first = active_nonterminal_descendants(snap, "work/release")
    assert active_nonterminal_descendants(snap, "work/release") is first
    assert child_rollup(snap, "work/epic") is child_rollup(snap, "work/epic")


def test_sweep_eligible_does_not_memoize_a_foreign_item() -> None:
    done = make_item("work/done", type="Bug", work_status="resolved")
    snap = as_snapshot((done,))
    assert sweep_eligible(snap, done) is True
    reopened = replace(done, work_status="open")
    assert sweep_eligible(snap, reopened) is False
    assert sweep_eligible(snap, done) is True


@pytest.mark.parametrize("use_snapshot", [False, True], ids=["tuple", "snapshot"])
@pytest.mark.parametrize("reverse", [False, True], ids=["resolved-last", "open-last"])
def test_state_for_duplicate_paths_use_last_status(use_snapshot: bool, reverse: bool) -> None:
    path = "work/bug-duplicate"
    opened = make_item(path, type="Bug", work_status="open")
    resolved = make_item(path, type="Bug", work_status="resolved")
    items = (resolved, opened) if reverse else (opened, resolved)
    expected = RouteState(type="Bug", work_status="open" if reverse else "resolved")
    assert state_for(as_snapshot(items) if use_snapshot else items, path) == expected


def test_state_for_matches_on_tuple_and_snapshot(path_native_bundle: Bundle) -> None:
    for items in (_hand_built(), tuple(load_items(path_native_bundle))):
        snap = as_snapshot(items)
        for item in items:
            assert state_for(snap, item.path) == state_for(items, item.path)
            assert state_for(snap, item.path, effort="large") == state_for(items, item.path, effort="large")
        assert state_for(snap, "work/unknown") is None


@dataclass(frozen=True)
class PlainNode:
    path: str
    phase: str | None
    work_status: str


def test_resolve_facts_matches_and_accepts_plain_nodes() -> None:
    items = _hand_built()
    edges = (
        DependencyEdge("work/done", "execute", "resolved"),
        DependencyEdge("work/ghost", "execute", "resolved"),
        DependencyEdge("work/done", "plan", "design"),
    )
    expected = (DependencyFact("work/done", True, True, None, "resolved"), DependencyFact("work/ghost", False, False))
    assert resolve_facts(as_snapshot(items), edges) == resolve_facts(items, edges) == expected
    assert resolve_facts((PlainNode("work/done", None, "resolved"),), edges) == expected


def test_resolve_facts_memoizes_per_snapshot_and_reads_fresh_loads(tmp_path: Path) -> None:
    path = "work/bug-a"
    edges = (DependencyEdge(path, "execute", "resolved"), DependencyEdge("work/ghost", "execute", "resolved"))
    write_item(tmp_path, path, "type: Bug\nwork_status: resolved\n")
    snap = load_items(load_bundle(tmp_path, ignore=IGNORE))
    first = resolve_facts(snap, edges)
    second = resolve_facts(snap, edges)
    assert second == first
    assert all(a is b for a, b in zip(first, second, strict=True))
    write_item(tmp_path, path, "type: Bug\nwork_status: open\n")
    fresh = load_items(load_bundle(tmp_path, ignore=IGNORE))
    assert resolve_facts(fresh, edges)[0] == DependencyFact(path, True, False, None, "open")
    assert resolve_facts(snap, edges)[0] is first[0]


def test_state_for_builds_indexes_once_for_a_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    builds: list[int] = []
    real = snapshot_module._build_indexes
    monkeypatch.setattr(snapshot_module, "_build_indexes", lambda items: (builds.append(1), real(items))[1])
    state = state_for(_hand_built(), "work/epic")
    assert state is not None and state.child_rollup is not None and state.child_rollup.total == 1
    assert builds == [1]


def test_state_for_keeps_effort_hold_and_stale_spec_per_call() -> None:
    snap = as_snapshot(_hand_built())
    path = "work/epic"
    original = state_for(snap, path)
    hold = HoldFact(path, "D-001", "park", "execute")
    changed = state_for(snap, path, effort="large", hold=hold, stale_spec=("work/landed",))
    assert original is not None and changed is not None
    assert changed == replace(original, effort="large", hold=hold, stale_spec=("work/landed",))
    assert state_for(snap, path) == original


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    builds: list[int] = []
    real = snapshot_module._build_indexes

    def counting(items):  # type: ignore[no-untyped-def]
        builds.append(len(items))
        return real(items)

    monkeypatch.setattr(snapshot_module, "_build_indexes", counting)
    return builds


def _population(n: int) -> tuple[WorkItem, ...]:
    roots = [make_item(f"work/bug-{i:03d}", type="Bug", work_status="resolved") for i in range(n)]
    epic = make_item("work/epic-x", type="Epic", child_paths=tuple(f"work/epic-x/children/bug-{i}" for i in range(n)))
    kids = [make_item(f"work/epic-x/children/bug-{i}", type="Bug", parent_path=epic.path) for i in range(n)]
    return (*roots, epic, *kids)


def test_archive_sweep_builds_indexes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    builds = _counting(monkeypatch)
    targets = _default_targets(_population(40))
    assert targets == tuple(f"work/bug-{i:03d}" for i in range(40))
    assert len(builds) == 1


def test_projection_rollup_builds_indexes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    builds = _counting(monkeypatch)
    # Two parents expose repeated coercion; one parent already builds only once.
    rolled = rollup((*_population(40), make_item("work/epic-y", type="Epic")))
    assert rolled.children["work/epic-x"].total == 40
    assert rolled.children["work/epic-y"].total == 0
    assert rolled.total == 82
    assert len(builds) == 1


def test_archive_eligible_rule_builds_indexes_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from okf_io.links import build
    from okf_io.validate import RuleContext
    from work_tracker_okf._rules.state import terminal

    base = "work_status: resolved\nopened: 2026-09-01\nupdated: 2026-09-01\n"
    for i in range(20):
        write_item(tmp_path, f"work/bug-{i:03d}", "type: Bug\n" + base)
    bundle = load_bundle(tmp_path, ignore=IGNORE)
    ctx = RuleContext(bundle=bundle, links=build(bundle), today=CONFORMANT_TODAY)
    builds = _counting(monkeypatch)
    findings = list(terminal(ctx))
    assert len(findings) == 20
    assert {finding.code for finding in findings} == {"state.archive-eligible"}
    assert len(builds) == 1
