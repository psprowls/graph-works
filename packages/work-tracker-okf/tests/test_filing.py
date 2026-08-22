from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import work_tracker_okf.filing as filing
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import load_sections
from okf_io import load_bundle, validate
from work_helpers import make_item
from work_tracker_okf import IGNORE, load_items
from work_tracker_okf.dependencies import DependencyEdge
from work_tracker_okf.filing import FilingPlan, FilingSeed, apply, compose_slug, plan_filing, slugify
from work_tracker_okf.init import install_bundle

_ON = date(2026, 3, 2)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """An installed, empty bundle: the fourteen declarations plus the scaffold."""
    root = tmp_path / "vault"
    install_bundle(root, today=_ON, dry_run=False)
    return root


def seed(**overrides: object) -> FilingSeed:
    values: dict[str, object] = {
        "type": "Feature",
        "title": "Child",
        "description": "d",
        "on": date(2026, 8, 18),
        "affects": ("packages/work-tracker-okf",),
    }
    values.update(overrides)
    return FilingSeed(**values)  # type: ignore[arg-type]


def _file(vault: Path, section_set, **overrides: object) -> FilingPlan:
    values: dict[str, object] = {
        "type": "Feature",
        "title": "The filing writer",
        "description": "Owns where a work item lands.",
        "on": _ON,
        "affects": ("packages/work-tracker-okf",),
        "tags": ("fixture",),
    }
    values.update(overrides)
    bundle = load_bundle(vault, ignore=IGNORE)
    return plan_filing(vault, load_items(bundle), FilingSeed(**values), section_set)  # type: ignore[arg-type]


# --- slug composition -------------------------------------------------------


def test_slugify_lowercases_and_collapses() -> None:
    assert slugify("The Filing Writer!") == "the-filing-writer"


def test_slugify_falls_back_to_untitled() -> None:
    assert slugify("") == "untitled"
    assert slugify("   ") == "untitled"
    assert slugify("!!!") == "untitled"


@pytest.mark.parametrize(
    ("type_name", "expected"),
    [
        ("Epic", "2026-03-02-epic-one-two"),
        ("Feature", "2026-03-02-feature-one-two"),
        ("Bug", "2026-03-02-bug-one-two"),
        ("TechDebt", "2026-03-02-tech-debt-one-two"),
        ("TestGap", "2026-03-02-test-gap-one-two"),
        ("Spike", "2026-03-02-spike-one-two"),
    ],
)
def test_compose_slug_covers_all_six_types_with_the_date_prefix(type_name, expected) -> None:
    """C2-A: the prefix stays, and `compose_slug` composes the whole thing rather
    than leaving half of it to the writer as `work-io` did."""
    slug, warnings = compose_slug(type_name, "One Two", on=_ON)
    assert slug == expected
    assert warnings == ()


def test_epic_child_takes_the_epic_prefix() -> None:
    slug, _ = compose_slug("Feature", "item layout", on=_ON, epic_child=True)
    assert slug == "2026-03-02-epic-feature-item-layout"


def test_four_words_pass_clean() -> None:
    slug, warnings = compose_slug("Feature", "one two three four", on=_ON)
    assert slug == "2026-03-02-feature-one-two-three-four"
    assert warnings == ()


def test_five_and_six_words_are_kept_with_a_warning() -> None:
    slug, warnings = compose_slug("Feature", "one two three four five", on=_ON)
    assert slug.endswith("one-two-three-four-five")
    assert len(warnings) == 1
    assert "5 words kept" in warnings[0]


def test_seven_words_are_truncated_to_six_with_a_warning() -> None:
    slug, warnings = compose_slug("Feature", "one two three four five six seven", on=_ON)
    assert slug == "2026-03-02-feature-one-two-three-four-five-six"
    assert len(warnings) == 1
    assert "truncated to 6" in warnings[0]


def test_the_warning_names_no_cli_flag() -> None:
    _, warnings = compose_slug("Feature", "one two three four five", on=_ON)
    assert "--" not in warnings[0]


