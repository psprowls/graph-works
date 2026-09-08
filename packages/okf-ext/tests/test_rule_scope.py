"""`RuleContext.scope` narrows the three per-document okf-ext rules and
deliberately does not narrow the cross-document ones."""

from __future__ import annotations

from datetime import date

import pytest
from ext_helpers import write, write_bundle
from okf_ext.placement.rule import placement_rule
from okf_ext.render.rule import render_rule
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import load_sections, section_rule
from okf_io import load_bundle
from okf_io.links import build
from okf_io.validate import RuleContext

# Reused verbatim rather than re-derived: both modules already build a
# `SectionSet` / `SchemaSet` the way `okf_ext.sections` / `okf_ext.schemas`
# expect, and this suite has no business inventing a second way to load one.
from test_schemas_rule import BASE as SCHEMA_BASE
from test_schemas_rule import METRIC as SCHEMA_METRIC
from test_sections_rule import DECLARATIONS as SECTION_DECLARATIONS
from test_sections_rule import doc as section_doc

TODAY = date(2026, 8, 3)

_A = """---
type: concept
title: Alpha
resource: /same/resource.py
---

Body with a bad [[wikilink-a]] and a stray < angle.
"""

_B = """---
type: concept
title: Bravo
resource: /same/resource.py
---

Body with a bad [[wikilink-b]] and a stray < angle.
"""


def _context(bundle, scope, *, links=None):
    return RuleContext(bundle=bundle, links=links if links is not None else build(bundle), today=TODAY, scope=scope)


@pytest.fixture
def two_member_bundle(tmp_path):
    return write_bundle(tmp_path, {"a.md": _A, "b.md": _B})


def test_render_rule_reports_both_members_without_a_scope(two_member_bundle):
    findings = list(render_rule()(_context(two_member_bundle, None)))
    assert {f.path for f in findings} == {"a.md", "b.md"}


def test_render_rule_reports_only_the_scoped_member(two_member_bundle):
    findings = list(render_rule()(_context(two_member_bundle, frozenset({"a.md"}))))
    assert {f.path for f in findings} == {"a.md"}


def test_render_rule_with_an_empty_scope_reports_nothing(two_member_bundle):
    assert not list(render_rule()(_context(two_member_bundle, frozenset())))


def test_placement_rule_is_deliberately_not_scoped(two_member_bundle):
    """`placement.resource-collision` is cross-document: both pages claim
    `/same/resource.py`. Scoping one member must not hide the collision, or the
    error-severity gate in `compose.rule_set` would stop seeing collisions a
    mutation introduces."""
    scoped = list(placement_rule({}, severity="error")(_context(two_member_bundle, frozenset({"a.md"}))))
    unscoped = list(placement_rule({}, severity="error")(_context(two_member_bundle, None)))
    assert scoped == unscoped


# --- section_rule: same scoped/unscoped shape, over a `SectionSet` ----------


@pytest.fixture
def two_member_section_bundle(tmp_path):
    """Two concepts, each missing the declaration's required `Plan` section --
    copied from `test_sections_rule.build`, the package's own fixture-
    construction pattern, rather than a second way to load a `SectionSet`."""
    root = tmp_path / "kb"
    sections_dir = root / "sections"
    sections_dir.mkdir(parents=True)
    write(sections_dir / "Feature.yaml", SECTION_DECLARATIONS)
    bundle = write_bundle(
        root,
        {
            "a.md": section_doc("## Summary\n\nReal.\n"),
            "b.md": section_doc("## Summary\n\nReal.\n"),
        },
        ignore=("sections/*",),
    )
    return bundle, load_sections(sections_dir)


def test_section_rule_reports_both_members_without_a_scope(two_member_section_bundle):
    bundle, section_set = two_member_section_bundle
    findings = [f for f in section_rule(section_set)(_context(bundle, None)) if f.code == "sections.missing"]
    assert {f.path for f in findings} == {"a.md", "b.md"}


def test_section_rule_reports_only_the_scoped_member(two_member_section_bundle):
    bundle, section_set = two_member_section_bundle
    findings = [
        f for f in section_rule(section_set)(_context(bundle, frozenset({"a.md"}))) if f.code == "sections.missing"
    ]
    assert {f.path for f in findings} == {"a.md"}


def test_section_rule_with_an_empty_scope_reports_nothing(two_member_section_bundle):
    bundle, section_set = two_member_section_bundle
    assert not list(section_rule(section_set)(_context(bundle, frozenset())))


# --- schema_rule: same scoped/unscoped shape, over a `SchemaSet` -----------

_INVALID_METRIC = "---\ntype: Metric\ntitle: Orders\nowner:\n  name: 42\n---\n\n# Orders\n"


@pytest.fixture
def two_member_schema_bundle(tmp_path):
    """Two concepts, each violating the schema's `owner.name` type -- copied
    from `test_schemas_rule.build`, the package's own fixture-construction
    pattern, rather than a second way to load a `SchemaSet`."""
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    write(schema_dir / "_base.schema.yaml", SCHEMA_BASE)
    write(schema_dir / "Metric.schema.yaml", SCHEMA_METRIC)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    write(bundle_dir / "a.md", _INVALID_METRIC)
    write(bundle_dir / "b.md", _INVALID_METRIC)
    return load_bundle(bundle_dir), load_schemas(schema_dir)


def test_schema_rule_reports_both_members_without_a_scope(two_member_schema_bundle):
    bundle, schema_set = two_member_schema_bundle
    findings = [f for f in schema_rule(schema_set)(_context(bundle, None)) if f.code == "schemas.invalid"]
    assert {f.path for f in findings} == {"a.md", "b.md"}


def test_schema_rule_reports_only_the_scoped_member(two_member_schema_bundle):
    bundle, schema_set = two_member_schema_bundle
    findings = [
        f for f in schema_rule(schema_set)(_context(bundle, frozenset({"a.md"}))) if f.code == "schemas.invalid"
    ]
    assert {f.path for f in findings} == {"a.md"}


def test_schema_rule_with_an_empty_scope_reports_nothing(two_member_schema_bundle):
    bundle, schema_set = two_member_schema_bundle
    assert not list(schema_rule(schema_set)(_context(bundle, frozenset())))
