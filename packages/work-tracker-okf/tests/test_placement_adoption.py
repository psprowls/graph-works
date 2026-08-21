"""What this lane owns after adopting `okf_ext.placement`.

The rule's own behaviour is `packages/okf-ext/tests/test_placement_rule.py`'s.
Asserted here is the composition: that the seeded schemas really do declare
`work/`, that the narrowing keeps a shared `_schema/`'s entity types out of the
map, that both codes fire over one built bundle at `error`, and that the writer
honours the declaration it is handed.

Modelled on `packages/code-wiki-okf/tests/test_placement_adoption.py`, the
sibling adoption -- including its drift test, which exists because a schema that
loses its `x-okf-directory` would silently stop being checked with nothing else
in the suite noticing.
"""

from datetime import date
from pathlib import Path

from okf_ext.placement import CODES, TOPIC, placement_rule
from okf_ext.schemas import SchemaSet, declared_directories, load_schemas
from okf_io import load_bundle, validate
from typer.testing import CliRunner
from work_helpers import CONFORMANT_TODAY, write_item
from work_tracker_okf import paths
from work_tracker_okf.cli import app
from work_tracker_okf.compose import rule_set
from work_tracker_okf.filing import FilingSeed, plan_filing
from work_tracker_okf.init import install_bundle
from work_tracker_okf.items import IGNORE, WORK_DIR, placement_directories
from work_tracker_okf.resources import assets_root
from work_tracker_okf.vocabulary import TYPES

runner = CliRunner()

_TODAY = date(2026, 1, 1)

#: An entity type parked into the bundle's `_schema/` to stand in for the
#: composed workspace, where `workspace/init.py` points every lane installer at
#: one shared declarations directory and thirteen types land in it. `Package`
#: nests under `repositories/<repo>/` (ADR-0026), so its declared `packages/`
#: is a prefix no correctly-placed page carries.
_PACKAGE_SCHEMA = (
    '{\n  "$schema": "https://json-schema.org/draft/2020-12/schema",\n'
    '  "properties": { "type": { "const": "Package" } },\n'
    '  "x-okf-directory": "packages/"\n}\n'
)

_PACKAGE_PAGE = '---\ntype: Package\ntitle: "okf-io"\ndescription: "D"\n---\n\n## Purpose\n\nReal text.\n'


def _seed_schemas() -> SchemaSet:
    return load_schemas(Path(str(assets_root() / "_schema")))


def _stray_page(root: Path, *, resource: str | None = None) -> None:
    """A `Bug` page parked outside the work lane, where nothing can reach it."""
    line = "" if resource is None else f'resource: "{resource}"\n'
    (root / "concepts").mkdir(exist_ok=True)
    (root / "concepts" / "stray.md").write_text(
        f'---\ntype: Bug\ntitle: "Stray"\ndescription: "D"\n{line}---\n\n## Summary\n\nReal text.\n',
        encoding="utf-8",
    )


def _placement_codes(root: Path, today: date, *rules_) -> set[str]:
    report = validate(load_bundle(root, ignore=IGNORE), today=today, extra_rules=rules_ or rule_set(root))
    return {finding.code for finding in report.findings if finding.code.startswith(f"{TOPIC}.")}


# --- A. the declaration ------------------------------------------------------


def test_the_seeded_schemas_declare_the_work_lane() -> None:
    """The composed call is only as good as what the assets declare."""
    assert declared_directories(_seed_schemas()) == {type_name: "work/" for type_name in TYPES}


def test_the_declaration_and_the_constant_agree() -> None:
    """`WORK_DIR` stays the reader's answer, and this is what keeps it from
    drifting away from the annotation the writer and the validator now read."""
    assert set(placement_directories(_seed_schemas()).values()) == {f"{WORK_DIR}/"}


def test_placement_directories_narrows_to_this_lanes_own_types(tmp_path: Path) -> None:
    """An allow-list, not a deny-list: the shared `_schema/` of a composed
    workspace holds seven other types, and a deny-list keyed on names this lane
    happens to know today would let every future one through."""
    schema_dir = tmp_path / "_schema"
    schema_dir.mkdir()
    (schema_dir / "Package.schema.json").write_text(_PACKAGE_SCHEMA, encoding="utf-8")
    seeded = Path(str(assets_root() / "_schema" / "Bug.schema.json")).read_text(encoding="utf-8")
    (schema_dir / "Bug.schema.json").write_text(seeded, encoding="utf-8")

    schema_set = load_schemas(schema_dir)
    assert set(declared_directories(schema_set)) == {"Bug", "Package"}
    assert placement_directories(schema_set) == {"Bug": "work/"}


# --- the rule, composed ------------------------------------------------------