def test_empty_words_degrade_silently_to_untitled() -> None:
    slug, warnings = compose_slug("Bug", "   ", on=_ON)
    assert slug == "2026-03-02-bug-untitled"
    assert warnings == ()


def test_compose_slug_raises_on_an_unknown_type() -> None:
    with pytest.raises(ValueError, match="unknown type"):
        compose_slug("Chore", "one two", on=_ON)


def test_graph_aware_planner_does_not_expose_the_old_file_item_api() -> None:
    assert not hasattr(filing, "file_item")


# --- planning ---------------------------------------------------------------


def test_the_plan_writes_nothing(vault: Path, section_set) -> None:
    """C2-E: not calling `apply` *is* the dry run. There is no `dry_run` flag."""
    plan = _file(vault, section_set)
    assert plan.changed is True
    assert plan.diff()
    assert not plan.target.exists()
    assert not (vault / "work").exists()


def test_the_plan_carries_the_composed_slug_and_target(vault: Path, section_set) -> None:
    plan = _file(vault, section_set, title="The filing writer", words="filing writer")
    assert plan.slug == "2026-03-02-feature-filing-writer"
    assert plan.target == vault / "work" / f"{plan.slug}.md"


def test_words_default_to_the_title(vault: Path, section_set) -> None:
    plan = _file(vault, section_set, title="One Two Three", words=None)
    assert plan.slug == "2026-03-02-feature-one-two-three"


def test_seed_carries_every_supported_field(vault: Path, section_set) -> None:
    filing_seed = FilingSeed(
        type="Feature",
        title="Child",
        description="d",
        on=date(2026, 8, 18),
        effort="medium",
        blast_radius="package",
        target="2026-Q4",
        owner="pat",
        parent="epic",
        depends_on=(DependencyEdge("sibling", blocks="plan", needs="design"),),
        affects=("packages/a",),
        tags=("compat",),
    )
    plan = plan_filing(vault, (make_item("epic", type="Epic"), make_item("sibling")), filing_seed, section_set)
    assert plan.frontmatter["effort"] == "medium"
    assert plan.frontmatter["blast_radius"] == "package"
    assert plan.frontmatter["target"] == "2026-Q4"
    assert plan.frontmatter["owner"] == "pat"
    assert plan.frontmatter["depends_on"] == ({"slug": "sibling", "blocks": "plan", "needs": "design"},)


def test_filing_plan_sequence_values_cannot_be_mutated(vault: Path, section_set) -> None:
    plan = _file(vault, section_set, tags=("compat",), affects=("packages/a",))
    tags = plan.frontmatter["tags"]
    affects = plan.frontmatter["affects"]

    with pytest.raises(AttributeError):
        tags.append("mutated")
    with pytest.raises(AttributeError):
        affects.append("packages/b")

    assert tags == ("compat",)
    assert affects == ("packages/a",)


def test_filing_plan_nested_dependency_mappings_cannot_be_mutated(vault: Path, section_set) -> None:
    plan = plan_filing(
        vault,
        (make_item("sibling"),),
        seed(
            depends_on=(DependencyEdge("sibling", blocks="plan", needs="design"),),
        ),
        section_set,
    )
    dependencies = plan.frontmatter["depends_on"]

    with pytest.raises(TypeError):
        dependencies[0]["needs"] = "resolved"

    assert dependencies[0]["needs"] == "design"


def test_direct_epic_child_derives_epic_prefix_but_feature_child_does_not(vault: Path, section_set) -> None:
    epic_child = plan_filing(vault, (make_item("parent", type="Epic"),), seed(parent="parent"), section_set)
    feature_child = plan_filing(vault, (make_item("parent", type="Feature"),), seed(parent="parent"), section_set)
    assert "-epic-feature-" in epic_child.slug
    assert "-epic-feature-" not in feature_child.slug


