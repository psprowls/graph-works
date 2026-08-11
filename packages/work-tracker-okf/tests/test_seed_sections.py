import importlib.resources

from okf_ext.sections import render_skeleton
from okf_ext.shape import load_sections

_EXPECTED_HEADINGS = {
    "Epic": ("Goal", "Plan", "Notes / log"),
    "Feature": ("Options considered", "Plan", "Notes / log"),
    "Bug": ("Steps to reproduce", "Expected vs actual", "Plan", "Notes / log"),
    "TechDebt": ("Current state", "Plan", "Notes / log"),
    "TestGap": ("Coverage gap", "Plan", "Notes / log"),
    "Spike": ("Question", "Findings", "Plan", "Notes / log"),
}

_PLAN_TABLE = "| Action | Done when | Rationale |\n| --- | --- | --- |\n"


def _section_set():
    assets = importlib.resources.files("work_tracker_okf") / "assets" / "_sections"
    return load_sections(str(assets))


def test_seed_sections_load_as_exactly_the_six_types() -> None:
    section_set = _section_set()
    assert set(section_set.type_names) == set(_EXPECTED_HEADINGS)
    assert "_fragments" not in section_set.types


def test_the_declared_headings_and_their_order() -> None:
    section_set = _section_set()
    for type_name, headings in _EXPECTED_HEADINGS.items():
        declaration = section_set.types[type_name]
        assert tuple(spec.heading for spec in declaration.sections) == headings


def test_plan_is_the_only_required_section_and_it_is_a_template() -> None:
    """C1-G. `ownership: template` sets `seeded_is_complete` at load, so tier 2
    never reports `sections.unfilled` against a freshly-filed empty plan table
    -- the state a new item is supposed to be in."""
    section_set = _section_set()
    for type_name in _EXPECTED_HEADINGS:
        required = [spec for spec in section_set.types[type_name].sections if spec.required]
        assert [spec.heading for spec in required] == ["Plan"]
        assert required[0].ownership == "template"
        assert required[0].seeded_is_complete is True
        assert required[0].placeholder == _PLAN_TABLE


def test_one_fragment_backs_every_plan_section() -> None:
    section_set = _section_set()
    assert dict(section_set.fragments) == {"plan_table": _PLAN_TABLE}


def test_no_declaration_claims_frontmatter_ownership() -> None:
    """Nothing in this lane is generator-owned: every key is the human's or the
    CLI's, and the empty `FrontmatterOwnership` is that statement."""
    section_set = _section_set()
    for type_name in _EXPECTED_HEADINGS:
        frontmatter = section_set.types[type_name].frontmatter
        assert frontmatter.owned == ()
        assert frontmatter.provenance == ()


def test_render_skeleton_emits_every_declared_section_in_order() -> None:
    section_set = _section_set()
    body = render_skeleton(section_set.types["Spike"])
    assert body == (
        "## Question\n"
        "\n"
        "## Findings\n"
        "\n"
        "## Plan\n"
        "\n"
        "| Action | Done when | Rationale |\n"
        "| --- | --- | --- |\n"
        "\n"
        "## Notes / log\n"
        "\n"
    )
