"""Filing: the lane resolves the target, the capability owns the merge."""

import pytest
from doc_wiki_okf.proposals.filing import plan_file
from okf_ext.proposals import apply
from proposal_helpers import AT, BY, build_bundle, lanes, source


def _file(bundle, *, title="Bulk Write Staging Protocol", entry=None, lane="adr"):
    return plan_file(
        bundle,
        lanes(),
        lane=lane,
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
    assert "Create new Explanation page `adrs/bulk-write-staging-protocol.md`." in plan.writes[0].text


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


def test_an_unknown_lane_raises(tmp_path) -> None:
    with pytest.raises(KeyError):
        _file(build_bundle(tmp_path / "b"), lane="concept")


def test_an_existing_target_files_in_update_mode(tmp_path) -> None:
    root = tmp_path / "b"
    page = "---\ntype: Reference\ntitle: Flags\n---\n\n# Flags\n"
    bundle = build_bundle(root, {"references/flags": page})
    plan = plan_file(
        bundle,
        lanes(),
        lane="reference",
        title="Flags",
        description="",
        source=source("src-a", "sources/x.md"),
        by=BY,
        at=AT,
    )
    assert "Update existing Reference page `references/flags.md`." in plan.writes[0].text
