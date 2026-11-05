"""Batch refusals, inventory closure, and reserved partial-plan equivalence."""

from __future__ import annotations

import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest
from okf_io import load_bundle
from work_helpers import write_item
from work_tracker_okf import load_items, reparent
from work_tracker_okf.mutation import _all_members, _reserved_aliases


def _tree(root: Path) -> None:
    for path, kind in (("work/e", "Epic"), ("work/a", "Bug"), ("work/b", "Bug"), ("work/c", "Bug")):
        write_item(root, path, f"type: {kind}\nwork_status: open\n")


def test_batch_moves_every_source_and_descendant_in_one_plan(tmp_path: Path) -> None:
    _tree(tmp_path)
    write_item(tmp_path, "work/a/children/x", "type: Bug\nwork_status: open\n")
    bundle = load_bundle(tmp_path)
    plan = reparent.plan_reparent_many(bundle, load_items(bundle), ["work/a", "work/b"], "work/e")
    assert plan.ok, plan.refusals
    assert plan.path_mapping == {
        "work/a": "work/e/children/a",
        "work/a/children/x": "work/e/children/a/children/x",
        "work/b": "work/e/children/b",
    }
    assert plan.move_plan is not None
    assert {move.source for move in plan.move_plan.moves} == {"work/a.md", "work/a/children/x.md", "work/b.md"}


def test_single_path_form_is_unchanged(tmp_path: Path) -> None:
    _tree(tmp_path)
    bundle = load_bundle(tmp_path)
    items = load_items(bundle)
    assert reparent.plan_reparent(bundle, items, "work/a", "work/e") == reparent.plan_reparent_many(
        bundle, items, ["work/a"], "work/e"
    )


@pytest.mark.parametrize(
    ("sources", "kind"),
    [
        (["work/a", "work/a"], "duplicate-source"),
        (["work/e", "work/e/children/x"], "overlapping-sources"),
        (["work/e/children/x", "work/e"], "overlapping-sources"),
        ([], "empty-batch"),
        (["work/a", "work/missing"], "unknown-source"),
        (["work/a", "work/_archive/old"], "archived-source"),
        (["work/a", "work/release"], "nested-release"),
    ],
)
def test_one_bad_source_refuses_the_whole_batch(tmp_path: Path, sources: list[str], kind: str) -> None:
    _tree(tmp_path)
    write_item(tmp_path, "work/e/children/x", "type: Bug\nwork_status: open\n")
    write_item(tmp_path, "work/_archive/old", "type: Bug\nwork_status: resolved\n")
    write_item(tmp_path, "work/release", "type: Release\nwork_status: open\n")
    bundle = load_bundle(tmp_path)
    plan = reparent.plan_reparent_many(bundle, load_items(bundle), sources, "work/e")
    assert not plan.ok
    assert kind in {refusal.kind for refusal in plan.refusals}
    assert plan.writes == plan.moves == plan.deletes == plan.mkdirs == ()


def test_batch_aggregates_parent_and_every_source_refusal(tmp_path: Path) -> None:
    _tree(tmp_path)
    bundle = load_bundle(tmp_path)
    plan = reparent.plan_reparent_many(bundle, load_items(bundle), ["work/missing", "work/lost"], "work/c")
    assert {(r.path, r.kind) for r in plan.refusals} >= {
        ("work/c", "invalid-parent-type"),
        ("work/missing", "unknown-source"),
        ("work/lost", "unknown-source"),
    }
    assert plan.writes == plan.moves == plan.deletes == plan.mkdirs == ()


def test_nested_closure_includes_owner_context_without_ancestor_siblings() -> None:
    inventory = [
        "work/index.md",
        "work/e.md",
        "work/unrelated.md",
        "work/e/children/index.md",
        "work/e/children/a.md",
        "work/e/children/b.md",
        "work/e/children/p.md",
        "work/e/children/p/children/index.md",
        "work/e/children/p/children/existing.md",
        "work/e/children/q.md",
        "work/e/children/q/children/index.md",
        "work/e/children/q/children/owner.md",
        "work/e/children/q/children/sibling.md",
        "work/e/children/q/children/owner/references/01-design.md",
        "docs/far.md",
    ]
    touched = {"work/e/children/q/children/owner/references/01-design.md"}
    got = reparent.reparent_load_set(iter(inventory), touched, ["work/e/children/a"], "work/e/children/p")
    assert got == frozenset(inventory) - {"work/unrelated.md", "docs/far.md"}