def test_both_codes_fire_over_one_built_bundle(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    _stray_page(root, resource="work:stray")
    # ...and a second page claiming the same resource, dropped by
    # find-by-resource and so unreachable by it.
    write_item(root, "dupe", 'type: Bug\nresource: "work:stray"\n')

    assert _placement_codes(root, _TODAY) == set(CODES)


def test_a_repo_scoped_entity_page_trips_no_placement_finding(tmp_path: Path) -> None:
    """The narrowing, end to end: a `Package` page correctly nested under
    `repositories/<repo>/packages/` must not be reported just because a shared
    `_schema/` declares `packages/` for it."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    (root / "_schema" / "Package.schema.json").write_text(_PACKAGE_SCHEMA, encoding="utf-8")
    page = root / "repositories" / "orca" / "packages" / "okf-io.md"
    page.parent.mkdir(parents=True)
    page.write_text(_PACKAGE_PAGE, encoding="utf-8")

    bundle = load_bundle(root, ignore=IGNORE)
    schema_set = load_schemas(root / "_schema")

    narrowed = validate(
        bundle,
        today=_TODAY,
        extra_rules=[placement_rule(placement_directories(schema_set), severity="error")],
    )
    assert [finding.code for finding in narrowed.findings if finding.code.startswith(f"{TOPIC}.")] == []

    # The regression the allow-list prevents: the raw map reports the page.
    raw = validate(
        bundle,
        today=_TODAY,
        extra_rules=[placement_rule(declared_directories(schema_set), severity="error")],
    )
    assert [finding.path for finding in raw.findings if finding.code == CODES[0]] == [
        "repositories/orca/packages/okf-io.md"
    ]


def test_the_archived_twin_is_not_reported_as_misplaced(tmp_path: Path) -> None:
    """No depth map: `work/_archive/<slug>` keeps the `work/` prefix, and
    `depth="exact"` would flag every archived item."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    archived = root / "work" / "_archive" / "2026-01-01-bug-archived.md"
    archived.parent.mkdir(parents=True)
    archived.write_text('---\ntype: Bug\ntitle: "T"\ndescription: "D"\n---\n\n## Summary\n\nText.\n', encoding="utf-8")

    assert _placement_codes(root, _TODAY) == set()


def test_the_composed_rule_set_reports_a_stray_page_as_an_error(conformant_root: Path) -> None:
    """`severity="error"`, for `rule_set`'s own reason: a work page outside
    `work/` is invisible to `load_items`, so it gets no routing, no rollup, no
    archive eligibility, and every lane rule silently skips it."""
    _stray_page(conformant_root)

    report = validate(
        load_bundle(conformant_root, ignore=IGNORE),
        today=CONFORMANT_TODAY,
        extra_rules=rule_set(conformant_root),
    )
    assert [finding.code for finding in report.errors if finding.code.startswith(f"{TOPIC}.")] == [CODES[0]]


# --- B. the writer -----------------------------------------------------------


def test_the_path_composers_default_to_the_hardcoded_lane() -> None:
    """`lane_dir=None` is what leaves ~40 existing call sites untouched."""
    assert paths.item_page("s").rel == "work/s.md"
    assert paths.item_page("s", archived=True).rel == "work/_archive/s.md"
    assert paths.references_dir("s").rel == "work/s/references"
    assert paths.decisions_ledger("s").rel == "work/s/references/00-decisions.md"
    assert paths.artifact_path("s", "design", "spec").rel == "work/s/references/01-design-spec.md"


def test_the_path_composers_honour_a_declared_lane_dir() -> None:
    assert paths.item_page("s", lane_dir="tickets").rel == "tickets/s.md"
    assert paths.references_dir("s", lane_dir="tickets").rel == "tickets/s/references"
    assert paths.decisions_ledger("s", lane_dir="tickets").rel == "tickets/s/references/00-decisions.md"
    assert paths.artifact_path("s", "design", "spec", lane_dir="tickets").rel == (
        "tickets/s/references/01-design-spec.md"
    )


def test_the_archived_twin_nests_under_the_declared_lane_dir() -> None:
    """`_archive` stays a literal: nothing declares `work/_archive/`."""
    assert paths.item_page("s", archived=True, lane_dir="tickets").rel == "tickets/_archive/s.md"
    assert paths.artifact_path("s", "plan", "plan", archived=True, lane_dir="tickets").rel == (
        "tickets/_archive/s/references/02-plan-plan.md"
    )


def test_plan_filing_places_the_page_in_the_declared_lane(tmp_path: Path, section_set) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    seed = FilingSeed(type="Bug", title="A declared bug", description="D", on=_TODAY)

    plan = plan_filing(root, (), seed, section_set, lane_dir="tickets")

    assert plan.refusal is None, plan.detail
    assert plan.target == root / "tickets" / f"{plan.slug}.md"
    assert plan.work_directory == root / "tickets" / plan.slug


def test_the_file_command_places_the_page_where_the_bundle_declares(tmp_path: Path) -> None:
    """The writer half, end to end: `cli.file` loads the bundle's own
    `SchemaSet` and files into the directory the seed's type declares."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    schema = root / "_schema" / "Bug.schema.json"
    schema.write_text(schema.read_text(encoding="utf-8").replace('"work/"', '"tickets/"'), encoding="utf-8")

    argv = ["file", str(root), "--type", "Bug", "--title", "A declared bug"]
    result = runner.invoke(app, [*argv, "--description", "D", "--today", "2026-01-01"])

    assert result.exit_code == 0, result.output
    slug = "2026-01-01-bug-a-declared-bug"
    assert (root / "tickets" / f"{slug}.md").is_file()
    assert not (root / "work" / f"{slug}.md").exists()
    assert (root / "tickets" / "index.md").is_file()
