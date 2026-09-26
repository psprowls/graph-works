from __future__ import annotations

import importlib.resources

from doc_wiki_okf.sources import drain_status, drain_statuses, entry_keys_from
from drain_helpers import ENTRY_KEYS, source, wiki
from okf_ext.schemas import load_schemas

MEMBER = "sources/2026-09-s.md"
FULL = (
    "drain:\n"
    "  - claim: 1\n    landed: [/adrs/a.md#D1, /docs/explanations/e.md#C1]\n"
    "  - claim: 2\n    landed: [/docs/reference/r.md#C2]\n"
    "  - claim: 3\n    dropped: history\n"
)


def test_entry_keys_come_from_the_seed_schema_set() -> None:
    assets = importlib.resources.files("doc_wiki_okf") / "assets" / "schema"
    assert entry_keys_from(load_schemas(str(assets))) == ENTRY_KEYS


def test_a_fully_dispositioned_cited_source_is_drained(tmp_path) -> None:
    status = drain_status(wiki(tmp_path, source(drain=FULL)), MEMBER, ENTRY_KEYS)
    assert (status.items, status.landed, status.dropped, status.pending) == (3, frozenset({1, 2}), frozenset({3}), ())
    assert status.problems == ()
    assert status.drained and status.reason is None


def test_a_partial_ledger_leaves_the_rest_pending(tmp_path) -> None:
    bundle = wiki(tmp_path, source(drain="drain:\n  - claim: 2\n    dropped: evidence\n"))
    status = drain_status(bundle, MEMBER, ENTRY_KEYS)
    assert status.pending == (1, 3)
    assert not status.drained
    assert status.reason == "pending: 1, 3"


def test_no_key_claims_is_undrainable(tmp_path) -> None:
    status = drain_status(wiki(tmp_path, source(claims="Prose only.\n")), MEMBER, ENTRY_KEYS)
    assert status.items == 0 and not status.drained and status.reason == "no-key-claims"


def test_empty_where_cited_is_undrained(tmp_path) -> None:
    status = drain_status(wiki(tmp_path, source(drain=FULL, cited="\n")), MEMBER, ENTRY_KEYS)
    assert not status.cited_in_wiki and not status.drained and status.reason == "not cited in this wiki"


def test_a_ref_on_a_type_without_a_mandate_searches_every_declared_key(tmp_path) -> None:
    # Reference has no `entries` mandate; `C2` lives in its optional `claims:`.
    status = drain_status(wiki(tmp_path, source(drain=FULL)), MEMBER, ENTRY_KEYS)
    assert 2 in status.landed and not status.problems


def test_citation_matches_with_or_without_slash_and_fragment(tmp_path) -> None:
    # EXPLANATION cites `sources/2026-09-s.md#top`, with no slash and with a fragment.
    status = drain_status(wiki(tmp_path, source(drain=FULL)), MEMBER, ENTRY_KEYS)
    assert not [p for p in status.problems if p.kind == "uncited"]


def test_out_of_range_and_duplicate_ordinals_are_invalid(tmp_path) -> None:
    ledger = (
        "drain:\n  - claim: 9\n    dropped: history\n"
        "  - claim: 1\n    dropped: history\n"
        "  - claim: 1\n    dropped: evidence\n"
    )
    kinds = [p.kind for p in drain_status(wiki(tmp_path, source(drain=ledger)), MEMBER, ENTRY_KEYS).problems]
    assert kinds == ["invalid", "invalid"]


def test_a_non_list_ledger_is_one_invalid_problem(tmp_path) -> None:
    status = drain_status(wiki(tmp_path, source(drain="drain: oops\n")), MEMBER, ENTRY_KEYS)
    assert [p.kind for p in status.problems] == ["invalid"]
    assert status.pending == (1, 2, 3)


def test_bool_claim_is_invalid(tmp_path) -> None:
    bundle = wiki(tmp_path, source(drain="drain:\n  - claim: true\n    dropped: history\n"))
    status = drain_status(bundle, MEMBER, ENTRY_KEYS)
    assert [p.kind for p in status.problems] == ["invalid"]


