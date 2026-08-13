"""The rewriter's read half: the mapping, the four refusals, the placements."""

from datetime import datetime

import pytest
from doc_wiki_okf.proposals.migrate import MigrationPlan, plan_migrate
from migrate_helpers import reload_bundle
from okf_io import Document
from proposal_helpers import AT, BY, build_bundle

OLD = """\
---
kind: adr
mode: create_new
target_slug: bulk-write-staging-protocol
title: Bulk multi-file writes stage to temp siblings
status: proposed
origins:
- ref: sources/2026-08-design-spec-scaffold-okf-ext-and-tag-management
  source: ingest
  rationale: It settles it.
  evidence:
  - Staging precedes any live write.
---
<!-- machine body -->

## Suggested Action

Create new adr page `adrs/bulk-write-staging-protocol.md`.
"""


def _plan(tmp_path, pages) -> MigrationPlan:
    return plan_migrate(build_bundle(tmp_path / "b", pages), by=BY, at=AT)


def _written(plan, member):
    return next(write for write in plan.writes if write.member == member)


def test_an_old_dialect_document_plans_one_write(tmp_path) -> None:
    plan = _plan(tmp_path, {"proposals/adr-bulk": OLD})
    assert plan.ok
    assert [write.member for write in plan.writes] == ["proposals/adr-bulk.md"]


def test_the_mapping_is_the_spec_table(tmp_path) -> None:
    plan = _plan(tmp_path, {"proposals/adr-bulk": OLD})
    data = Document.parse(_written(plan, "proposals/adr-bulk.md").text).fm_data()

    assert data["type"] == "Proposal"
    assert data["target"] == "adrs/bulk-write-staging-protocol.md"
    assert data["page_status"] == "proposed"
    assert data["title"] == "Bulk multi-file writes stage to temp siblings"
    assert data["description"] == ""
    assert data["generated"] == {"by": BY, "at": AT.isoformat()}
    assert data["sources"] == [
        {
            "id": "2026-08-design-spec-scaffold-okf-ext-and-tag-management",
            "resource": "sources/2026-08-design-spec-scaffold-okf-ext-and-tag-management.md",
            "rationale": "It settles it.",
            "evidence": ["Staging precedes any live write."],
        }
    ]
    for dead in ("kind", "mode", "target_slug", "status", "origins", "tokens"):
        assert dead not in data


def test_the_key_order_is_the_cores(tmp_path) -> None:
    """`Document.set` gives a new key the position `PREFERRED_KEY_ORDER` says
    it should have, so the result matches what `plan_propose` writes."""
    plan = _plan(tmp_path, {"proposals/adr-bulk": OLD})
    data = Document.parse(_written(plan, "proposals/adr-bulk.md").text).fm_data()
    assert list(data) == ["type", "title", "description", "generated", "sources", "target", "page_status"]


def test_a_concept_kind_migrates_to_a_concepts_target(tmp_path) -> None:
    """`concept` has no successor as a *lane*; it has one as a *target*. A
    proposal's own type is Proposal regardless of what it argues for."""
    page = OLD.replace("kind: adr", "kind: concept")
    plan = _plan(tmp_path, {"proposals/concept-x": page})
    data = Document.parse(_written(plan, "proposals/concept-x.md").text).fm_data()
    assert data["target"] == "concepts/bulk-write-staging-protocol.md"


def test_an_already_migrated_document_is_skipped_not_refused(tmp_path) -> None:
    """Re-running is an empty plan, not an error -- the capability's own
    'idempotence surfaces as an empty plan' posture, inherited."""
    new = "---\ntype: Proposal\ntitle: Already\ntarget: adrs/x.md\npage_status: proposed\n---\nbody\n"
    plan = _plan(tmp_path, {"proposals/adrs-x": new})
    assert plan.ok
    assert plan.is_empty


def test_a_ref_that_already_has_an_extension_is_left_alone(tmp_path) -> None:
    page = OLD.replace(
        "ref: sources/2026-08-design-spec-scaffold-okf-ext-and-tag-management",
        "ref: sources/already.md",
    )
    plan = _plan(tmp_path, {"proposals/adr-bulk": page})
    data = Document.parse(_written(plan, "proposals/adr-bulk.md").text).fm_data()
    assert data["sources"][0]["resource"] == "sources/already.md"
    assert data["sources"][0]["id"] == "already"


