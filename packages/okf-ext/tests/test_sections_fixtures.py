"""The committed corpus, walked once. Every code, and nothing else.

`nonconformant/`'s model, one layer up: the golden set is hand-written here,
so regenerating a fixture proves nothing on its own.
"""

from __future__ import annotations

import pytest
from ext_helpers import (
    SECTIONED,
    SECTIONS_DIR,
    SECTIONS_EXPECTED,
    WORK_BODIES,
    WORK_BODY_HEADINGS,
    sectioned_bundle,
)
from okf_ext.sections import CODES, DEFAULT_IGNORE, load_sections, render_skeleton, section_rule
from okf_io import load_bundle, validate


def walk():
    section_set = load_sections(SECTIONS_DIR)
    report = validate(sectioned_bundle(), today="2026-08-07", extra_rules=[section_rule(section_set)])
    return [f for f in report.findings if f.code.startswith("sections.")]


def test_the_walk_reports_exactly_the_reviewed_set():
    assert {(f.code, f.path) for f in walk()} == SECTIONS_EXPECTED


def test_every_code_is_emitted_by_this_one_bundle():
    """What makes `SECTIONS_EXPECTED` a contract test rather than a sample."""
    assert {f.code for f in walk()} == set(CODES)


def test_every_reported_code_is_a_member_of_codes():
    assert {f.code for f in walk()} <= set(CODES)


def test_the_default_ignore_keeps_the_declarations_out_of_the_bundle():
    """An ignored member is 'not a concept', not 'not there'."""
    bundle = load_bundle(SECTIONED, ignore=DEFAULT_IGNORE)
    assert any(member.startswith("sections/") for member in bundle.ignored)
    assert not any(member.startswith("sections/") for member in bundle.assets)


def test_declarations_need_not_live_inside_the_bundle_they_describe():
    """The path is explicit, so one shared set can measure many bundles."""
    assert load_sections(str(SECTIONS_DIR)).root == SECTIONS_DIR


def test_the_nine_work_io_kinds_load_as_one_set():
    """The 'cheaper proving ground' work-io §7.6 asked for. Downstream adoption
    is its own item, as `tables` did for its three consumer shapes -- this
    proves the format carries the shape, nothing more."""
    section_set = load_sections(WORK_BODIES)
    assert set(section_set.type_names) == set(WORK_BODY_HEADINGS)


def test_the_plan_table_is_declared_once_and_shared_by_all_nine():
    """The drift `{plan_table}` was invented to escape, made structurally
    impossible: there is one copy of the header in the whole directory."""
    section_set = load_sections(WORK_BODIES)
    table = section_set.fragments["plan-table"]
    for kind in WORK_BODY_HEADINGS:
        plan = next(s for s in section_set.types[kind].sections if s.heading == "Plan")
        assert plan.placeholder == table
        assert plan.seeded_is_complete is True


@pytest.mark.parametrize("kind", sorted(WORK_BODY_HEADINGS))
def test_each_kind_renders_back_to_its_template_shape(kind):
    section_set = load_sections(WORK_BODIES)
    rendered = render_skeleton(section_set.types[kind])
    headings = [line[3:] for line in rendered.split("\n") if line.startswith("## ")]
    assert headings == WORK_BODY_HEADINGS[kind]


@pytest.mark.parametrize("kind", sorted(WORK_BODY_HEADINGS))
def test_summary_and_plan_are_the_only_required_sections(kind):
    """Everything else is legitimately optional -- declared so a real heading
    is not 'extra', never scaffolded, never warned about."""
    declaration = load_sections(WORK_BODIES).types[kind]
    assert [s.heading for s in declaration.sections if s.required] == ["Summary", "Plan"]
