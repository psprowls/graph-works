"""Spec §7.3, §7.4, §7.5 and §9, through the shipped rules."""

from __future__ import annotations

from pathlib import Path

import pytest
from diataxis_helpers import (
    build_bundle,
    declaration_rules,
    ext_codes,
    full_ext_rules,
    schema_set,
    section_set,
    validate_with,
)
from doc_wiki_okf.diataxis.classify import Classification
from doc_wiki_okf.diataxis.pages import default_concept_id, new_page_text
from okf_ext.sections import render_skeleton

_TYPES = ("Tutorial", "HowTo", "Reference", "Explanation")

#: One line of real prose per declared section, so a filled-in page is not
#: sitting on its placeholder. Keyed by heading, shared across the four types.
_FILLED = "Real prose, written by a human, and not the placeholder.\n"


def _filled_page(type_name: str, title: str) -> str:
    """`new_page_text`, with every declared section's placeholder replaced."""
    schemas, sections = schema_set(), section_set()
    text = new_page_text(
        schema_set=schemas,
        section_set=sections,
        classification=Classification(
            type_name=type_name,
            concept_id=default_concept_id(schemas, type_name=type_name, title=title),
            title=title,
            rationale="because",
            decided_by="agent:test",
        ),
        description=f"A {type_name} page.",
    )
    for spec in sections.types[type_name].sections:
        if spec.placeholder:
            text = text.replace(spec.placeholder, _FILLED)
    return text


def test_a_conformant_page_per_type_produces_no_ext_finding(tmp_path: Path) -> None:
    """Spec §7.3."""
    schemas = schema_set()
    pages = {
        default_concept_id(schemas, type_name=name, title=f"A {name}"): _filled_page(name, f"A {name}")
        for name in _TYPES
    }
    bundle = build_bundle(tmp_path / "bundle", pages)
    report = validate_with(bundle, declaration_rules())
    assert ext_codes(report) == [], ext_codes(report)


def test_a_missing_required_section_is_exactly_sections_missing(tmp_path: Path) -> None:
    """Spec §7.4, first half."""
    page = (
        "---\ntype: HowTo\ntitle: How to do it\ndescription: A how-to.\n---\n\n"
        "## Goal\n\nDo the thing.\n\n"
        "## Assumptions\n\nYou have the thing.\n\n"
        "## Steps\n\nDo it.\n"
    )  # `Result` is missing
    bundle = build_bundle(tmp_path / "bundle", {"how-tos/how-to-do-it": page})
    report = validate_with(bundle, declaration_rules())
    assert ext_codes(report) == ["sections.missing"]
    assert "Result" in report.by_code("sections.missing")[0].message


def test_an_undeclared_type_is_exactly_no_declaration_for_type(tmp_path: Path) -> None:
    """Spec §7.4, second half. `Concept` is one of the two lanes §8 leaves
    undeclared on purpose."""
    page = "---\ntype: Concept\ntitle: A concept\ndescription: A concept.\n---\n\n## Summary\n\nWords.\n"
    bundle = build_bundle(tmp_path / "bundle", {"concepts/a-concept": page})
    report = validate_with(bundle, declaration_rules())
    assert ext_codes(report) == ["schemas.no-schema-for-type", "sections.no-declaration-for-type"]


@pytest.mark.parametrize("type_name", _TYPES)
def test_a_raw_skeleton_carries_no_render_breakage(tmp_path: Path, type_name: str) -> None:
    """Spec §7.5, asserted modulo `sections.unfilled`.

    Every required section here is `ownership: prose` with a TODO placeholder --
    a placeholder is not a valid end state, which is why `ownership: template`
    is wrong for this lane -- so a raw skeleton reports `sections.unfilled` by
    construction. What §7.5 is actually guarding is §3.2's angle-bracket trap
    and §3.3's wikilink ban, and both are asserted exactly.
    """
    body = render_skeleton(section_set().types[type_name])
    page = f"---\ntype: {type_name}\ntitle: A page\ndescription: A page.\n---\n\n{body}"
    bundle = build_bundle(tmp_path / "bundle", {f"lane/{type_name.lower()}": page})
    report = validate_with(bundle, full_ext_rules())

    assert report.by_code("render.angle-bracket") == ()
    assert report.by_code("render.wikilink") == ()
    # `health.uncited` is real but incidental here: a bundle holding only this
    # one page has no other concept's prose to cite it, regardless of skeleton
    # quality. `sections.unfilled` is expected, per the docstring above.
    assert set(ext_codes(report)) == {"sections.unfilled", "health.uncited"}, ext_codes(report)


def test_the_skeletons_are_the_reason_placeholders_avoid_bare_slots() -> None:
    """The declaration-level twin of the test above: nothing this lane writes
    carries a `<slot>` or a `[[wikilink]]` at all (spec §3.2, §3.3)."""
    for type_name in _TYPES:
        body = render_skeleton(section_set().types[type_name])
        assert "<" not in body
        assert "[[" not in body


def test_the_references_lane_does_not_collide_with_a_nested_work_artifact(tmp_path: Path) -> None:
    """Spec §9: `references/` is a bundle-root lane; `work-tracker-okf`'s
    `work/<slug>/references/` is nested. Confirmed against a bundle holding
    both rather than reasoned about."""
    lane_page = _filled_page("Reference", "CLI flags")
    artifact = "---\ntitle: Design spec\ncategory: work\n---\n\n# Design spec\n\nWords.\n"
    bundle = build_bundle(
        tmp_path / "bundle",
        {
            "references/cli-flags": lane_page,
            "work/2026-01-01-feature-x/references/01-design-spec": artifact,
        },
    )
    assert "references/cli-flags" in bundle.concepts
    assert "work/2026-01-01-feature-x/references/01-design-spec" in bundle.concepts

    report = validate_with(bundle, declaration_rules())
    # The artifact carries no `type`, so `section_rule` and `schema_rule` both
    # skip it -- there is nothing to dispatch on.
    assert ext_codes(report) == [], ext_codes(report)