def test_a_blank_ref_carries_no_id_or_resource_but_keeps_other_keys(tmp_path) -> None:
    """A blank `ref` is tolerated, not refused: the entry lands in `sources[]`
    with no `id`/`resource` key, carrying whatever other keys it had."""
    page = OLD.replace(
        "ref: sources/2026-08-design-spec-scaffold-okf-ext-and-tag-management",
        "ref: ''",
    )
    plan = _plan(tmp_path, {"proposals/adr-bulk": page})
    data = Document.parse(_written(plan, "proposals/adr-bulk.md").text).fm_data()
    assert data["sources"] == [
        {
            "rationale": "It settles it.",
            "evidence": ["Staging precedes any live write."],
        }
    ]


def test_origins_that_is_not_a_list_produces_no_sources(tmp_path) -> None:
    """A non-list `origins` is content to tolerate, not an error to raise on:
    the document still plans a normal write, just with an empty `sources[]`."""
    page = OLD.replace(
        "origins:\n"
        "- ref: sources/2026-08-design-spec-scaffold-okf-ext-and-tag-management\n"
        "  source: ingest\n"
        "  rationale: It settles it.\n"
        "  evidence:\n"
        "  - Staging precedes any live write.\n",
        "origins: not-a-list\n",
    )
    plan = _plan(tmp_path, {"proposals/adr-bulk": page})
    assert plan.ok
    data = Document.parse(_written(plan, "proposals/adr-bulk.md").text).fm_data()
    assert data["sources"] == []


def test_a_non_mapping_origins_entry_is_skipped_its_neighbour_still_lands(tmp_path) -> None:
    """A list element that is not a mapping is skipped rather than raising; a
    valid neighbour in the same list still produces a `sources[]` entry."""
    page = OLD.replace(
        "origins:\n- ref:",
        "origins:\n- just-a-string\n- ref:",
    )
    plan = _plan(tmp_path, {"proposals/adr-bulk": page})
    data = Document.parse(_written(plan, "proposals/adr-bulk.md").text).fm_data()
    assert len(data["sources"]) == 1
    assert data["sources"][0]["resource"] == "sources/2026-08-design-spec-scaffold-okf-ext-and-tag-management.md"


def test_planning_writes_nothing(tmp_path) -> None:
    root = tmp_path / "b"
    before = root / "proposals" / "adr-bulk.md"
    plan_migrate(build_bundle(root, {"proposals/adr-bulk": OLD}), by=BY, at=AT)
    assert before.read_text(encoding="utf-8") == OLD


def test_a_naive_instant_raises(tmp_path) -> None:
    """These packages never read the clock, and a naive instant lands in
    `generated.at` as a value okf-io cannot coerce."""
    with pytest.raises(ValueError, match="timezone-aware"):
        plan_migrate(build_bundle(tmp_path / "b", {}), by=BY, at=datetime(2026, 8, 12, 9, 0))


# --- the four refusals ------------------------------------------------------


def test_an_unknown_kind_is_refused(tmp_path) -> None:
    plan = _plan(tmp_path, {"proposals/x": OLD.replace("kind: adr", "kind: pattern")})
    assert not plan.ok
    assert [(r.member, r.kind) for r in plan.refusals] == [("proposals/x.md", "unknown-kind")]
    assert plan.is_empty


def test_an_unrecognised_status_is_refused(tmp_path) -> None:
    plan = _plan(tmp_path, {"proposals/x": OLD.replace("status: proposed", "status: pending")})
    assert [(r.member, r.kind) for r in plan.refusals] == [("proposals/x.md", "unrecognised-status")]


def test_a_blank_target_slug_is_refused(tmp_path) -> None:
    plan = _plan(tmp_path, {"proposals/x": OLD.replace("target_slug: bulk-write-staging-protocol", "target_slug: ''")})
    assert [(r.member, r.kind) for r in plan.refusals] == [("proposals/x.md", "unresolvable-target")]


def test_a_climbing_target_slug_is_refused(tmp_path) -> None:
    page = OLD.replace("target_slug: bulk-write-staging-protocol", "target_slug: ../escape")
    plan = _plan(tmp_path, {"proposals/x": page})
    assert [(r.member, r.kind) for r in plan.refusals] == [("proposals/x.md", "unresolvable-target")]


def test_an_unreadable_old_dialect_document_is_refused(tmp_path) -> None:
    """Unterminated frontmatter swallows every line, so `fm_raw` is empty and
    the trigger cannot fire. Scanning the raw text for the trigger key is
    allowed to do exactly one thing: turn a hidden old-dialect proposal into a
    refusal instead of a silent skip."""
    broken = "---\nkind: adr\ntarget_slug: x\nstatus: proposed\n\n## no close delimiter\n"
    plan = _plan(tmp_path, {"proposals/x": broken})
    assert [(r.member, r.kind) for r in plan.refusals] == [("proposals/x.md", "unreadable")]