def test_fixed_nested_closure_does_not_grow_with_unrelated_root_lane() -> None:
    fixed = {
        "work/index.md",
        "work/e.md",
        "work/e/children/index.md",
        "work/e/children/a.md",
        "work/e/children/p.md",
        "work/e/children/p/children/index.md",
        "work/e/children/p/children/x.md",
    }
    results = [
        reparent.reparent_load_set(
            fixed | {f"work/unrelated-{n}.md" for n in range(size)},
            {"work/e/children/a.md"},
            ["work/e/children/a"],
            "work/e/children/p",
        )
        for size in (100, 1000)
    ]
    assert results == [frozenset(fixed), frozenset(fixed)]


def test_touched_root_closure_keeps_siblings_and_raw_reference_ids() -> None:
    raw = unicodedata.normalize("NFD", "work/q/references/café.md")
    inventory = {"work/index.md", "work/a.md", "work/e.md", "work/q.md", raw, "docs/far.md"}
    assert reparent.reparent_load_set(inventory, {raw}, ["work/a"], "work/e") == inventory - {"docs/far.md"}


@pytest.mark.parametrize("filename", ["index.md", "log.md"])
@pytest.mark.parametrize("target_kind", ["unloaded", "pruned"])
def test_reserved_markdown_link_to_existing_target_has_exact_full_partial_plan(
    tmp_path: Path,
    filename: str,
    target_kind: str,
) -> None:
    _tree(tmp_path)
    member = f"work/a/references/{filename}"
    write_item(
        tmp_path,
        "work/a",
        f"type: Bug\nwork_status: open\nsources:\n  - id: context\n    resource: /{member}\n",
    )
    target = "docs/target.md"
    (tmp_path / target).parent.mkdir()
    (tmp_path / target).write_text("Existing target\n", encoding="utf-8")
    (tmp_path / member).parent.mkdir(parents=True)
    (tmp_path / member).write_text("See [target](../../../docs/target.md).\n", encoding="utf-8")
    full = load_bundle(tmp_path, prune=("docs",) if target_kind == "pruned" else ())
    partial = load_bundle(
        tmp_path,
        only={"work/a.md", "work/b.md", "work/c.md", "work/e.md", member},
        prune=("docs",) if target_kind == "pruned" else (),
    )
    if target_kind == "unloaded":
        assert target in partial.unloaded
    else:
        assert partial.pruned == {"docs"}
    full_plan = reparent.plan_reparent(full, load_items(full), "work/a", "work/e")
    partial_plan = reparent.plan_reparent(partial, load_items(partial), "work/a", "work/e")
    assert full_plan.ok, full_plan.refusals
    assert partial_plan == full_plan
    written = {write.member: write.after for write in partial_plan.writes}
    assert b"../../../../../docs/target.md" in written[f"work/e/children/a/references/{filename}"]


@pytest.mark.parametrize("selected", [False, True])
def test_reserved_alias_inventory_uses_only_virtual_raw_ids(tmp_path: Path, selected: bool) -> None:
    member = unicodedata.normalize("NFD", "work/a/references/café/index.md")
    (tmp_path / member).parent.mkdir(parents=True)
    (tmp_path / member).write_text("Opaque index\n", encoding="utf-8")
    partial = load_bundle(tmp_path, only=(member,) if selected else ())
    partial = replace(partial, unreadable_dirs=frozenset({"work/a/references/locked.md"}))
    virtual, aliases, _sources, _destinations = _reserved_aliases(
        partial,
        {member: "work/e/children/a/references/index.md"},
        frozenset() if selected else frozenset({member}),
    )
    assert virtual.unloaded == (frozenset() if selected else {aliases[member]})
    assert virtual.partial == ({aliases[member]} if selected else frozenset())
    assert virtual.unreadable_dirs == partial.unreadable_dirs
    assert _all_members(virtual) == (aliases[member],)
    assert virtual.member_id(member) == aliases[member]
    assert virtual.member_id(unicodedata.normalize("NFC", member)) == aliases[member]


