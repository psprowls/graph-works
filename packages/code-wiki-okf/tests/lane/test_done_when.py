"""This child's done-when, verbatim from the design spec §1:

- both declared codes fire over a bundle built in `tmp_path`,
- the catalog test asserts every code's prefix equals the module name,
- and `install_bundle`'s seeded bundle still reports zero errors with
  `lane_rule` added to the existing four.

The last one is the property §3.1 could plausibly have broken -- it made
thirteen previously-optional headings required -- so it is asserted with the
full five-rule set, not with `lane_rule` alone.
"""

from __future__ import annotations

import importlib.resources
from datetime import date
from pathlib import Path

from code_wiki_okf.init import install_bundle
from code_wiki_okf.lane.rule import CODES, TOPIC, lane_rule
from code_wiki_okf.sync.rule import sync_rule
from code_wiki_okf.sync.snapshot import SyncSnapshot
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import load_sections
from okf_ext.tags import load_vocabulary, vocabulary_rule
from okf_io import Rule, load_bundle, validate

_TODAY = date(2026, 1, 1)


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
        lane_rule(schema_set),
    ]


def test_every_declared_code_has_the_module_name_as_its_prefix() -> None:
    """okf-io's `test_catalog.py` invariant, applied mechanically to the one
    module: the module name IS the code prefix."""
    assert lane_rule.__module__ == f"code_wiki_okf.{TOPIC}.rule"
    assert all(code.split(".", 1)[0] == TOPIC for code in CODES)
    assert len(set(CODES)) == len(CODES)


def test_both_codes_fire_over_one_built_bundle(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)

    # A `Package` page parked in the dependency lane: outside `prune_lane`'s
    # `packages/` scope forever, while sync keeps writing `packages/widgets.md`.
    (root / "dependencies").mkdir()
    (root / "dependencies" / "widgets.md").write_text(
        '---\ntype: Package\ntitle: "widgets"\nresource: "pkg:acme/repo-a/widgets"\n---\n\n'
        "## Purpose\n\nReal text.\n\n## Public API\n\nReal text.\n\n## Files\n\n_(none)_\n",
        encoding="utf-8",
    )
    # ...and a second page claiming the same resource, dropped by
    # find-by-resource and so invisible to deletion.
    (root / "packages").mkdir()
    (root / "packages" / "widgets.md").write_text(
        '---\ntype: Package\ntitle: "widgets"\nresource: "pkg:acme/repo-a/widgets"\n---\n\n'
        "## Purpose\n\nReal text.\n\n## Public API\n\nReal text.\n\n## Files\n\n_(none)_\n",
        encoding="utf-8",
    )

    report = validate(load_bundle(root), today=_TODAY, extra_rules=_five_rules(root))
    fired = {finding.code for finding in report.findings if finding.code.startswith(f"{TOPIC}.")}
    assert fired == set(CODES)
    assert not report.ok  # both codes are `error`, so the report already fails


def test_the_seeded_bundle_reports_zero_errors_under_all_five_rules(tmp_path: Path) -> None:
    """The property §3.1 could plausibly have broken. Both `lane.*` codes are
    `error`, so this asserts `report.ok` rather than only the absence of
    `lane.*` -- a regression in either would surface here.
    """
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