def test_a_parse_error_that_is_not_a_proposal_is_ignored(tmp_path) -> None:
    """A broken tutorial page is not this rewriter's business."""
    broken = "---\ntitle: A broken page\n\n## no close delimiter\n"
    plan = _plan(tmp_path, {"tutorials/x": broken})
    assert plan.ok
    assert plan.is_empty


def test_one_refusal_does_not_stop_its_neighbours(tmp_path) -> None:
    """All-or-nothing per **document**, never across the batch: a vault with
    one bad proposal still converts."""
    plan = _plan(
        tmp_path,
        {
            "proposals/good": OLD,
            "proposals/bad": OLD.replace("kind: adr", "kind: pattern"),
        },
    )
    assert not plan.ok
    assert [write.member for write in plan.writes] == ["proposals/good.md"]
    assert [r.member for r in plan.refusals] == ["proposals/bad.md"]


# --- placements -------------------------------------------------------------


def test_placements_map_old_member_to_the_capabilitys_own_rule(tmp_path) -> None:
    plan = _plan(tmp_path, {"proposals/adr-bulk": OLD})
    assert plan.placements == {"proposals/adr-bulk.md": "proposals/adrs-bulk-write-staging-protocol.md"}


def test_an_archived_proposal_stays_archived(tmp_path) -> None:
    """The migrator passes the source document's own parent directory, which
    is what keeps `_archive/` from being resurrected into the live lane."""
    plan = _plan(tmp_path, {"proposals/_archive/adr-bulk": OLD.replace("status: proposed", "status: created")})
    assert plan.placements == {
        "proposals/_archive/adr-bulk.md": "proposals/_archive/adrs-bulk-write-staging-protocol.md"
    }


def test_a_stray_proposal_outside_the_proposals_directory_re_places_in_place(tmp_path) -> None:
    """The trigger is content-based, so the walk is the whole bundle -- the
    migrator never asks where a proposal *lives*, only what it *says*."""
    plan = _plan(tmp_path, {"notes/adr-bulk": OLD})
    assert plan.placements == {"notes/adr-bulk.md": "notes/adrs-bulk-write-staging-protocol.md"}


# --- apply ------------------------------------------------------------------


def test_apply_rewrites_in_place(tmp_path) -> None:
    from doc_wiki_okf.proposals.migrate import apply

    root = tmp_path / "b"
    bundle = build_bundle(root, {"proposals/adr-bulk": OLD})
    result = apply(bundle, plan_migrate(bundle, by=BY, at=AT))

    assert result.ok
    assert result.written == ("proposals/adr-bulk.md",)
    landed = (root / "proposals" / "adr-bulk.md").read_text(encoding="utf-8")
    assert Document.parse(landed).fm_data()["type"] == "Proposal"


def test_apply_does_not_gate_on_ok(tmp_path) -> None:
    """The one place this departs from `moves.apply`, which raises on a non-ok
    plan. Nothing here is computed across documents, so per-document isolation
    is both safe and more useful."""
    from doc_wiki_okf.proposals.migrate import apply

    root = tmp_path / "b"
    bundle = build_bundle(root, {"proposals/good": OLD, "proposals/bad": OLD.replace("kind: adr", "kind: pattern")})
    plan = plan_migrate(bundle, by=BY, at=AT)
    assert not plan.ok

    result = apply(bundle, plan)
    assert result.written == ("proposals/good.md",)
    assert (root / "proposals" / "bad.md").read_text(encoding="utf-8") == OLD.replace("kind: adr", "kind: pattern")


def test_apply_refuses_a_plan_from_another_bundle(tmp_path) -> None:
    from doc_wiki_okf.proposals.migrate import apply

    one = build_bundle(tmp_path / "one", {"proposals/adr-bulk": OLD})
    other = build_bundle(tmp_path / "other", {"proposals/adr-bulk": OLD})
    with pytest.raises(ValueError, match="different bundle"):
        apply(other, plan_migrate(one, by=BY, at=AT))


def test_apply_refuses_a_drifted_body(tmp_path) -> None:
    """`Bundle` is a frozen, one-time snapshot -- `apply` never re-reads disk
    on its own -- so staleness only surfaces once the caller reloads. This
    mirrors `test_moves_apply.py::test_a_stale_body_aborts_the_whole_batch`:
    the plan is computed against the pre-edit bundle, the file is edited on
    disk, and the *reloaded* bundle is what `apply` is called against."""
    from doc_wiki_okf.proposals.migrate import apply

    root = tmp_path / "b"
    bundle = build_bundle(root, {"proposals/adr-bulk": OLD})
    plan = plan_migrate(bundle, by=BY, at=AT)
    (root / "proposals" / "adr-bulk.md").write_text(OLD + "\nan edit\n", encoding="utf-8")
    reloaded = build_bundle(root)

    result = apply(reloaded, plan)
    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [("proposals/adr-bulk.md", "stale")]


