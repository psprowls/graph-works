from okf_io import Bundle
from work_tracker_okf.items import WorkItem, load_items


def _by_slug(bundle: Bundle) -> dict[str, WorkItem]:
    return {item.slug: item for item in load_items(bundle)}


def test_every_fixture_item_is_projected_and_nothing_else_is(minimal_bundle: Bundle) -> None:
    assert sorted(_by_slug(minimal_bundle)) == [
        "broken-eta",
        "bug-gamma",
        "bug-theta",
        "epic-alpha",
        "feature-beta",
        "spike-zeta",
        "tech-debt-delta",
        "test-gap-epsilon",
    ]


def test_a_page_two_segments_under_work_is_not_an_item(minimal_bundle: Bundle) -> None:
    """`work/feature-beta/notes.md` is a concept, and must still not be an
    item -- an item page sits at exactly one segment under the lane."""
    assert "work/feature-beta/notes" in minimal_bundle.concepts
    assert "notes" not in _by_slug(minimal_bundle)


def test_a_page_outside_the_lane_is_not_an_item(minimal_bundle: Bundle) -> None:
    assert "notes/scratch" in minimal_bundle.concepts
    assert "scratch" not in _by_slug(minimal_bundle)


def test_the_six_types_all_project(minimal_bundle: Bundle) -> None:
    items = _by_slug(minimal_bundle)
    assert {items[slug].type for slug in ("epic-alpha", "feature-beta", "bug-gamma")} == {"Epic", "Feature", "Bug"}
    assert {items[slug].type for slug in ("tech-debt-delta", "test-gap-epsilon", "spike-zeta")} == {
        "TechDebt",
        "TestGap",
        "Spike",
    }


def test_an_active_item_projects_every_field(minimal_bundle: Bundle) -> None:
    item = _by_slug(minimal_bundle)["feature-beta"]
    assert item.path == "work/feature-beta.md"
    assert item.archived is False
    assert item.type == "Feature"
    assert item.title == "Beta feature"
    assert item.description.startswith("A feature with both artifacts")
    assert item.status == "stable"
    assert item.workflow_status == "in-progress"
    assert item.phase == "execute"
    assert item.effort == "medium"
    assert item.opened == "2026-01-05"
    assert item.updated == "2026-02-02"
    assert item.affects == ("packages/work-tracker-okf",)
    assert item.parent == "epic-alpha"
    assert item.depends_on == ("spike-zeta",)
    assert item.owner is None
    assert item.resolved_in is None
    assert item.superseded_by is None
    assert item.tags == ("fixture",)
    assert item.has_spec_doc is True
    assert item.has_plan_doc is True
    assert [source.id for source in item.sources] == ["design-spec", "plan", "transcript-plan-subagent-1"]


def test_unquoted_dates_project_as_iso_strings(minimal_bundle: Bundle) -> None:
    """`epic-alpha` writes `opened: 2026-01-01` unquoted, so ruamel hands back
    a `datetime.date`. Reading through `fm_data(dates="iso")` is what makes
    the field reliably a `str` -- the §6.2 hazard, live."""
    item = _by_slug(minimal_bundle)["epic-alpha"]
    assert item.opened == "2026-01-01"
    assert item.updated == "2026-02-01"
    assert isinstance(item.opened, str)


def test_an_archived_item_projects_with_its_archive_path(minimal_bundle: Bundle) -> None:
    item = _by_slug(minimal_bundle)["bug-theta"]
    assert item.archived is True
    assert item.path == "work/_archive/bug-theta.md"
    assert item.workflow_status == "superseded"
    assert item.superseded_by == "bug-gamma"


def test_children_are_derived_across_the_archive_boundary(minimal_bundle: Bundle) -> None:
    """An archived child still belongs to its parent's list."""
    items = _by_slug(minimal_bundle)
    assert items["epic-alpha"].children == ("bug-theta", "feature-beta")
    assert items["feature-beta"].children == ()


def test_a_resolved_item_carries_its_ref_and_its_tags(minimal_bundle: Bundle) -> None:
    item = _by_slug(minimal_bundle)["bug-gamma"]
    assert item.workflow_status == "resolved"
    assert item.resolved_in == "abc1234"
    assert item.tags == ("fixture", "security")


def test_a_draft_item_projects_its_absent_optionals_as_none(minimal_bundle: Bundle) -> None:
    item = _by_slug(minimal_bundle)["tech-debt-delta"]
    assert item.status == "draft"
    assert item.effort is None
    assert item.phase is None
    assert item.affects == ()


def test_an_item_with_no_sources_has_neither_artifact(minimal_bundle: Bundle) -> None:
    item = _by_slug(minimal_bundle)["epic-alpha"]
    assert item.sources == ()
    assert item.has_spec_doc is False
    assert item.has_plan_doc is False


def test_a_malformed_page_projects_to_empty_values_without_raising(minimal_bundle: Bundle) -> None:
    """Every field on `broken-eta` is the wrong shape in a different way. The
    reader does not refuse it; the rules will report it."""
    item = _by_slug(minimal_bundle)["broken-eta"]
    assert item.slug == "broken-eta"
    assert item.path == "work/broken-eta.md"
    assert item.type == ""
    assert item.title == ""
    assert item.description == ""
    assert item.status == ""
    assert item.workflow_status == ""
    assert item.phase is None
    assert item.effort is None
    assert item.opened == ""
    assert item.updated == ""
    assert item.parent is None
    assert item.owner is None
    assert item.resolved_in is None
    assert item.superseded_by is None
    assert item.tags == ()
    assert item.sources == ()
    assert item.has_spec_doc is False
    assert item.has_plan_doc is False


def test_a_bare_string_where_a_list_belongs_is_empty_not_characters(minimal_bundle: Bundle) -> None:
    """The single likeliest hand-edit error in this lane. Iterating the string
    would produce ('p', 'a', 'c', ...) and every path rule would fire on it."""
    assert _by_slug(minimal_bundle)["broken-eta"].affects == ()


def test_non_string_entries_are_dropped_from_a_list_field(minimal_bundle: Bundle) -> None:
    assert _by_slug(minimal_bundle)["broken-eta"].depends_on == ("good-slug",)


def test_items_are_frozen_and_sorted_by_slug(minimal_bundle: Bundle) -> None:
    items = load_items(minimal_bundle)
    assert [item.slug for item in items] == sorted(item.slug for item in items)
    assert WorkItem.__dataclass_params__.frozen is True


def test_load_items_over_an_empty_bundle_is_empty(tmp_path) -> None:
    from pathlib import Path

    from okf_io import load_bundle

    root = Path(tmp_path) / "bundle"
    root.mkdir()
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    assert load_items(load_bundle(root)) == ()
