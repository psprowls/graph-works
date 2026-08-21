"""The fixture corpus itself: 13 files, still old-dialect, still byte-exact."""

import re
from pathlib import Path

import pytest
from doc_wiki_okf.proposals.filing import plan_file
from doc_wiki_okf.proposals.migrate import apply, migrate_and_move, plan_migrate
from migrate_helpers import IGNORE, fixture_bundle, fixture_bytes, fixture_member, fixture_paths, reload_bundle
from okf_ext import moves
from okf_ext.proposals import list_proposals
from okf_io import Document
from proposal_helpers import AT, BY, build_bundle, lanes, source


@pytest.fixture(params=fixture_paths(), ids=lambda path: path.name)
def fixture_path(request):
    return request.param


def test_the_corpus_is_the_thirteen_live_proposals() -> None:
    members = [fixture_member(path) for path in fixture_paths()]
    assert len(members) == 13
    assert sum(1 for member in members if "/_archive/" in member) == 2


def test_every_fixture_is_old_dialect(fixture_path) -> None:
    """The trigger, asserted over the corpus: `target_slug` present and
    `type` absent. A fixture that drifts out of the old dialect stops proving
    anything about a migrator, so it fails here rather than silently passing
    as a skip."""
    data = Document.parse(fixture_path.read_bytes().decode("utf-8")).fm_data()
    assert "target_slug" in data
    assert "type" not in data


def test_every_fixture_parses(fixture_path) -> None:
    assert Document.parse(fixture_path.read_bytes().decode("utf-8")).parse_error is None


def test_the_bundle_carries_all_thirteen(tmp_path) -> None:
    bundle = fixture_bundle(tmp_path / "b")
    for member in fixture_bytes():
        assert bundle.has_member(member), member


def test_copying_into_a_bundle_is_byte_exact(tmp_path) -> None:
    root = tmp_path / "b"
    fixture_bundle(root)
    for member, original in fixture_bytes().items():
        assert (root / member).read_bytes() == original, member


# --- the acceptance criteria over the corpus ---------------------------------

#: The full destination table. The two `_archive/` rows are the point:
#: `page_status: created` distinguishes them either way, but archiving is a
#: placement convention the vault already uses and a migrator has no business
#: undoing it.
DESTINATIONS = {
    "proposals/adr-bulk-write-staging-protocol.md": "proposals/adrs-bulk-write-staging-protocol.md",
    "proposals/adr-capability-contract-answering-vs-reporting.md": (
        "proposals/adrs-capability-contract-answering-vs-reporting.md"
    ),
    "proposals/adr-extension-layer-tier-model-and-packaging.md": (
        "proposals/adrs-extension-layer-tier-model-and-packaging.md"
    ),
    "proposals/adr-generated-bundle-and-curated-vault-coexistence.md": (
        "proposals/adrs-generated-bundle-and-curated-vault-coexistence.md"
    ),
    "proposals/adr-migration-refusal-policy.md": "proposals/adrs-migration-refusal-policy.md",
    "proposals/adr-pages-identified-by-resource-not-path.md": (
        "proposals/adrs-pages-identified-by-resource-not-path.md"
    ),
    "proposals/adr-provenance-keys-are-not-drift.md": "proposals/adrs-provenance-keys-are-not-drift.md",
    "proposals/adr-structural-warn-findings-on-machine-generated-bundles.md": (
        "proposals/adrs-structural-warn-findings-on-machine-generated-bundles.md"
    ),
    "proposals/adr-tier-2-dependency-policy.md": "proposals/adrs-tier-2-dependency-policy.md",
    "proposals/adr-tier-2-writers-return-plans-not-dry-run-flags.md": (
        "proposals/adrs-tier-2-writers-return-plans-not-dry-run-flags.md"
    ),
    "proposals/concept-one-declaration-two-readers.md": "proposals/concepts-one-declaration-two-readers.md",
    "proposals/_archive/adr-quality-gates-enforced-locally-ci-deferred.md": (
        "proposals/_archive/adrs-quality-gates-enforced-locally-ci-deferred.md"
    ),
    "proposals/_archive/concept-okf-io-core-invariants.md": "proposals/_archive/concepts-okf-io-core-invariants.md",
}


def test_no_live_proposal_is_refused(tmp_path) -> None:
    plan = plan_migrate(fixture_bundle(tmp_path / "b"), by=BY, at=AT)
    assert plan.ok, [(r.member, r.kind, r.detail) for r in plan.refusals]
    assert len(plan.writes) == 13


def test_every_destination_is_the_spec_table(tmp_path) -> None:
    """Spec section 6.5."""
    plan = plan_migrate(fixture_bundle(tmp_path / "b"), by=BY, at=AT)
    assert dict(plan.placements) == DESTINATIONS


