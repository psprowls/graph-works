from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from okf_io import load
from work_helpers import make_item
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.filing import FilingSeed, apply, compose_basename, plan_filing, slugify
from work_tracker_okf.init import install_bundle

TODAY = date(2026, 8, 22)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    install_bundle(root, today=TODAY, dry_run=False)
    return root


def seed(**overrides: object) -> FilingSeed:
    values: dict[str, object] = {
        "type": "Feature",
        "title": "Child",
        "description": "d",
        "on": TODAY,
        "affects": ("packages/work-tracker-okf",),
    }
    values.update(overrides)
    return FilingSeed(**values)  # type: ignore[arg-type]


def test_slugify_lowercases_and_collapses() -> None:
    assert slugify("The Filing Writer!") == "the-filing-writer"
    assert slugify("!!!") == "untitled"


@pytest.mark.parametrize(
    ("type_name", "expected"),
    [
        ("Release", "release-one-two"),
        ("Epic", "epic-one-two"),
        ("Feature", "feature-one-two"),
        ("Bug", "bug-one-two"),
        ("TechDebt", "tech-debt-one-two"),
        ("TestGap", "test-gap-one-two"),
        ("Spike", "spike-one-two"),
    ],
)
def test_compose_basename_is_date_free(type_name: str, expected: str) -> None:
    basename, warnings = compose_basename(type_name, "One Two")
    assert basename == expected
    assert warnings == ()


def test_compose_basename_preserves_the_word_warning_policy() -> None:
    five, kept = compose_basename("Feature", "one two three four five")
    seven, truncated = compose_basename("Feature", "one two three four five six seven")
    assert five == "feature-one-two-three-four-five"
    assert "5 words kept" in kept[0]
    assert seven == "feature-one-two-three-four-five-six"
    assert "truncated to 6" in truncated[0]


def test_filing_a_parent_creates_owned_lanes_and_no_hierarchy_frontmatter(vault, section_set):
    plan = plan_filing(
        vault,
        (),
        FilingSeed(type="Epic", title="Bundle migration", description="d", on=TODAY),
        section_set,
    )
    assert plan.path == "work/epic-bundle-migration"
    assert "parent" not in plan.frontmatter and "children" not in plan.frontmatter
    assert set(plan.required_directories) == {
        "work/epic-bundle-migration/references",
        "work/epic-bundle-migration/children",
        "work/epic-bundle-migration/children/_archive",
    }
    assert set(plan.required_indexes) == {
        "work/epic-bundle-migration/children/index.md",
        "work/epic-bundle-migration/children/_archive/index.md",
    }


def test_child_filing_uses_the_parent_active_lane(vault: Path, section_set) -> None:
    parent = make_item("release-r1/children/epic-migration", type="Epic")
    plan = plan_filing(vault, (parent,), seed(parent_path=parent.path), section_set)
    assert plan.path == f"{parent.path}/children/feature-child"
    assert "parent" not in plan.frontmatter


def test_name_overrides_the_title_for_the_basename(vault: Path, section_set) -> None:
    plan = plan_filing(vault, (), seed(title="Display title", name="stable identity"), section_set)
    assert plan.path == "work/feature-stable-identity"


def test_optional_release_and_common_fields_are_written(vault: Path, section_set) -> None:
    plan = plan_filing(
        vault,
        (),
        seed(
            type="Release",
            version="1.2.3",
            target_date=date(2026, 9, 1),
            effort="large",
            blast_radius="system",
            owner="human:pat",
            tags=("release",),
        ),
        section_set,
    )
    assert plan.frontmatter["version"] == "1.2.3"
    assert plan.frontmatter["target_date"] == date(2026, 9, 1)
    assert plan.frontmatter["work_status"] == "open"
    assert "workflow" + "_status" not in plan.frontmatter


def test_optional_empty_fields_are_omitted(vault: Path, section_set) -> None:
    plan = plan_filing(vault, (), seed(affects=(), tags=()), section_set)
    for key in ("name", "parent", "parent_path", "depends_on", "sources", "version", "target_date"):
        assert key not in plan.frontmatter


def test_dependency_edges_serialize_as_complete_path_mappings(vault: Path, section_set) -> None:
    dependency = make_item("bug-prerequisite")
    edge = DependencyEdge(dependency.path, "execute", "resolved")
    plan = plan_filing(vault, (dependency,), seed(depends_on=(edge,)), section_set)
    assert plan.frontmatter["depends_on"] == (
        {"path": "work/bug-prerequisite", "blocks": "execute", "needs": "resolved"},
    )


@pytest.mark.parametrize(
    ("items", "change", "refusal"),
    [
        ((), {"parent_path": "work/missing"}, "unknown-parent"),
        ((make_item("bug-parent", type="Bug"),), {"parent_path": "work/bug-parent"}, "invalid-parent-type"),
        (
            (make_item("epic-done", type="Epic", work_status="resolved"),),
            {"parent_path": "work/epic-done"},
            "inactive-parent",
        ),
        (
            (),
            {"depends_on": (DependencyEdge("work/missing", "execute", "resolved"),)},
            "unknown-dependency",
        ),
        (
            (make_item("epic-parent", type="Epic"),),
            {
                "parent_path": "work/epic-parent",
                "depends_on": (DependencyEdge("work/epic-parent", "execute", "resolved"),),
            },
            "invalid-dependency",
        ),
    ],
)
def test_graph_refusals_are_data(items, change, refusal, vault: Path, section_set) -> None:
    assert plan_filing(vault, items, replace(seed(), **change), section_set).refusal == refusal


def test_release_children_are_refused(vault: Path, section_set) -> None:
    parent = make_item("release-parent", type="Release")
    plan = plan_filing(vault, (parent,), seed(type="Release", parent_path=parent.path), section_set)
    assert plan.refusal == "root-only-child"


def test_destination_page_and_directory_collisions_are_distinct(vault: Path, section_set) -> None:
    plan = plan_filing(vault, (), seed(), section_set)
    plan.target.parent.mkdir(parents=True, exist_ok=True)
    plan.target.write_text("authored", encoding="utf-8")
    assert plan_filing(vault, (), seed(), section_set).refusal == "page-exists"
    plan.target.unlink()
    plan.owned_directory.mkdir(parents=True)
    assert plan_filing(vault, (), seed(), section_set).refusal == "directory-exists"


def test_apply_creates_scaffolding_but_does_not_register_gitkeep(vault: Path, section_set) -> None:
    plan = plan_filing(vault, (), seed(type="Epic", title="Migration"), section_set)
    target = apply(plan)
    document = load(target)
    assert (plan.owned_directory / "references" / ".gitkeep").is_file()
    assert (plan.owned_directory / "children" / "index.md").is_file()
    assert (plan.owned_directory / "children" / "_archive" / "index.md").is_file()
    assert "sources" not in document.fm_data()


def test_apply_a_leaf_creates_no_child_lane(vault: Path, section_set) -> None:
    plan = plan_filing(vault, (), seed(type="Bug"), section_set)
    apply(plan)
    assert (plan.owned_directory / "references" / ".gitkeep").is_file()
    assert not (plan.owned_directory / "children").exists()


def test_apply_rechecks_collisions(vault: Path, section_set) -> None:
    plan = plan_filing(vault, (), seed(), section_set)
    plan.owned_directory.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        apply(plan)
    assert not plan.target.exists()