def _top_level_tree(root: Path, *, epic_extra: str = "", child_extra: str = "") -> None:
    write_item(root, "work/e", f"type: Epic\nwork_status: open\n{epic_extra}")
    write_item(root, "work/e/children/x", f"type: Bug\nwork_status: open\n{child_extra}")
    write_item(root, "work/e/children/x/children/y", "type: Bug\nwork_status: open\n")


def _plan_top(root: Path, sources: list[str]):  # noqa: ANN202 -- test helper
    bundle = load_bundle(root)
    return reparent.plan_reparent_many(bundle, load_items(bundle), sources, None)


def test_top_level_moves_a_child_subtree_to_the_root(tmp_path: Path) -> None:
    _top_level_tree(tmp_path)
    plan = _plan_top(tmp_path, ["work/e/children/x"])
    assert plan.ok, plan.refusals
    assert plan.path_mapping == {
        "work/e/children/x": "work/x",
        "work/e/children/x/children/y": "work/x/children/y",
    }


def test_top_level_refuses_a_source_already_at_the_root(tmp_path: Path) -> None:
    _top_level_tree(tmp_path)
    plan = _plan_top(tmp_path, ["work/e"])
    assert "already-top-level" in {r.kind for r in plan.refusals}
    assert plan.writes == plan.moves == plan.deletes == plan.mkdirs == ()


def test_top_level_refuses_a_taken_root_basename_and_a_shared_basename(tmp_path: Path) -> None:
    _top_level_tree(tmp_path)
    write_item(tmp_path, "work/x", "type: Bug\nwork_status: open\n")
    assert not _plan_top(tmp_path, ["work/e/children/x"]).ok
    write_item(tmp_path, "work/f", "type: Epic\nwork_status: open\n")
    write_item(tmp_path, "work/f/children/z", "type: Bug\nwork_status: open\n")
    write_item(tmp_path, "work/e/children/z", "type: Bug\nwork_status: open\n")
    assert not _plan_top(tmp_path, ["work/e/children/z", "work/f/children/z"]).ok


def test_top_level_batch_mixing_a_root_item_refuses_whole(tmp_path: Path) -> None:
    _top_level_tree(tmp_path)
    plan = _plan_top(tmp_path, ["work/e/children/x", "work/e"])
    assert not plan.ok
    assert plan.writes == plan.moves == plan.deletes == plan.mkdirs == ()


def test_top_level_allows_a_release_source_but_refuses_a_deeper_release(tmp_path: Path) -> None:
    write_item(tmp_path, "work/e", "type: Epic\nwork_status: open\n")
    write_item(tmp_path, "work/e/children/rel", "type: Release\nwork_status: open\n")
    assert _plan_top(tmp_path, ["work/e/children/rel"]).ok
    write_item(tmp_path, "work/e/children/x", "type: Epic\nwork_status: open\n")
    write_item(tmp_path, "work/e/children/x/children/rel", "type: Release\nwork_status: open\n")
    plan = _plan_top(tmp_path, ["work/e/children/x"])
    assert "nested-release" in {r.kind for r in plan.refusals}


def test_top_level_refuses_when_an_inherited_repo_would_be_lost(tmp_path: Path) -> None:
    _top_level_tree(tmp_path, epic_extra="repo: graph-works\n")
    plan = _plan_top(tmp_path, ["work/e/children/x"])
    lost = [r for r in plan.refusals if r.kind == "repo-lost"]
    assert lost
    assert "work/e" in lost[0].detail
    assert "graph-works" in lost[0].detail


def test_top_level_keeps_a_repo_set_on_the_source_or_absent_everywhere(tmp_path: Path) -> None:
    _top_level_tree(tmp_path, epic_extra="repo: graph-works\n", child_extra="repo: graph-works\n")
    assert _plan_top(tmp_path, ["work/e/children/x"]).ok
    other = tmp_path / "other"
    other.mkdir()
    _top_level_tree(other)
    assert _plan_top(other, ["work/e/children/x"]).ok


def test_top_level_load_set_includes_the_root_lane() -> None:
    inventory = ["work/e.md", "work/e/children/x.md", "work/q.md", "work/index.md"]
    only = reparent.reparent_load_set(inventory, {"work/e/children/x.md"}, ["work/e/children/x"], None)
    assert {"work/q.md", "work/e.md"} <= only
