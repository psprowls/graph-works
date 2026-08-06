from __future__ import annotations

from datetime import date

import pytest
from helpers import BUNDLES, EDGE, all_concept_files, fixture_id, read
from okf_io import _yaml
from okf_io.models import build_frontmatter, parse_actor


def view(name: str):
    split = _yaml.split(read(EDGE / name))
    return build_frontmatter(_yaml.load_fm(split.fm_text), body=split.body)


def test_bare_mapping_verified_normalizes_to_one_entry():
    fm = view("verified_bare_mapping.md")
    assert len(fm.verified) == 1
    assert fm.verified[0].by is not None
    assert fm.verified[0].by.kind == "human"
    assert fm.verified[0].by.id == "jsmith@acme"


def test_verified_list_keeps_order_and_kinds():
    fm = view("verified_list.md")
    assert [v.by.kind for v in fm.verified if v.by] == ["process", "human"]


def test_dates_coerce_identically_whether_quoted_or_not():
    pre, quoted = view("dates_preparsed.md"), view("dates_quoted.md")
    assert pre.stale_after == quoted.stale_after == date(2026, 12, 31)
    assert pre.sources[0].last_modified == quoted.sources[0].last_modified
    assert pre.generated is not None and quoted.generated is not None
    assert pre.generated.at_dt == quoted.generated.at_dt


def test_keyword_colliding_keys_land_in_extra():
    fm = view("keyword_keys.md")
    assert set(fm.extra) >= {"not", "from", "class"}
    assert fm.extra["from"] == "legacy-import"
    assert fm.extra["not"][0]["term"] == "revenue minus product cost only"


@pytest.mark.parametrize(
    ("raw", "kind", "ident"),
    [
        ("human:jsmith@acme", "human", "jsmith@acme"),
        ("process:finance-nightly", "process", "finance-nightly"),
        ("reference_agent/gemini-2.5-pro", "agent", "reference_agent/gemini-2.5-pro"),
        ("team:data-platform", "unknown", "team:data-platform"),
    ],
)
def test_actor_classification(raw, kind, ident):
    actor = parse_actor(raw)
    assert actor is not None
    assert (actor.kind, actor.id) == (kind, ident)


def test_actor_of_none_is_none():
    assert parse_actor(None) is None


def test_coercion_failures_record_dotted_paths():
    from ruamel.yaml.comments import CommentedMap

    fm = build_frontmatter(
        CommentedMap(
            {
                "type": ["not", "a", "string"],
                "sources": [{"id": "ok", "resource": {"wrong": "shape"}}],
            }
        )
    )
    assert fm.type is None
    assert "type" in fm.coercion_failures
    assert "sources.0.resource" in fm.coercion_failures


@pytest.mark.parametrize("path", all_concept_files(), ids=fixture_id)
def test_build_never_raises(path):
    split = _yaml.split(read(path))
    try:
        raw = _yaml.load_fm(split.fm_text) if split.has_frontmatter else None
    except Exception:
        raw = None
    from ruamel.yaml.comments import CommentedMap

    build_frontmatter(raw if raw is not None else CommentedMap(), body=split.body)


def test_wrong_shaped_tag_is_recorded_not_stringified():
    """`tuple(str(t) for t in tags)` would turn a mapping into "{'a': 1}"."""
    from ruamel.yaml.comments import CommentedMap

    fm = build_frontmatter(CommentedMap({"tags": ["real", {"a": 1}, ["b"]]}))
    assert fm.tags == ("real",)
    assert "tags.1" in fm.coercion_failures
    assert "tags.2" in fm.coercion_failures


def test_wrong_shaped_receipt_entry_is_recorded_not_stringified():
    from ruamel.yaml.comments import CommentedMap

    fm = build_frontmatter(CommentedMap({"executor": {"resource": "r", "receipt": ["ok", {"a": 1}]}}))
    assert fm.executor is not None
    assert fm.executor.receipt == ("ok",)
    assert "executor.receipt.1" in fm.coercion_failures


def test_structurally_wrong_actor_is_recorded():
    """A mapping `by:` must be distinguishable from an odd-but-valid string."""
    from ruamel.yaml.comments import CommentedMap

    fm = build_frontmatter(CommentedMap({"generated": {"by": {"nested": "map"}}}))
    assert "generated.by" in fm.coercion_failures

    # An unrecognized *format* is not a failure -- it is a legitimate unknown.
    ok = build_frontmatter(CommentedMap({"generated": {"by": "team:data-platform"}}))
    assert ok.generated is not None
    assert ok.generated.by is not None
    assert ok.generated.by.kind == "unknown"
    assert ok.coercion_failures == frozenset()


def test_extra_is_read_only_at_the_top_level():
    """`extra` aliases live fm_raw objects; a memoized view must not be a way in."""
    from ruamel.yaml.comments import CommentedMap

    fm = build_frontmatter(CommentedMap({"custom": "value"}))
    with pytest.raises(TypeError):
        fm.extra["injected"] = "surprise"  # type: ignore[index]


def test_legacy_timestamp_populates_generated_at():
    fm = view("legacy_timestamp.md")
    assert fm.generated is not None
    assert fm.generated.at_dt is not None
    assert fm.generated.at_dt.year == 2024
    assert "generated.at" in fm.fallbacks


