"""Synthetic bundles for the Diátaxis lane, and the rule sets to judge them by.

Named `diataxis_helpers` rather than `helpers`: the root `pyproject.toml` warns
that a duplicate plain module basename across test directories collides
silently, and the prefix costs nothing.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from okf_ext.health import health_rule
from okf_ext.render import render_rule
from okf_ext.schemas import SchemaSet, load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import SectionSet, load_sections
from okf_io import Bundle, Report, Rule, load_bundle
from okf_io import validate as okf_validate

TODAY = date(2026, 8, 12)

#: The okf-ext topics this suite judges by. A finding outside these belongs to
#: okf-io's own catalog and is not what any of these tests is about.
EXT_TOPICS = ("schemas.", "sections.", "render.", "health.")


def schema_set() -> SchemaSet:
    return load_schemas(str(importlib.resources.files("doc_wiki_okf") / "assets" / "_schema"))


def section_set() -> SectionSet:
    return load_sections(str(importlib.resources.files("doc_wiki_okf") / "assets" / "_sections"))


def declaration_rules() -> tuple[Rule, ...]:
    """The two rules spec §7.3 and §7.4 measure against."""
    return (schema_rule(schema_set()), section_rule(section_set()))


def full_ext_rules() -> tuple[Rule, ...]:
    """Every okf-ext rule that reads a document, for spec §7.5.

    `tags.vocabulary_rule` is absent: it needs a `_tags.yaml` this lane does not
    ship, and a bundle without one has no vocabulary to violate.
    """
    return (*declaration_rules(), render_rule(), health_rule())


def build_bundle(root: Path, pages: Mapping[str, str], *, index: bool = True) -> Bundle:
    """Write *pages* (concept id -> full page text) under *root* and load it."""
    root.mkdir(parents=True, exist_ok=True)
    if index:
        (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    for concept_id, text in pages.items():
        target = root / f"{concept_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return load_bundle(root)


def ext_codes(report: Report) -> list[str]:
    """Every okf-ext finding code in *report*, sorted, duplicates kept."""
    return sorted(finding.code for finding in report.findings if finding.code.startswith(EXT_TOPICS))


def validate_with(bundle: Bundle, rules: tuple[Rule, ...]) -> Report:
    return okf_validate(bundle, today=TODAY, extra_rules=list(rules))