def test_both_and_neither_are_invalid(tmp_path) -> None:
    ledger = "drain:\n  - claim: 1\n    landed: [/adrs/a.md#D1]\n    dropped: history\n  - claim: 2\n"
    problems = drain_status(wiki(tmp_path, source(drain=ledger)), MEMBER, ENTRY_KEYS).problems
    assert [p.kind for p in problems] == ["invalid", "invalid"]


def test_dangling_refs(tmp_path) -> None:
    ledger = (
        "drain:\n  - claim: 1\n    landed: [/adrs/missing.md#D1]\n"
        "  - claim: 2\n    landed: [/adrs/a.md#D9]\n"
        "  - claim: 3\n    landed: [/adrs/a.md]\n"
    )
    kinds = [p.kind for p in drain_status(wiki(tmp_path, source(drain=ledger)), MEMBER, ENTRY_KEYS).problems]
    assert kinds == ["dangling", "dangling", "dangling"]


def test_a_ref_on_a_page_that_does_not_cite_the_source_is_uncited(tmp_path) -> None:
    other = (
        "---\ntype: Adr\ntitle: B\ndescription: d\nstatus: accepted\ndecisions:\n"
        "  - id: D1\n    claim: x.\n---\n\n## Decision\nd\n"
    )
    ledger = "drain:\n  - claim: 1\n    landed: [/adrs/b.md#D1]\n"
    status = drain_status(wiki(tmp_path, source(drain=ledger), **{"adrs/b.md": other}), MEMBER, ENTRY_KEYS)
    assert [p.kind for p in status.problems] == ["uncited"]


def test_a_ref_on_a_page_whose_sources_list_never_matches_is_uncited(tmp_path) -> None:
    # Exercises the full `_cites` loop: a mapping entry with no `resource` key
    # (skipped) followed by one whose `resource` names a different source
    # (also skipped), so the loop runs to its end and returns False.
    other = (
        "---\ntype: Adr\ntitle: C\ndescription: d\nstatus: accepted\ndecisions:\n"
        "  - id: D1\n    claim: x.\n"
        "sources:\n  - note: not-a-resource-entry\n  - id: z\n    resource: /sources/2026-09-other.md\n"
        "---\n\n## Decision\nd\n"
    )
    ledger = "drain:\n  - claim: 1\n    landed: [/adrs/c.md#D1]\n"
    status = drain_status(wiki(tmp_path, source(drain=ledger), **{"adrs/c.md": other}), MEMBER, ENTRY_KEYS)
    assert [p.kind for p in status.problems] == ["uncited"]


def test_a_non_mapping_drain_entry_is_invalid(tmp_path) -> None:
    status = drain_status(wiki(tmp_path, source(drain="drain:\n  - 5\n")), MEMBER, ENTRY_KEYS)
    assert [p.kind for p in status.problems] == ["invalid"]


def test_an_unrecognised_dropped_reason_is_invalid(tmp_path) -> None:
    status = drain_status(
        wiki(tmp_path, source(drain="drain:\n  - claim: 1\n    dropped: bogus\n")), MEMBER, ENTRY_KEYS
    )
    assert [p.kind for p in status.problems] == ["invalid"]
    assert 1 not in status.dropped


def test_an_empty_landed_list_is_invalid(tmp_path) -> None:
    status = drain_status(wiki(tmp_path, source(drain="drain:\n  - claim: 1\n    landed: []\n")), MEMBER, ENTRY_KEYS)
    assert [p.kind for p in status.problems] == ["invalid"]


def test_a_non_list_landed_value_is_invalid(tmp_path) -> None:
    status = drain_status(wiki(tmp_path, source(drain="drain:\n  - claim: 1\n    landed: oops\n")), MEMBER, ENTRY_KEYS)
    assert [p.kind for p in status.problems] == ["invalid"]


def test_archived_and_reference_members_are_not_sources(tmp_path) -> None:
    bundle = wiki(
        tmp_path,
        source(drain=FULL),
        **{"sources/_archive/2026-01-old.md": source(drain=FULL), "sources/references/2026-09-s.md": "# material\n"},
    )
    assert [s.member for s in drain_statuses(bundle, ENTRY_KEYS)] == [MEMBER]
