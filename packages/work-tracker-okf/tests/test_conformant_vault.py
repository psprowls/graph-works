from pathlib import Path

from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import load_sections
from okf_ext.tables import Column, TableSpec, read_section
from okf_io import load_bundle, validate
from work_helpers import CONFORMANT_ROOT, CONFORMANT_TODAY
from work_tracker_okf import IGNORE, load_items
from work_tracker_okf._rules import TOPICS
from work_tracker_okf.rules import lane_rules

_RELEASE = "work/release-path-native-cutover"
_EPIC = f"{_RELEASE}/children/epic-conformant-vault"
_FEATURE = f"{_EPIC}/children/feature-epic-feature-filing-writer"
_LEAF = f"{_FEATURE}/children/test-gap-cover-the-upsert"
_SPIKE = f"{_EPIC}/children/spike-path-layout-questions"
_ARCHIVED = f"{_EPIC}/children/_archive/feature-epic-feature-archived-child"

_ACTIVE = (
    _RELEASE,
    _EPIC,
    _FEATURE,
    _LEAF,
    _SPIKE,
    "work/bug-path-prefix-mismatch",
    "work/tech-debt-retire-the-sidecar",
)

_ARTIFACTS = (
    f"{_FEATURE}/references/01-design.md",
    f"{_FEATURE}/references/02-plan.md",
    f"{_FEATURE}/references/03-execute-transcript.jsonl",
    f"{_FEATURE}/references/evidence/decision.md",
    f"{_FEATURE}/references/nested/raw-notes.txt",
    f"{_ARCHIVED}/references/01-design.md",
)

_PLAN_SPEC = TableSpec(columns=(Column("action"), Column("done when"), Column("rationale")))


def test_the_item_pages_are_the_only_concepts(conformant_root: Path) -> None:
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    expected = set(_ACTIVE) | {_ARCHIVED}
    assert set(bundle.concepts) == expected


def test_every_artifact_is_still_a_member(conformant_root: Path) -> None:
    """`ignore=` declares "this is not a concept", not "this is not there"."""
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    for artifact in _ARTIFACTS:
        assert bundle.has_member(artifact), artifact


def test_every_source_resource_is_root_absolute_and_resolves(conformant_root: Path) -> None:
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    seen = 0
    for item in load_items(bundle):
        for source in item.sources:
            assert source.resource is not None
            assert source.resource.startswith("/"), (item.path, source.id)
            assert bundle.has_member(source.resource[1:]), (item.path, source.id)
            seen += 1
    assert seen == 5


def test_the_vault_validates_with_zero_errors(conformant_root: Path) -> None:
    """The property child 6's acceptance gate is built on. Both house rules are
    raised to `severity="error"` deliberately: §6 declines to squeak a finding
    past a zero-errors gate on the strength of `section_rule`'s `warn` default."""
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    report = validate(
        bundle,
        today=CONFORMANT_TODAY,
        extra_rules=(
            schema_rule(load_schemas(conformant_root / "schema"), severity="error"),
            section_rule(load_sections(conformant_root / "sections"), severity="error"),
        ),
    )
    assert [f"{f.code} {f.path}: {f.message}" for f in report.errors] == []


def test_the_vault_carries_only_the_ok_and_empty_plan_table_states(conformant_root: Path) -> None:
    """C2-J: this narrows the epic's written child-2 bullet. `malformed` and
    `missing` are non-conformant by construction — a vault carrying them cannot
    also carry the zero-errors property — so they move to child 5's set."""
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    states = {
        concept_id: read_section(document.body, "Plan", _PLAN_SPEC).state
        for concept_id, document in bundle.concepts.items()
    }
    assert set(states.values()) == {"ok", "empty"}, states
    assert states[_FEATURE] == "ok"
    assert states[_EPIC] == "empty"


def test_the_archived_child_still_belongs_to_its_active_parent(conformant_root: Path) -> None:
    items = load_items(load_bundle(conformant_root, ignore=IGNORE))
    epic = next(item for item in items if item.path == _EPIC)
    assert _ARCHIVED in epic.archived_child_paths
    assert epic.archived is False


def test_the_archived_item_is_the_symmetric_shape(conformant_root: Path) -> None:
    assert (conformant_root / f"{_ARCHIVED}.md").is_file()
    assert (conformant_root / _ARCHIVED / "references").is_dir()


def test_both_index_files_are_present_and_carry_no_frontmatter(conformant_root: Path) -> None:
    """P-3: `reserved.index-frontmatter` is an **error** for a non-root index, so
    a `title:` on either of these would sink the zero-errors property."""
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    assert set(bundle.indexes) >= {
        "",
        "work",
        "work/_archive",
        f"{_RELEASE}/children",
        f"{_EPIC}/children",
        f"{_EPIC}/children/_archive",
        f"{_FEATURE}/children",
    }
    assert bundle.indexes["work"].has_frontmatter is False
    assert bundle.indexes["work/_archive"].has_frontmatter is False