def test_plan_move_many_accepts_the_placements(tmp_path) -> None:
    """Spec section 6.5, second half: the mapping the migrator computes is one
    `okf_ext.moves` will actually perform."""
    root = tmp_path / "b"
    bundle = fixture_bundle(root)
    plan = plan_migrate(bundle, by=BY, at=AT)

    assert apply(bundle, plan).ok
    move_plan = moves.plan_move_many(reload_bundle(root), dict(plan.placements))
    assert move_plan.ok, [(r.path, r.kind, r.detail) for r in move_plan.refusals]


def test_all_thirteen_round_trip(tmp_path) -> None:
    """Spec section 6.1: every fixture migrates to a document the capability
    reads back cleanly, carrying what the original carried."""
    root = tmp_path / "b"
    before = {}
    for concept_id, document in fixture_bundle(root).concepts.items():
        member = f"{concept_id}.md"
        if member in DESTINATIONS:
            before[DESTINATIONS[member]] = document.fm_data()

    outcome = migrate_and_move(root, by=BY, at=AT, ignore=IGNORE)
    assert outcome.ok

    after = {proposal.member: proposal for proposal in list_proposals(reload_bundle(root))}
    assert set(after) == set(DESTINATIONS.values())

    for member, original in before.items():
        migrated = after[member]
        assert migrated.malformed is None, member
        assert migrated.title == original["title"], member
        assert migrated.page_status == original["status"], member
        assert len(migrated.sources) == len(original["origins"]), member
        for source_entry, origin in zip(migrated.sources, original["origins"], strict=True):
            assert source_entry["resource"].startswith(origin["ref"]), member
            assert source_entry.get("rationale") == origin.get("rationale"), member
            assert source_entry.get("evidence") == origin.get("evidence"), member
            assert "source" not in source_entry, member


def test_every_body_is_byte_identical(tmp_path) -> None:
    """Spec section 6.2, asserted against the fixture **bytes**, not inferred
    from the round trip. The rewriter never touches a body, so this is a
    property of the mechanism rather than a promise anyone has to keep."""
    root = tmp_path / "b"
    originals = {
        DESTINATIONS[member]: Document.parse(raw.decode("utf-8")).body for member, raw in fixture_bytes().items()
    }
    fixture_bundle(root)
    assert migrate_and_move(root, by=BY, at=AT, ignore=IGNORE).ok

    for member, body in originals.items():
        landed = Document.parse((root / member).read_bytes().decode("utf-8"))
        assert landed.body == body, member


def test_re_running_is_an_empty_plan(tmp_path) -> None:
    """Spec section 6.3."""
    root = tmp_path / "b"
    fixture_bundle(root)
    assert migrate_and_move(root, by=BY, at=AT, ignore=IGNORE).ok

    again = plan_migrate(reload_bundle(root), by=BY, at=AT)
    assert again.ok
    assert again.is_empty


def test_filing_and_migrating_agree_on_the_path(tmp_path) -> None:
    """Spec section 6.6: `proposal_path()`'s two callers resolve the same target to
    the same file, which is the whole reason it became public."""
    filed = plan_file(
        build_bundle(tmp_path / "filed"),
        lanes(),
        lane="adr",
        title="Bulk Write Staging Protocol",
        description="",
        source=source("src-a", "sources/a.md"),
        by=BY,
        at=AT,
    )
    migrated = plan_migrate(fixture_bundle(tmp_path / "migrated"), by=BY, at=AT)

    assert filed.writes[0].member == "proposals/adrs-bulk-write-staging-protocol.md"
    assert migrated.placements["proposals/adr-bulk-write-staging-protocol.md"] == filed.writes[0].member


#: An actual read of the env var -- `os.environ[...]` / `.get(...)` / `os.getenv(...)`
#: naming it -- as opposed to a docstring merely documenting where a fixture's
#: content originated (`migrate_helpers.py` and `proposal_helpers.py` both do,
#: legitimately, and this file's own assertions necessarily name the string
#: too). A plain substring check would fail on all three for reasons that have
#: nothing to do with the suite touching the live workspace.
_ENV_READ = re.compile(
    r'os\.environ(\[|\.get\()\s*["\']GRAPH_WIKI_WORKSPACE["\']|os\.getenv\(\s*["\']GRAPH_WIKI_WORKSPACE["\']'
)


def test_nothing_reads_the_live_workspace() -> None:
    """Spec section 6.7: this child builds the rewriter and proves it on
    fixtures. Running it against `wiki/` is a separate, later, cutover epic's
    job -- this suite must never touch the live vault."""
    suite = Path(__file__).resolve().parent
    for path in sorted(suite.glob("*.py")):
        assert not _ENV_READ.search(path.read_text(encoding="utf-8")), path.name
    source_root = suite.parent / "src" / "doc_wiki_okf"
    for path in sorted(source_root.rglob("*.py")):
        assert not _ENV_READ.search(path.read_text(encoding="utf-8")), path.name