def test_legacy_citations_populate_sources():
    fm = view("legacy_citations.md")
    assert "sources" in fm.fallbacks
    assert [s.resource for s in fm.sources] == [
        "policies/revenue-recognition.md",
        "policies/margin-standard.md",
    ]
    assert fm.sources[0].title == "Revenue Recognition Policy (FY2026)"


def test_fallbacks_do_not_fire_when_the_modern_field_is_present():
    split = _yaml.split(read(BUNDLES / "acme_retail/metrics/revenue.md"))
    fm = build_frontmatter(_yaml.load_fm(split.fm_text), body=split.body)
    assert fm.fallbacks == frozenset()
    assert fm.generated is not None
    assert fm.generated.at_dt is not None
    assert len(fm.sources) == 1


def test_no_fallbacks_on_a_document_with_neither():
    assert view("dialect_flow.md").fallbacks == frozenset()


def test_real_sources_are_not_clobbered_by_a_leftover_citations_section():
    """The guard's safety-critical direction: real data must win.

    A v0.1 document that has been migrated keeps its old `# Citations` prose
    while gaining a real `sources` block. Losing the real one to the scanner
    would be silent data corruption.
    """
    from ruamel.yaml.comments import CommentedMap

    body = "# Definition\n\nText.\n\n# Citations\n\n- [Legacy](legacy/old.md)\n"
    fm = build_frontmatter(
        CommentedMap({"sources": [{"id": "real", "resource": "policies/real.md"}]}),
        body=body,
    )
    assert [s.resource for s in fm.sources] == ["policies/real.md"]
    assert fm.fallbacks == frozenset()


def test_unparseable_legacy_timestamp_is_flagged_both_ways():
    """The fallback fired, and it produced something unusable. Both are visible."""
    from ruamel.yaml.comments import CommentedMap

    fm = build_frontmatter(CommentedMap({"timestamp": "not-a-real-timestamp"}))
    assert fm.generated is not None
    assert fm.generated.at == "not-a-real-timestamp"
    assert fm.generated.at_dt is None
    assert "timestamp" in fm.coercion_failures
    assert "generated.at" in fm.fallbacks


def test_a_directly_constructed_frontmatter_is_also_read_only():
    """`Frontmatter` is exported, so its default must honour its own contract."""
    from okf_io.models import Frontmatter

    fm = Frontmatter()
    with pytest.raises(TypeError):
        fm.extra["injected"] = "surprise"  # type: ignore[index]


def test_the_bracketed_number_dialect_now_populates_sources():
    """The announced ADR-0003 read-side change (design spec §5.3).

    `crypto_bitcoin` writes citations as `[n]` paragraph lines. They are not
    list items, so the list-item-only scan returned nothing and the fallback
    never fired on this dialect. It does now: the document really is v0.1 with
    citations, and the values arrive flagged in `fallbacks` exactly as the
    list dialect's do.
    """
    from ruamel.yaml.comments import CommentedMap

    body = (
        "# Schema\n\nText.\n\n# Citations\n\n"
        "[1] [Bitcoin Transactions](https://x.example/t)\n"
        "[2] [Bitcoin ETL](https://github.com/blockchain-etl/bitcoin-etl)\n"
    )
    fm = build_frontmatter(CommentedMap(), body=body)
    assert "sources" in fm.fallbacks
    assert [s.title for s in fm.sources] == ["Bitcoin Transactions", "Bitcoin ETL"]
    assert [s.resource for s in fm.sources] == [
        "https://x.example/t",
        "https://github.com/blockchain-etl/bitcoin-etl",
    ]


def test_a_numbered_line_with_no_link_keeps_the_two_field_shape():
    """Unchanged contract: a non-link entry is `Source(title=text, resource=text)`."""
    from ruamel.yaml.comments import CommentedMap

    body = "# Citations\n\n[1] See the FY2026 policy binder\n"
    fm = build_frontmatter(CommentedMap(), body=body)
    assert fm.sources[0].title == "See the FY2026 policy binder"
    assert fm.sources[0].resource == "See the FY2026 policy binder"


def test_an_impure_section_still_reads_its_entries():
    """Purity gates the *rewrite*, never the read.

    A section holding prose beside its citations is still a v0.1 document
    carrying citations, and a reader that dropped them would lose data the
    fallback exists to recover.
    """
    from ruamel.yaml.comments import CommentedMap

    body = "# Citations\n\n- [Policy](p.md)\n\nSee also the binder.\n"
    fm = build_frontmatter(CommentedMap(), body=body)
    assert "sources" in fm.fallbacks
    assert [s.resource for s in fm.sources] == ["p.md"]


def test_a_second_citations_heading_is_not_folded_into_the_first():
    """Entries stop at the next heading; a second section's entries never reach the view.

    This behaviour is a deliberate consequence of delegating to the shared
    locator: the rewriter finds the same construct in order to delete it, and
    the locator stops at the next heading. The test exists so a future change
    to the locator's boundary cannot move this behaviour unnoticed.
    """
    from ruamel.yaml.comments import CommentedMap

    body = "# Citations\n\n[1] [First](first.md)\n\n# Citations\n\n[2] [Second](second.md)\n"
    fm = build_frontmatter(CommentedMap(), body=body)
    assert "sources" in fm.fallbacks
    assert [s.resource for s in fm.sources] == ["first.md"]
