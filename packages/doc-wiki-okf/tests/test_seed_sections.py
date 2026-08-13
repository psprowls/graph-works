"""The four declarations load, resolve, and render. Mirrors work-tracker-okf's own."""

import importlib.resources

from okf_ext.sections import render_skeleton
from okf_ext.shape import load_sections

_EXPECTED_HEADINGS = {
    "Tutorial": ("What you will build", "Before you start", "Steps", "What you learned"),
    "HowTo": ("Goal", "Assumptions", "Steps", "Result"),
    "Reference": ("Summary", "See also"),
    "Explanation": ("Context", "Trade-offs", "See also"),
    "Source": (
        "TL;DR",
        "Key claims",
        "Touches",
        "Evidence / rationale",
        "Surprises / contradictions",
        "Decisions triggered",
        "Where it's cited in this wiki",
    ),
}

_EXPECTED_REQUIRED = {
    "Tutorial": ["What you will build", "Before you start", "Steps"],
    "HowTo": ["Goal", "Assumptions", "Steps", "Result"],
    "Reference": ["Summary"],
    "Explanation": ["Context"],
    "Source": [],
}

_SEE_ALSO = "> TODO: related pages, as root-absolute markdown links, one per line.\n"


def _section_set():
    assets = importlib.resources.files("doc_wiki_okf") / "assets" / "_sections"
    return load_sections(str(assets))


def test_seed_sections_load_as_exactly_the_five_types() -> None:
    section_set = _section_set()
    assert set(section_set.type_names) == set(_EXPECTED_HEADINGS)
    assert "_fragments" not in section_set.types


def test_the_declared_headings_and_their_order() -> None:
    section_set = _section_set()
    for type_name, headings in _EXPECTED_HEADINGS.items():
        declaration = section_set.types[type_name]
        assert tuple(spec.heading for spec in declaration.sections) == headings


def test_the_required_sets_are_prescriptive_for_two_and_loose_for_two() -> None:
    """Spec §3: Tutorial and HowTo have a shape a rule can check; Reference and
    Explanation do not, and a rigid skeleton over either is a permanent finding."""
    section_set = _section_set()
    for type_name, required in _EXPECTED_REQUIRED.items():
        found = [spec.heading for spec in section_set.types[type_name].sections if spec.required]
        assert found == required


def test_additional_sections_is_true_everywhere() -> None:
    """Real prose grows headings the declaration never named."""
    section_set = _section_set()
    for type_name in _EXPECTED_HEADINGS:
        assert section_set.types[type_name].additional_sections is True


def test_one_fragment_is_shared_by_reference_and_explanation() -> None:
    section_set = _section_set()
    assert dict(section_set.fragments) == {"see_also": _SEE_ALSO}
    for type_name in ("Reference", "Explanation"):
        see_also = next(spec for spec in section_set.types[type_name].sections if spec.heading == "See also")
        assert see_also.placeholder == _SEE_ALSO


def test_no_declaration_claims_frontmatter_ownership() -> None:
    section_set = _section_set()
    for type_name in _EXPECTED_HEADINGS:
        frontmatter = section_set.types[type_name].frontmatter
        assert frontmatter.owned == ()
        assert frontmatter.provenance == ()


def test_no_placeholder_carries_an_angle_bracket_or_a_wikilink() -> None:
    """Spec §3.2 and §3.3, as a declaration-level check. Task 7 asserts the same
    two properties through the shipped rules."""
    section_set = _section_set()
    for type_name in _EXPECTED_HEADINGS:
        for spec in section_set.types[type_name].sections:
            assert "<" not in spec.placeholder
            assert "[[" not in spec.placeholder
            assert "]]" not in spec.placeholder


def test_render_skeleton_emits_every_declared_section_in_order() -> None:
    body = render_skeleton(_section_set().types["Reference"])
    assert body == (
        "## Summary\n"
        "\n"
        "> TODO: what this page is a reference for, and what it is true of.\n"
        "\n"
        "## See also\n"
        "\n"
        "> TODO: related pages, as root-absolute markdown links, one per line.\n"
        "\n"
    )