@pytest.mark.parametrize(
    ("items", "seed_change", "refusal"),
    [
        ((), {"parent": "missing"}, "unknown-parent"),
        ((make_item("parent", type="Bug"),), {"parent": "parent"}, "invalid-parent-type"),
        ((make_item("parent", type="Epic", workflow_status="resolved"),), {"parent": "parent"}, "inactive-parent"),
        ((), {"depends_on": (DependencyEdge("missing"),)}, "unknown-dependency"),
        (
            (make_item("parent", type="Epic"),),
            {"parent": "parent", "depends_on": (DependencyEdge("parent"),)},
            "invalid-dependency",
        ),
    ],
)
def test_expected_graph_refusals_are_data(items, seed_change, refusal, vault: Path, section_set) -> None:
    plan = plan_filing(vault, items, replace(seed(), **seed_change), section_set)
    assert plan.refusal == refusal


@pytest.mark.parametrize(
    ("seed_change", "refusal"),
    [
        ({"blast_radius": "repository"}, "invalid-blast-radius"),
        ({"target": "2026-Q5"}, "invalid-target"),
        ({"effort": "tiny"}, "invalid-effort"),
    ],
)
def test_invalid_filing_metadata_is_refused_before_write(seed_change, refusal, vault: Path, section_set) -> None:
    plan = plan_filing(vault, (), replace(seed(), **seed_change), section_set)
    assert plan.refusal == refusal


def test_archived_dependency_is_valid_and_parent_note_distinguishes_parent_type(vault: Path, section_set) -> None:
    archived = make_item("landed", archived=True, workflow_status="resolved")
    epic_plan = plan_filing(
        vault,
        (make_item("parent", type="Epic"), archived),
        seed(parent="parent", depends_on=(DependencyEdge("landed"),)),
        section_set,
    )
    feature_plan = plan_filing(
        vault,
        (make_item("parent", type="Feature"), archived),
        seed(parent="parent", depends_on=(DependencyEdge("landed"),)),
        section_set,
    )
    assert epic_plan.refusal is None and "Designed as part of epic" in epic_plan.body
    assert feature_plan.refusal is None and "Filed as a child" in feature_plan.body


def test_active_parent_metadata_wins_over_an_archived_same_slug_twin(vault: Path, section_set) -> None:
    plan = plan_filing(
        vault,
        (
            make_item("parent", type="Epic"),
            make_item("parent", type="Feature", archived=True, path="work/_archive/parent.md"),
        ),
        seed(parent="parent"),
        section_set,
    )

    assert plan.refusal is None
    assert "-epic-feature-" in plan.slug
    assert "Designed as part of epic parent" in plan.body


def test_no_phase_is_seeded(vault: Path, section_set) -> None:
    """Child 3's router has an entry branch defined for `phase: None`; writing
    `phase: design` at filing time pre-empts a routing decision the table owns."""
    plan = _file(vault, section_set)
    assert "phase" not in plan.frontmatter


def test_empty_optional_keys_are_omitted_entirely(vault: Path, section_set) -> None:
    plan = _file(vault, section_set, affects=(), tags=(), parent=None, depends_on=())
    for key in ("affects", "tags", "parent", "depends_on"):
        assert key not in plan.frontmatter


# --- refusals ---------------------------------------------------------------


def test_an_existing_page_is_refused_and_left_alone(vault: Path, section_set) -> None:
    plan = _file(vault, section_set)
    apply(plan)
    before = plan.target.read_bytes()

    again = _file(vault, section_set)
    assert again.refusal == "page-exists"
    assert again.changed is False
    assert "page-exists" in again.diff()
    assert plan.target.read_bytes() == before


def test_an_existing_working_directory_is_refused(vault: Path, section_set) -> None:
    plan = _file(vault, section_set)
    (vault / "work" / plan.slug).mkdir(parents=True)

    again = _file(vault, section_set)
    assert again.refusal == "directory-exists"
    assert not again.target.exists()


def test_an_unknown_type_is_refused_rather_than_raised(vault: Path, section_set) -> None:
    plan = _file(vault, section_set, type="Chore")
    assert plan.refusal == "unknown-type"
    assert plan.changed is False


