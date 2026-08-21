"""What this package still owns after the rule moved to tier 2.

The rule's own behaviour is `packages/okf-ext/tests/test_placement_rule.py`'s.
What is asserted here is the composition: that the seeded declarations really
do declare every entity lane, that the depth map covers the one pair no
annotation can separate, and that `install_bundle`'s bundle still reports zero
errors under the full five-rule set `cli.py:validate` builds.
"""

from __future__ import annotations

import importlib.resources
from datetime import date
from pathlib import Path

from code_wiki_okf.entities.lanes import ENTITY_DEPTH, placement_directories
from code_wiki_okf.init import install_bundle
from code_wiki_okf.sync.rule import sync_rule
from code_wiki_okf.sync.snapshot import SyncSnapshot
from okf_ext.placement import CODES, TOPIC, placement_rule
from okf_ext.schemas import declared_directories, load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import load_sections
from okf_ext.tags import load_vocabulary, vocabulary_rule
from okf_io import Rule, load_bundle, validate

_TODAY = date(2026, 1, 1)


def _seed_schemas():
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_schema"
    return load_schemas(str(assets))


def _five_rules(root: Path) -> list[Rule]:
    """The exact list `cli.py:validate` builds, loaded from the bundle's own
    seeded declarations rather than the package's asset directory -- child 1's
    `test_done_when.py` pattern, and the one that actually proves the *bundle*
    is self-describing. `SyncSnapshot.empty()` stands in for the graph walk:
    nothing here reads a graph.
    """
    schema_set = load_schemas(root / "_schema")
    return [
        sync_rule(SyncSnapshot.empty()),
        schema_rule(schema_set),
        section_rule(load_sections(root / "_sections")),
        vocabulary_rule(load_vocabulary(root / "_tags.yaml")),
        placement_rule(placement_directories(schema_set), depth=ENTITY_DEPTH, severity="error"),
    ]


def test_the_seeded_schemas_declare_every_entity_lane() -> None:
    """The composed call is only as good as what the assets declare: a schema
    that loses its `x-okf-directory` would silently stop being checked, with
    nothing else in the suite noticing."""
    assert declared_directories(_seed_schemas()) == {
        "AgentPlugin": "agent-plugins/",
        "App": "apps/",
        "Dependency": "dependencies/",
        "File": "repositories/",
        "Package": "packages/",
        "Repository": "repositories/",
        "TestSuite": "test-suites/",
    }


def test_the_depth_map_covers_the_one_pair_annotations_cannot_separate() -> None:
    """`Repository` and `File` both declare `repositories/`. Every other type
    owns its directory outright and needs no entry."""
    declared = declared_directories(_seed_schemas())
    shared = {name for name, directory in declared.items() if list(declared.values()).count(directory) > 1}
    assert shared == set(ENTITY_DEPTH)
    assert dict(ENTITY_DEPTH) == {"Repository": "exact", "File": "nested"}


def test_both_codes_fire_over_one_built_bundle(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)

    # A `Dependency` page parked in the packages lane: `directory-mismatch`
    # still fires for Dependency -- narrowing (§5) drops only the four
    # repo-scoped types (Package/App/TestSuite/AgentPlugin), and Dependency
    # stays global and fully prefix-checkable.
    (root / "packages").mkdir()
    (root / "packages" / "httpx.md").write_text(
        '---\ntype: Dependency\ntitle: "httpx"\nresource: "dependency:pypi/httpx"\n---\n\n'
        "## Why we depend on this\n\nReal text.\n\n## Gotchas / workarounds\n\nReal text.\n",
        encoding="utf-8",
    )
    # ...and a second page claiming the same resource, dropped by
    # find-by-resource and so invisible to deletion.
    (root / "dependencies").mkdir()
    (root / "dependencies" / "httpx.md").write_text(
        '---\ntype: Dependency\ntitle: "httpx"\nresource: "dependency:pypi/httpx"\n---\n\n'
        "## Why we depend on this\n\nReal text.\n\n## Gotchas / workarounds\n\nReal text.\n",
        encoding="utf-8",
    )

    report = validate(load_bundle(root), today=_TODAY, extra_rules=_five_rules(root))
    fired = {finding.code for finding in report.findings if finding.code.startswith(f"{TOPIC}.")}
    assert fired == set(CODES)
    assert not report.ok  # this package passes `error`, so the report already fails


def test_a_nested_package_page_trips_no_placement_finding(tmp_path: Path) -> None:
    """The narrowing this task makes: a Package page correctly nested under
    `repositories/<repo>/packages/` must not trip `placement.directory-mismatch`
    even though its concept id no longer starts with the schema's declared
    `packages/` directory."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    (root / "repositories" / "repo-a" / "packages").mkdir(parents=True)
    (root / "repositories" / "repo-a" / "packages" / "widgets.md").write_text(
        '---\ntype: Package\ntitle: "widgets"\nresource: "pkg:acme/repo-a/widgets"\n---\n\n'
        "## Purpose\n\nReal text.\n\n## Public API\n\nReal text.\n\n## Files\n\n_(none)_\n",
        encoding="utf-8",
    )

    report = validate(load_bundle(root), today=_TODAY, extra_rules=_five_rules(root))
    fired = {finding.code for finding in report.findings if finding.code.startswith(f"{TOPIC}.")}
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
    for relative in ("_schema/Dependency.schema.json", "_sections/Package.yaml", "_sections/File.yaml"):
        assert (root / relative).read_text(encoding="utf-8") == (assets / relative).read_text(encoding="utf-8")
