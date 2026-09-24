"""Adoption of the code-wiki vocabulary-specific placement policy."""

from __future__ import annotations

import importlib.resources
from datetime import date
from pathlib import Path

import pytest
from code_wiki_okf.init import install_bundle
from code_wiki_okf.placement import is_entity_lane_page, placement_rule
from code_wiki_okf.sync.rule import sync_rule
from code_wiki_okf.sync.snapshot import SyncSnapshot
from okf_ext.schemas import declared_directories, load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import load_sections
from okf_ext.tags import load_vocabulary, vocabulary_rule
from okf_io import Rule, load_bundle, validate

_TODAY = date(2026, 1, 1)


def _seed_schemas():
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "schema"
    return load_schemas(str(assets))


def _five_rules(root: Path) -> list[Rule]:
    """The Task 4 validation composition, loaded from the seeded bundle.

    `SyncSnapshot.empty()` stands in for the graph walk: nothing here reads a
    graph. Command-level adoption follows in the later integration tasks.
    """
    schema_set = load_schemas(root / "schema")
    return [
        sync_rule(SyncSnapshot.empty()),
        schema_rule(schema_set),
        section_rule(load_sections(root / "sections")),
        vocabulary_rule(load_vocabulary(root / "tags.yaml")),
        placement_rule(severity="error"),
    ]


def test_the_seeded_schemas_pin_every_type_to_its_lane_segment() -> None:
    assert declared_directories(_seed_schemas()) == {
        "AgentPlugin": "agent-plugins/",
        "App": "apps/",
        "Dependency": "dependencies/",
        "File": "file-system/",
        "Package": "packages/",
        "Repository": "code-graph/",
        "TestSuite": "test-suites/",
    }


@pytest.mark.parametrize(
    "concept_id",
    [
        "code-graph/demo",
        "code-graph/demo/entities/packages/lib",
        "code-graph/demo/entities/apps/web",
        "code-graph/demo/entities/agent-plugins/reviewer",
        "code-graph/demo/entities/test-suites/unit",
        "code-graph/demo/entities/dependencies/pypi/httpx",
    ],
)
def test_entity_lane_ownership_recognizes_canonical_entity_ids(concept_id: str) -> None:
    assert is_entity_lane_page(concept_id)


@pytest.mark.parametrize(
    "concept_id",
    [
        "code-graph/demo/file-system/src/main.py",
        "code-graph/demo/extra",
        "dependencies/pypi/httpx",
        "code-graph/demo/entities/dependencies/httpx",
        "code-graph/demo/entities/dependencies/pypi/httpx/extra",
    ],
)
def test_entity_lane_ownership_refuses_file_and_noncanonical_ids(concept_id: str) -> None:
    assert not is_entity_lane_page(concept_id)


def test_both_codes_fire_over_one_built_bundle(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)

    # A Dependency page parked in the packages lane must disagree with the
    # exact ID computed from its resource.
    (root / "packages").mkdir()
    (root / "packages" / "httpx.md").write_text(
        '---\ntype: Dependency\ntitle: "httpx"\nresource: "dependency:acme/demo/pypi/httpx"\n---\n\n'
        "## Why we depend on this\n\nReal text.\n\n## Gotchas / workarounds\n\nReal text.\n",
        encoding="utf-8",
    )
    # ...and a second page claiming the same resource, dropped by
    # find-by-resource and so invisible to deletion.
    (root / "code-graph" / "demo" / "entities" / "dependencies" / "pypi").mkdir(parents=True)
    (root / "code-graph" / "demo" / "entities" / "dependencies" / "pypi" / "httpx.md").write_text(
        '---\ntype: Dependency\ntitle: "httpx"\nresource: "dependency:acme/demo/pypi/httpx"\n---\n\n'
        "## Why we depend on this\n\nReal text.\n\n## Gotchas / workarounds\n\nReal text.\n",
        encoding="utf-8",
    )

    report = validate(load_bundle(root), today=_TODAY, extra_rules=_five_rules(root))
    fired = {finding.code for finding in report.findings if finding.code.startswith("placement.")}
    assert fired == {"placement.directory-mismatch", "placement.duplicate-resource"}
    assert not report.ok  # this package passes `error`, so the report already fails


def test_a_nested_package_page_trips_no_placement_finding(tmp_path: Path) -> None:
    """A Package at the exact resource-derived ID has no mismatch."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    (root / "code-graph" / "repo-a" / "entities" / "packages").mkdir(parents=True)
    (root / "code-graph" / "repo-a" / "entities" / "packages" / "widgets.md").write_text(
        '---\ntype: Package\ntitle: "widgets"\nresource: "pkg:acme/repo-a/widgets"\n---\n\n'
        "## Purpose\n\nReal text.\n\n## Public API\n\nReal text.\n\n## Files\n\n_(none)_\n",
        encoding="utf-8",
    )

    report = validate(load_bundle(root), today=_TODAY, extra_rules=_five_rules(root))
    fired = {finding.code for finding in report.findings if finding.code.startswith("placement.")}
    assert "placement.directory-mismatch" not in fired


def test_the_seeded_bundle_reports_zero_errors_under_all_five_rules(tmp_path: Path) -> None:
    """Acceptance §7.5. Both `placement.*` codes are `error` here, so this
    asserts `report.ok` rather than only the absence of `placement.*` -- a
    regression in either would surface."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    report = validate(load_bundle(root), today=_TODAY, extra_rules=_five_rules(root))
    assert report.ok, report.errors


def test_the_packaged_assets_and_the_seeded_bundle_agree(tmp_path: Path) -> None:
    """`_five_rules` reads the bundle's own copies; this proves those copies
    are the package's, so a future asset edit cannot pass the test above while
    shipping something else."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    assets = importlib.resources.files("code_wiki_okf") / "assets"
    for relative in ("schema/Dependency.schema.json", "sections/Package.yaml", "sections/File.yaml"):
        assert (root / relative).read_text(encoding="utf-8") == (assets / relative).read_text(encoding="utf-8")
