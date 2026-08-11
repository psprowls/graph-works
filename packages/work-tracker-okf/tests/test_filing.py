from datetime import date
from pathlib import Path

import pytest
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import load_sections
from okf_io import load_bundle, validate
from work_tracker_okf import IGNORE
from work_tracker_okf.filing import FilingPlan, apply, compose_slug, file_item, slugify
from work_tracker_okf.init import install_bundle

_ON = date(2026, 3, 2)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """An installed, empty bundle: the fourteen declarations plus the scaffold."""
    root = tmp_path / "vault"
    install_bundle(root, today=_ON, dry_run=False)
    return root


def _file(vault: Path, section_set, **overrides) -> FilingPlan:
    kwargs = {
        "type": "Feature",
        "title": "The filing writer",
        "description": "Owns where a work item lands.",
        "on": _ON,
        "affects": ("packages/work-tracker-okf",),
        "tags": ("fixture",),
        "section_set": section_set,
    }
    kwargs.update(overrides)
    return file_item(vault, **kwargs)


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


def test_the_seeded_frontmatter_is_the_documented_set(vault: Path, section_set) -> None:
    plan = _file(vault, section_set, parent="2026-03-01-epic-root", depends_on=("2026-03-03-spike-x",))
    assert plan.frontmatter["type"] == "Feature"
    assert plan.frontmatter["status"] == "draft"
    assert plan.frontmatter["workflow_status"] == "open"
    assert plan.frontmatter["opened"] == _ON
    assert plan.frontmatter["updated"] == _ON
    assert plan.frontmatter["parent"] == "2026-03-01-epic-root"
    assert plan.frontmatter["depends_on"] == ["2026-03-03-spike-x"]


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

    document = parse(apply(_file(vault, section_set, parent="2026-03-01-epic-root")).read_text(encoding="utf-8"))
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
        "parent",
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
            schema_rule(load_schemas(vault / "_schema"), severity="error"),
            section_rule(load_sections(vault / "_sections"), severity="error"),
        ),
    )
    assert [f"{f.code} {f.path}: {f.message}" for f in report.errors] == []
