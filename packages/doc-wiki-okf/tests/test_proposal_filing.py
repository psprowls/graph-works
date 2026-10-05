"""Filing: the pool resolves the target, the capability owns the merge."""

import unicodedata

import pytest
from doc_wiki_okf.proposals.filing import plan_file
from doc_wiki_okf.proposals.pool import PoolError
from okf_ext.proposals import apply
from proposal_helpers import AT, BY, build_bundle, pool, source


def _file(bundle, *, title="Bulk Write Staging Protocol", entry=None, type_name="Adr"):
    return plan_file(
        bundle,
        pool(),
        type_name=type_name,
        title=title,
        description="Two sources argue for one page.",
        source=entry or source("src-a", "sources/2026-08-spec.md", rationale="It settles it."),
        by=BY,
        at=AT,
    )


def test_a_first_filing_plans_one_create_at_the_undated_target(tmp_path) -> None:
    plan = _file(build_bundle(tmp_path / "b"))
    assert plan.ok
    assert plan.target == "adrs/bulk-write-staging-protocol.md"
    assert [(write.member, write.mode) for write in plan.writes] == [
        ("proposals/adrs-bulk-write-staging-protocol.md", "create")
    ]
    assert "## Suggested Action" in plan.writes[0].text
    assert "Create new Adr page `adrs/bulk-write-staging-protocol.md`." in plan.writes[0].text


def test_planning_writes_nothing(tmp_path) -> None:
    root = tmp_path / "b"
    _file(build_bundle(root))
    assert not (root / "proposals").exists()


def test_a_second_source_merges_and_the_body_names_both(tmp_path) -> None:
    root = tmp_path / "b"
    bundle = build_bundle(root)
    apply(bundle, _file(bundle))

    reloaded = build_bundle(root)
    plan = _file(
        reloaded,
        entry=source("src-b", "sources/2026-08-other.md", rationale="A second argument.", title="The other"),
    )
    assert plan.ok
    assert [write.mode for write in plan.writes] == ["update"]
    body = plan.writes[0].body
    assert "It settles it." in body
    assert "A second argument." in body


def test_refiling_an_identical_source_plans_nothing(tmp_path) -> None:
    """Idempotence surfaces as an empty plan -- the capability's property,
    inherited unchanged."""
    root = tmp_path / "b"
    bundle = build_bundle(root)
    apply(bundle, _file(bundle))

    plan = _file(build_bundle(root))
    assert plan.ok
    assert plan.is_empty


def test_an_unknown_type_raises(tmp_path) -> None:
    with pytest.raises(KeyError):
        _file(build_bundle(tmp_path / "b"), type_name="concept")


def test_an_existing_target_files_in_update_mode(tmp_path) -> None:
    root = tmp_path / "b"
    page = "---\ntype: Reference\ntitle: Flags\n---\n\n# Flags\n"
    bundle = build_bundle(root, {"docs/reference/flags": page})
    plan = plan_file(
        bundle,
        pool(),
        type_name="Reference",
        title="Flags",
        description="",
        source=source("src-a", "sources/x.md"),
        by=BY,
        at=AT,
    )
    assert "Update existing Reference page `docs/reference/flags.md`." in plan.writes[0].text


def test_a_non_ascii_target_files_in_update_mode_against_the_raw_disk_id(tmp_path, monkeypatch) -> None:
    """Defensive per the ticket: `target_for`'s slug is ASCII-only today
    (`SLUG_RE`), so this can only be exercised by monkeypatching `slugify` --
    see work/tech-debt-has-member-callers-raw-id."""
    nfd = unicodedata.normalize("NFD", "café")
    nfc = unicodedata.normalize("NFC", "café")
    monkeypatch.setattr("doc_wiki_okf.proposals.pool.slugify", lambda title: nfc)
    root = tmp_path / "b"
    page = "---\ntype: Reference\ntitle: Café\n---\n\n# Café\n"
    bundle = build_bundle(root, {f"docs/reference/{nfd}": page})

    plan = plan_file(
        bundle,
        pool(),
        type_name="Reference",
        title="Café",
        description="",
        source=source("src-a", "sources/x.md"),
        by=BY,
        at=AT,
    )

    assert plan.target == f"docs/reference/{nfd}.md"
    assert f"Update existing Reference page `docs/reference/{nfd}.md`." in plan.writes[0].text


def test_filing_records_the_type_in_target_type(tmp_path) -> None:
    bundle = build_bundle(tmp_path / "b")
    plan = plan_file(
        bundle,
        pool(),
        type_name="explanation",
        title="Byte Fidelity",
        description="d",
        source=source("s1", "sources/2026-08-spec.md"),
        by=BY,
        at=AT,
    )
    (write,) = plan.writes
    assert write.member == "proposals/docs-explanations-byte-fidelity.md"
    assert "target_type: Explanation" in write.text


@pytest.mark.parametrize(
    ("asked", "fragment"), [("Source", "x-okf-accept-proposals: false"), ("explanation-ish", "expected one of")]
)
def test_filing_into_a_type_outside_the_pool_raises_naming_why(tmp_path, asked, fragment) -> None:
    bundle = build_bundle(tmp_path / "b")
    with pytest.raises(PoolError, match=fragment):
        plan_file(bundle, pool(), type_name=asked, title="T", description="d", source=source("s1", "r"), by=BY, at=AT)