def test_the_index_files_name_every_direct_item_in_their_lane(conformant_root: Path) -> None:
    for item in load_items(load_bundle(conformant_root, ignore=IGNORE)):
        lane = Path(item.page_path).parent
        index = (conformant_root / lane / "index.md").read_text(encoding="utf-8")
        assert f"({Path(item.page_path).name})" in index, item.path


def test_no_page_uses_wikilink_form() -> None:
    """Authored from birth in the form `moves` can repair, which is what lets
    child 4 archive it. Read from the committed fixture, not the materialized
    copy, so the installed declarations are out of scope."""
    for page in CONFORMANT_ROOT.rglob("*.md"):
        assert "[[" not in page.read_text(encoding="utf-8"), page


def test_the_vault_ships_no_declarations_of_its_own() -> None:
    """C2-I: materialized by `init`, not committed — no drift between the fixture
    and the package assets."""
    assert not (CONFORMANT_ROOT / "schema").exists()
    assert not (CONFORMANT_ROOT / "sections").exists()


def test_all_seven_types_appear(conformant_root: Path) -> None:
    from work_tracker_okf.vocabulary import TYPES

    items = load_items(load_bundle(conformant_root, ignore=IGNORE))
    assert {item.type for item in items} == TYPES


def test_the_vault_validates_with_zero_errors_with_the_lane_rules(conformant_root: Path) -> None:
    """The property child 6's acceptance gate is built on, now including the lane
    catalog. `repo_root` stays `None`: the vault's `affects` entries name real
    repo paths, and pointing at them would couple the fixture to the live tree.
    The nonconformant golden is where those two codes are exercised."""
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    report = validate(
        bundle,
        today=CONFORMANT_TODAY,
        extra_rules=(
            schema_rule(load_schemas(conformant_root / "schema"), severity="error"),
            section_rule(load_sections(conformant_root / "sections"), severity="error"),
            *lane_rules(repo_root=None),
        ),
    )
    assert [f"{f.code} {f.path}: {f.message}" for f in report.errors] == []


def test_the_lane_warns_on_the_conformant_vault_are_exactly_the_three_expected(conformant_root: Path) -> None:
    """Design spec §7.3, verified item by item. These three are **correct**, not
    defects to design away:

    - `state.resolved-without-ref` on the resolved `Spike`, which has no `resolved_in`
    - `state.archive-eligible` on that same `Spike` — terminal, still under `work/`
    - `decisions.ledger-missing` on the executing `Feature`, whose fixture omits
      a second decision ledger intentionally

    Staleness fires on nothing: `CONFORMANT_TODAY` is well inside both thresholds
    for every item in the vault. If this set ever grows, re-read the fixture
    before loosening the assertion — the vault is the gate.
    """
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    report = validate(bundle, today=CONFORMANT_TODAY, extra_rules=lane_rules(repo_root=None))
    lane = {(f.code, f.path) for f in report.findings if f.code.split(".")[0] in TOPICS}
    spike = f"{_SPIKE}.md"
    assert lane == {
        ("state.resolved-without-ref", spike),
        ("state.archive-eligible", spike),
        ("decisions.ledger-missing", f"{_FEATURE}.md"),
    }


def test_the_in_progress_item_names_an_owner(conformant_root: Path) -> None:
    """The recorded edit, asserted from the projection rather than from the
    text, so a re-authored fixture cannot satisfy it by accident."""
    items = load_items(load_bundle(conformant_root, ignore=IGNORE))
    feature = next(item for item in items if item.path == _FEATURE)
    assert feature.work_status == "in-progress"
    assert feature.owner


def test_the_epics_ledger_is_a_member_but_not_a_concept(conformant_root: Path) -> None:
    """§3.1: the ledger lives under `references/`, so `IGNORE`'s existing
    `*/references/*` already covers it — it is present without being schema- or
    section-checked."""
    from work_tracker_okf.decisions import ledger_ref

    bundle = load_bundle(conformant_root, ignore=IGNORE)
    ref = ledger_ref(_EPIC)
    assert bundle.has_member(ref.rel)
    assert ref.rel.removesuffix(".md") not in bundle.concepts


def test_the_epics_ledger_parses_clean(conformant_root: Path) -> None:
    """The other half of the zero-errors property: a well-formed ledger present,
    not merely absent."""
    from work_tracker_okf.decisions import counts, ledger_ref, load

    parsed = load(ledger_ref(_EPIC).path(conformant_root))
    assert parsed.warnings == []
    assert counts(parsed.entries) == {
        "answered": 1,
        "assumed": 1,
        "open": 0,
        "superseded": 1,
        "invalid": 0,
        "total": 3,
    }