def test_applying_a_refused_plan_raises_and_writes_nothing(vault: Path, section_set) -> None:
    """A refused plan's `target` is an existing page; a silent write would clobber
    exactly what `page-exists` protects. A raise, not an assert -- asserts vanish
    under `-O`."""
    plan = _file(vault, section_set)
    apply(plan)
    before = plan.target.read_bytes()

    again = _file(vault, section_set)
    with pytest.raises(ValueError, match="refused"):
        apply(again)
    assert plan.target.read_bytes() == before


def test_apply_rechecks_page_and_directory_before_exclusive_create(vault: Path, section_set) -> None:
    plan = plan_filing(vault, (make_item("parent", type="Epic"),), seed(parent="parent"), section_set)
    plan.target.parent.mkdir(parents=True)
    plan.target.write_text("authored", encoding="utf-8")
    with pytest.raises(FileExistsError):
        apply(plan)
    assert plan.target.read_text(encoding="utf-8") == "authored"


def test_apply_rechecks_work_directory_before_exclusive_create(vault: Path, section_set) -> None:
    plan = plan_filing(vault, (), seed(), section_set)
    plan.work_directory.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        apply(plan)
    assert not plan.target.exists()


def test_apply_uses_exclusive_create(vault: Path, section_set, monkeypatch) -> None:
    plan = plan_filing(vault, (), seed(), section_set)
    modes: list[str] = []
    original = Path.open

    def recording_open(path: Path, mode: str = "r", *args, **kwargs):
        modes.append(mode)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recording_open)
    assert apply(plan) == plan.target
    assert "x" in modes


# --- the written page -------------------------------------------------------


def test_apply_writes_the_page_and_returns_its_path(vault: Path, section_set) -> None:
    plan = _file(vault, section_set)
    target = apply(plan)
    assert target == plan.target
    assert target.is_file()


def test_the_dates_render_bare_not_quoted(vault: Path, section_set) -> None:
    """C3-J: ruamel quotes a `str` that would re-parse as a date; every authored
    page renders bare, so `opened` and `updated` are written as `date` objects."""
    text = apply(_file(vault, section_set)).read_text(encoding="utf-8")
    assert "opened: 2026-03-02" in text
    assert "updated: 2026-03-02" in text
    assert "'2026-03-02'" not in text


def test_the_body_is_the_declared_skeleton(vault: Path, section_set) -> None:
    text = apply(_file(vault, section_set)).read_text(encoding="utf-8")
    assert "## Options considered" in text
    assert "## Plan" in text
    assert "| Action | Done when | Rationale |" in text
    assert "## Notes / log" in text


def test_the_key_order_is_the_one_document_set_produces(vault: Path, section_set) -> None:
    """`Document.set` places a key per okf-io's `PREFERRED_KEY_ORDER` when the core
    schema knows it, and appends otherwise -- so `tags` is hoisted above `status`
    whatever order `_KEY_ORDER` lists. This pins the result rather than the
    intention, because the conformant vault has to be authored to match it."""
    from okf_io import parse

    document = parse(apply(_file(vault, section_set)).read_text(encoding="utf-8"))
    assert list(document.fm_data()) == [
        "type",
        "title",
        "description",
        "tags",
        "status",
        "workflow_status",
        "opened",
        "updated",
        "affects",
    ]


@pytest.mark.parametrize("type_name", ["Epic", "Feature", "Bug", "TechDebt", "TestGap", "Spike"])
def test_the_filed_page_validates_against_its_own_declarations(vault: Path, section_set, type_name) -> None:
    """§6 declines to rely on `section_rule`'s `warn` default: both rules are
    built at `severity="error"`, so a violation actually shows up as one."""
    apply(_file(vault, section_set, type=type_name, words=f"{type_name.lower()} item"))
    bundle = load_bundle(vault, ignore=IGNORE)
    report = validate(
        bundle,
        today=_ON,
        extra_rules=(
            schema_rule(load_schemas(vault / "schema"), severity="error"),
            section_rule(load_sections(vault / "sections"), severity="error"),
        ),
    )
    assert [f"{f.code} {f.path}: {f.message}" for f in report.errors] == []