def test_apply_refuses_a_member_no_longer_in_the_bundle(tmp_path) -> None:
    from doc_wiki_okf.proposals.migrate import apply

    root = tmp_path / "b"
    bundle = build_bundle(root, {"proposals/adr-bulk": OLD})
    plan = plan_migrate(bundle, by=BY, at=AT)
    (root / "proposals" / "adr-bulk.md").unlink()
    reloaded = build_bundle(root)

    result = apply(reloaded, plan)
    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [("proposals/adr-bulk.md", "not-a-member")]


def test_apply_refuses_a_document_that_now_fails_to_parse(tmp_path) -> None:
    from doc_wiki_okf.proposals.migrate import apply

    root = tmp_path / "b"
    bundle = build_bundle(root, {"proposals/adr-bulk": OLD})
    plan = plan_migrate(bundle, by=BY, at=AT)
    (root / "proposals" / "adr-bulk.md").write_text(
        "---\nkind: adr\ntarget_slug: x\nstatus: proposed\n\n## no close delimiter\n", encoding="utf-8"
    )
    reloaded = build_bundle(root)

    result = apply(reloaded, plan)
    assert not result.ok
    assert [(f.path, f.kind) for f in result.failed] == [("proposals/adr-bulk.md", "parse-error")]


# --- the documented sequence ------------------------------------------------


def test_the_round_trip_rewrites_then_moves(tmp_path) -> None:
    from doc_wiki_okf.proposals.migrate import migrate_and_move
    from okf_ext.proposals import list_proposals

    root = tmp_path / "b"
    build_bundle(root, {"proposals/adr-bulk": OLD})
    outcome = migrate_and_move(root, by=BY, at=AT)

    assert outcome.ok
    assert not (root / "proposals" / "adr-bulk.md").exists()
    landed = root / "proposals" / "adrs-bulk-write-staging-protocol.md"
    assert landed.is_file()

    reloaded = reload_bundle(root)
    proposals = list_proposals(reloaded)
    assert [p.target for p in proposals] == ["adrs/bulk-write-staging-protocol.md"]
    assert proposals[0].malformed is None
    assert proposals[0].page_status == "proposed"


def test_the_round_trip_is_a_no_op_when_nothing_is_old_dialect(tmp_path) -> None:
    """`migrate_and_move` skips the move step entirely when the rewrite lands
    nothing -- an already-migrated bundle, most plausibly -- rather than
    paying for a no-op move plan."""
    from doc_wiki_okf.proposals.migrate import migrate_and_move

    root = tmp_path / "b"
    new = "---\ntype: Proposal\ntitle: Already\ntarget: adrs/x.md\npage_status: proposed\n---\nbody\n"
    build_bundle(root, {"proposals/already": new})

    outcome = migrate_and_move(root, by=BY, at=AT)

    assert outcome.ok
    assert outcome.plan.is_empty
    assert outcome.rewrite.written == ()
    assert outcome.move_plan.is_empty
    assert outcome.move.moved == ()
    assert (root / "proposals" / "already.md").read_text(encoding="utf-8") == new


def test_the_round_trip_reports_a_refused_move_plan_without_raising(tmp_path) -> None:
    """Two old-dialect proposals that resolve to the same `target_slug` each
    plan their own placement against the bundle as it stood before either
    write landed, so both compute the same destination. `moves.plan_move_many`
    catches the collision as `dest-exists`; `moves.apply` raises on a non-ok
    plan, so this function must report the refusal through `move_plan`
    instead of calling it."""
    from doc_wiki_okf.proposals.migrate import migrate_and_move

    root = tmp_path / "b"
    build_bundle(root, {"proposals/one": OLD, "proposals/two": OLD})

    outcome = migrate_and_move(root, by=BY, at=AT)

    assert not outcome.ok
    assert outcome.plan.ok
    assert outcome.rewrite.ok
    assert not outcome.move_plan.ok
    assert any(r.kind == "dest-exists" for r in outcome.move_plan.refusals)
    assert outcome.move.moved == ()
    assert (root / "proposals" / "one.md").is_file()
    assert (root / "proposals" / "two.md").is_file()
