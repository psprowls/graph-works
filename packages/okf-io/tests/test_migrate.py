from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from helpers import BUNDLES, EDGE, read
from okf_io import bundle, migrate
from okf_io.document import Document
from okf_io.migrate import DEFAULT_ACTOR, Migration, _is_instant
from okf_io.models import _as_timestamp


def make(tmp_path: Path, files: dict[str, str]) -> bundle.Bundle:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return bundle.load(tmp_path)


def from_edge(tmp_path: Path, *names: str) -> bundle.Bundle:
    """A one-concept-per-fixture bundle built from `edge/` files, byte for byte."""
    for name in names:
        target = tmp_path / Path(name).name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((EDGE / name).read_bytes())
    return bundle.load(tmp_path)


def only(results: tuple[Migration, ...]) -> Migration:
    assert len(results) == 1, [r.path for r in results]
    return results[0]


# --- the sweep -------------------------------------------------------------


def test_a_clean_v02_bundle_has_nothing_to_migrate():
    """§8.3's first regression guard."""
    for name in ("acme_retail", "ga4"):
        assert migrate(bundle.load(BUNDLES / name)) == ()


def test_results_carry_only_documents_with_something_to_report(tmp_path):
    loaded = make(
        tmp_path,
        {
            "a.md": read(EDGE / "legacy_timestamp.md"),
            "b.md": "---\ntype: Metric\ntitle: Clean\n---\n\n# B\n",
        },
    )
    assert [r.path for r in migrate(loaded)] == ["a.md"]


def test_results_are_sorted_by_path(tmp_path):
    text = read(EDGE / "legacy_timestamp.md")
    loaded = make(tmp_path, {"z/one.md": text, "a/two.md": text, "m.md": text})
    assert [r.path for r in migrate(loaded)] == ["a/two.md", "m.md", "z/one.md"]


def test_dry_run_is_the_default_and_writes_nothing(tmp_path):
    loaded = from_edge(tmp_path, "legacy_timestamp.md")
    before = (tmp_path / "legacy_timestamp.md").read_bytes()
    result = only(migrate(loaded))
    assert result.changed
    assert (tmp_path / "legacy_timestamp.md").read_bytes() == before


def test_dry_run_does_not_mutate_the_bundles_documents(tmp_path):
    """§6.3. A declined change must not survive in the in-memory bundle."""
    loaded = from_edge(tmp_path, "legacy_timestamp.md")
    document = loaded.concepts["legacy_timestamp"]
    migrate(loaded)
    assert "timestamp" in document.fm_raw
    assert document.serialize() == document.raw_text


def test_an_explicit_write_lands_the_bytes(tmp_path):
    loaded = from_edge(tmp_path, "legacy_timestamp.md")
    result = only(migrate(loaded, dry_run=False))
    assert (tmp_path / "legacy_timestamp.md").read_bytes() == result.after.encode("utf-8")


def test_a_write_skips_a_document_it_did_not_change(tmp_path):
    loaded = from_edge(tmp_path, "legacy_conflict.md")
    before = (tmp_path / "legacy_conflict.md").read_bytes()
    result = only(migrate(loaded, dry_run=False))
    assert not result.changed
    assert (tmp_path / "legacy_conflict.md").read_bytes() == before


def test_an_unrecognized_actor_is_a_caller_error(tmp_path):
    loaded = from_edge(tmp_path, "legacy_timestamp.md")
    with pytest.raises(ValueError, match="human:"):
        migrate(loaded, actor="pat")


@pytest.mark.parametrize("actor", ["human:pat", "process:okf-io-migrate", "acme/1.2"])
def test_every_actor_convention_is_accepted(tmp_path, actor):
    loaded = from_edge(tmp_path, "legacy_timestamp.md")
    assert only(migrate(loaded, actor=actor)).changes[0].actor == actor


def test_an_unparseable_document_is_reported_not_raised(tmp_path):
    loaded = from_edge(tmp_path, "malformed_yaml.md")
    result = only(migrate(loaded))
    assert not result.changed
    assert [u.reason for u in result.unmigrated] == ["unparseable"]


def test_changed_compares_and_diff_renders(tmp_path):
    loaded = from_edge(tmp_path, "legacy_timestamp.md")
    result = only(migrate(loaded))
    assert result.changed
    assert result.diff().startswith("--- a/legacy_timestamp.md")
    assert "+generated:" in result.diff()


# --- rewrite A -------------------------------------------------------------


def test_the_timestamp_value_passes_through_verbatim(tmp_path):
    """The design spec §9 risk, pinned to exact bytes.

    `edge/legacy_timestamp.md` writes an unquoted `2024-01-15T10:00:00Z`, which
    ruamel parses into a `TimeStamp`. That object re-emits its own source
    representation, so the instant crosses the rewrite unchanged in both value
    and spelling. A ruamel bump that breaks this fails here, loudly, rather
    than silently reformatting every migrated document's provenance.
    """
    loaded = from_edge(tmp_path, "legacy_timestamp.md")
    after = only(migrate(loaded)).after
    assert "  at: 2024-01-15T10:00:00Z\n" in after
    assert "timestamp:" not in after


def test_a_quoted_timestamp_keeps_its_quotes(tmp_path):
    loaded = make(
        tmp_path,
        {"a.md": ("---\ntype: Metric\ntitle: T\ntimestamp: '2026-05-28T23:32:40+00:00'\n---\n\n# T\n")},
    )
    assert "  at: '2026-05-28T23:32:40+00:00'\n" in only(migrate(loaded)).after


def test_generated_is_inserted_at_its_preferred_position(tmp_path):
    loaded = make(
        tmp_path,
        {"a.md": ("---\ntype: Metric\ntitle: T\nstatus: stable\ntimestamp: '2024-01-01'\n---\n\n# T\n")},
    )
    after = only(migrate(loaded)).after
    keys = [line.split(":")[0] for line in after.splitlines() if line and not line.startswith(" ")]
    assert keys[:5] == ["---", "type", "title", "status", "generated"]


def test_the_default_actor_names_the_process(tmp_path):
    loaded = from_edge(tmp_path, "legacy_timestamp.md")
    result = only(migrate(loaded))
    assert DEFAULT_ACTOR == "process:okf-io-migrate"
    assert f"  by: {DEFAULT_ACTOR}\n" in result.after
    assert result.changes[0].kind == "generated"
    assert result.changes[0].actor == DEFAULT_ACTOR


def test_a_partial_generated_block_keeps_its_author(tmp_path):
    """The fallback fires on `generated: { by }` with no `at`.

    Inserting a fresh block there would overwrite an authored `by` with the
    migration process — lossy, and "never lossy" is the guarantee this
    rewriter inherits. The `at` is filled in place instead.
    """
    loaded = from_edge(tmp_path, "legacy_generated_partial.md")
    after = only(migrate(loaded)).after
    assert "  by: human:pat\n" in after
    assert "  at: '2024-01-01T00:00:00Z'\n" in after
    assert "timestamp:" not in after
    assert "status: stable\n" in after


def test_a_partial_generated_block_reports_the_author_it_kept(tmp_path):
    loaded = from_edge(tmp_path, "legacy_generated_partial.md")
    assert only(migrate(loaded)).changes[0].actor == "human:pat"


def test_an_empty_generated_block_is_filled_with_the_default_actor(tmp_path):
    """`generated: {}` beside a `timestamp` -- the fill-in-place shape with no author to keep.

    Unlike the partial case, there is no authored `by` to preserve: the block
    is present but empty, so both fields come from the migration itself.
    """
    loaded = make(
        tmp_path,
        {"a.md": ("---\ntype: Metric\ntitle: T\ntimestamp: '2024-01-01T00:00:00Z'\ngenerated: {}\n---\n\n# T\n")},
    )
    result = only(migrate(loaded))
    assert f"  by: {DEFAULT_ACTOR}\n" in result.after
    assert "  at: '2024-01-01T00:00:00Z'\n" in result.after
    assert result.changes[0].actor == DEFAULT_ACTOR


def test_a_blank_timestamp_carries_nothing_to_migrate(tmp_path):
    """The read fallback fires on `timestamp: ''`; `_rules.legacy` ignores it.

    The writer follows the validator: migrating a blank would launder a broken
    value into v0.2 shape and silence the warning that says so.
    """
    loaded = from_edge(tmp_path, "legacy_timestamp_blank.md")
    assert migrate(loaded) == ()


def test_a_whitespace_only_timestamp_is_also_left_alone(tmp_path):
    loaded = make(tmp_path, {"a.md": "---\ntype: Metric\ntitle: T\ntimestamp: '   '\n---\n\n# T\n"})
    assert migrate(loaded) == ()


@pytest.mark.parametrize(
    "timestamp_line",
    [
        "timestamp: 42\n",
        "timestamp: true\n",
        "timestamp:\n  - 2024\n",
    ],
)
def test_an_uncoercible_timestamp_is_reported_not_silently_declined(tmp_path, timestamp_line):
    """A blank string is the only value with nothing to *report*.

    An int, a bool, and a list all fire the read fallback -- the builder only
    tests `is not None` -- but none of them is a shape a reader can coerce to
    an instant, so there is still nothing to migrate: writing one into
    `generated.at` would silence `legacy.timestamp` while handing the document
    a value that fails to coerce, the same laundering the blank guard exists
    to prevent. Unlike the blank case, `legacy.py` keeps warning on these, so
    `migrate()` must report the decline too -- a caller hearing nothing would
    be left with a warning no result explains.
    """
    loaded = make(tmp_path, {"a.md": f"---\ntype: Metric\ntitle: T\n{timestamp_line}---\n\n# T\n"})
    result = only(migrate(loaded))
    assert not result.changed
    assert [u.reason for u in result.unmigrated] == ["not-an-instant"]


def test_the_not_an_instant_message_names_the_offending_value(tmp_path):
    loaded = make(tmp_path, {"a.md": "---\ntype: Metric\ntitle: T\ntimestamp: 42\n---\n\n# T\n"})
    result = only(migrate(loaded))
    assert result.unmigrated[0].reason == "not-an-instant"
    assert "42" in result.unmigrated[0].message
    assert "human" in result.unmigrated[0].message


@pytest.mark.parametrize(
    "value",
    [
        42,
        True,
        [2024],
        "",
        "   ",
        "not-a-timestamp",
        "2026-05-28T23:32:40+00:00",
        "2024-01-01",
        datetime(2024, 1, 1, 10, 0, 0),
        date(2024, 1, 1),
        Document.parse(read(EDGE / "legacy_timestamp.md")).fm_raw["timestamp"],
    ],
    ids=[
        "int",
        "bool",
        "list",
        "blank-string",
        "whitespace-only-string",
        "iso-unparseable-string",
        "quoted-iso-string",
        "date-only-string",
        "datetime",
        "date",
        "ruamel-timestamp",
    ],
)
def test_is_instant_agrees_with_as_timestamp(value):
    """`_is_instant` must never drift from what `models._as_timestamp` accepts.

    They are two independent implementations of the same question -- "is this
    a real instant" -- and nothing else keeps them in step. If a future ADR
    widens or narrows what the reader coerces (epoch ints, say) and this guard
    is not updated to match, `_migrate_timestamp`'s trigger stops matching the
    reader's fallback: the rewriter would silently resume laundering values
    the reader still can't read, or start declining ones it now can. That is
    the same reader/writer-disagreement risk the module docstring calls the
    spine of the design, and this test is what turns a silent drift into a
    loud, local failure instead of one discovered downstream.

    The predicate compared against `_is_instant` is "no coercion failure was
    recorded" by `_as_timestamp`, not "a rendered value came back": those
    differ for exactly one case here. An ISO-unparseable string still comes
    back as `_as_timestamp`'s `rendered` value -- only `failures` records that
    it did not actually parse. Comparing on `rendered is not None` instead
    would make this test pass vacuously for that case.
    """
    failures: set[str] = set()
    _as_timestamp(value, "generated.at", failures)
    assert _is_instant(value) == (not failures)


def test_a_timestamp_beside_a_real_generated_at_is_reported_not_resolved(tmp_path):
    """§3. Two provenance claims may name different instants."""
    loaded = from_edge(tmp_path, "legacy_conflict.md")
    result = only(migrate(loaded))
    assert not result.changed
    assert result.changes == ()
    assert [u.reason for u in result.unmigrated] == ["conflicting-provenance"]


def test_a_non_mapping_generated_is_reported_not_overwritten(tmp_path):
    loaded = make(
        tmp_path,
        {"a.md": ("---\ntype: Metric\ntitle: T\ntimestamp: '2024-01-01'\ngenerated: yesterday\n---\n\n# T\n")},
    )
    result = only(migrate(loaded))
    assert not result.changed
    assert [u.reason for u in result.unmigrated] == ["conflicting-provenance"]


def test_a_document_with_no_timestamp_at_all_is_untouched(tmp_path):
    loaded = make(tmp_path, {"a.md": "---\ntype: Metric\ntitle: T\ntimestamp:\n---\n\n# T\n"})
    assert migrate(loaded) == ()


# --- rewrite B -------------------------------------------------------------


def test_a_linked_list_becomes_sources_and_footnote_definitions(tmp_path):
    loaded = from_edge(tmp_path, "legacy_citations.md")
    result = only(migrate(loaded))
    assert result.after == (
        "---\n"
        "type: Metric\n"
        "title: Legacy citations\n"
        "sources:\n"
        "  - title: Revenue Recognition Policy (FY2026)\n"
        "    resource: policies/revenue-recognition.md\n"
        "    id: revenue-recognition-policy-fy2026\n"
        "  - title: Cost Allocation & Margin Standard\n"
        "    resource: policies/margin-standard.md\n"
        "    id: cost-allocation-margin-standard\n"
        "---\n"
        "\n"
        "# Definition\n"
        "\n"
        "Revenue follows the FY2026 policy.\n"
        "\n"
        "[^revenue-recognition-policy-fy2026]: [Revenue Recognition Policy (FY2026)]"
        "(policies/revenue-recognition.md)\n"
        "[^cost-allocation-margin-standard]: [Cost Allocation & Margin Standard]"
        "(policies/margin-standard.md)\n"
    )
    assert result.changes[0].kind == "sources"
    assert result.changes[0].ids == (
        "revenue-recognition-policy-fy2026",
        "cost-allocation-margin-standard",
    )


def test_the_deletion_does_not_double_an_existing_blank_line(tmp_path):
    """The line above the definitions is the one the deletion already left."""
    loaded = from_edge(tmp_path, "legacy_citations.md")
    assert "\n\n\n" not in only(migrate(loaded)).after


def test_the_bracketed_number_dialect_migrates(tmp_path):
    loaded = from_edge(tmp_path, "legacy_citations_numbered.md")
    after = only(migrate(loaded)).after
    assert "  - title: Bitcoin Transactions\n" in after
    assert "    resource: https://x.example/transactions\n" in after
    assert "[^bitcoin-etl]: [Bitcoin ETL](https://github.com/blockchain-etl/bitcoin-etl)\n" in after
    assert "# Citations" not in after


def test_a_bare_url_item_emits_a_resource_with_no_title(tmp_path):
    """§5.2. `_MD` runs with linkify off, so this arrives as text, not a link.

    The reader models it `Source(title=text, resource=text)`; writing that
    title back would state that the source is called `https://…`.
    """
    loaded = make(
        tmp_path,
        {"a.md": "---\ntype: Reference\ntitle: T\n---\n\n# Citations\n\n- https://x.example/docs\n"},
    )
    after = only(migrate(loaded)).after
    assert "sources:\n  - resource: https://x.example/docs\n    id: https-x-example-docs\n" in after
    assert "title:" not in after.split("---")[1].split("sources:")[1]
    assert "[^https-x-example-docs]: https://x.example/docs\n" in after


def test_a_prose_item_refuses_the_whole_document(tmp_path):
    """§5.4. `resource: See the FY2026 policy binder` is never written."""
    loaded = from_edge(tmp_path, "legacy_citations_prose.md")
    result = only(migrate(loaded))
    assert not result.changed
    assert [u.reason for u in result.unmigrated] == ["not-a-resource"]
    assert "See the FY2026 policy binder" in result.unmigrated[0].message


def test_an_impure_section_refuses_the_whole_document(tmp_path):
    loaded = from_edge(tmp_path, "legacy_citations_impure.md")
    result = only(migrate(loaded))
    assert not result.changed
    assert [u.reason for u in result.unmigrated] == ["impure-section"]


def test_rewrite_a_still_runs_when_rewrite_b_refuses(tmp_path):
    """§5.4's last line: the two rewrites are independent."""
    text = read(EDGE / "legacy_citations_impure.md").replace(
        "title: Legacy impure citations section\n",
        "title: Legacy impure citations section\ntimestamp: '2024-01-01T00:00:00Z'\n",
    )
    loaded = make(tmp_path, {"a.md": text})
    result = only(migrate(loaded))
    assert result.changed
    assert [c.kind for c in result.changes] == ["generated"]
    assert [u.reason for u in result.unmigrated] == ["impure-section"]
    assert "# Citations" in result.after


def test_both_constructs_migrate_in_one_pass(tmp_path):
    loaded = from_edge(tmp_path, "legacy_both.md")
    result = only(migrate(loaded))
    assert [c.kind for c in result.changes] == ["generated", "sources"]
    assert "timestamp:" not in result.after
    assert "# Citations" not in result.after


def test_an_id_colliding_with_an_existing_footnote_label_is_suffixed(tmp_path):
    """§5.5. A bare collision would silently join an unrelated footnote."""
    loaded = from_edge(tmp_path, "legacy_citations_collision.md")
    after = only(migrate(loaded)).after
    assert "    id: revenue-recognition-policy-2\n" in after
    assert "[^revenue-recognition-policy]: An unrelated note the author already wrote.\n" in after
    assert "[^revenue-recognition-policy-2]: [Revenue Recognition Policy]" in after


def test_sibling_id_collisions_are_suffixed_in_order(tmp_path):
    body = "# Citations\n\n- [Policy](a.md)\n- [Policy](b.md)\n- [Policy](c.md)\n"
    loaded = make(tmp_path, {"a.md": f"---\ntype: Metric\ntitle: T\n---\n\n{body}"})
    assert only(migrate(loaded)).changes[0].ids == ("policy", "policy-2", "policy-3")


def test_an_unsluggable_title_falls_back_to_its_position(tmp_path):
    body = "# Citations\n\n- [Policy](a.md)\n- [—](b.md)\n"
    loaded = make(tmp_path, {"a.md": f"---\ntype: Metric\ntitle: T\n---\n\n{body}"})
    assert only(migrate(loaded)).changes[0].ids == ("policy", "source-2")


def test_ids_are_deterministic_across_runs(tmp_path):
    """§5.5: no clock, no randomness. Migrating twice yields the same ids."""
    first = only(migrate(from_edge(tmp_path / "one", "legacy_citations.md"))).changes[0].ids
    second = only(migrate(from_edge(tmp_path / "two", "legacy_citations.md"))).changes[0].ids
    assert first == second


def test_an_empty_citations_section_fires_no_fallback_and_is_left_alone(tmp_path):
    """No entries means no `sources` fallback, so there is nothing to migrate.

    The heading stays. Removing it would be the rewriter acting on something
    the read fallback never fired on, which is the one thing §3 forbids.
    """
    loaded = from_edge(tmp_path, "legacy_citations_empty.md")
    assert migrate(loaded) == ()


def test_a_citations_section_beside_real_sources_reports_nothing(tmp_path):
    """§3's last row. This is the already-migrated shape, and nothing is wrong."""
    loaded = from_edge(tmp_path, "legacy_migrated.md")
    assert migrate(loaded) == ()


def test_crlf_line_endings_survive_the_rewrite(tmp_path):
    loaded = from_edge(tmp_path, "encoding/legacy_crlf.md")
    after = only(migrate(loaded)).after
    assert after.count("\n") == after.count("\r\n")
    assert "[^revenue-recognition-policy]: " in after
    assert "# Citations" not in after


def test_the_section_is_deleted_up_to_the_next_heading(tmp_path):
    body = "# Citations\n\n- [Policy](p.md)\n\n# Notes\n\nKeep me.\n"
    loaded = make(tmp_path, {"a.md": f"---\ntype: Metric\ntitle: T\n---\n\n{body}"})
    after = only(migrate(loaded)).after
    assert after.endswith("# Notes\n\nKeep me.\n\n[^policy]: [Policy](p.md)\n")
    assert "# Citations" not in after


def test_a_document_that_is_nothing_but_a_citations_section_survives(tmp_path):
    loaded = make(
        tmp_path,
        {"a.md": "---\ntype: Metric\ntitle: T\n---\n\n# Citations\n\n- [Policy](p.md)\n"},
    )
    after = only(migrate(loaded)).after
    assert after.endswith("---\n\n[^policy]: [Policy](p.md)\n")


def test_sources_lands_at_its_preferred_position(tmp_path):
    text = (
        "---\ntype: Metric\ntitle: T\nstale_after: '2027-01-01'\n"
        "usage_window:\n  from: '2026-01-01'\n"
        "---\n\n# Citations\n\n- [Policy](p.md)\n"
    )
    loaded = make(tmp_path, {"a.md": text})
    after = only(migrate(loaded)).after
    assert after.index("sources:") > after.index("stale_after:")
    assert after.index("sources:") < after.index("usage_window:")


def test_a_second_citations_section_refuses_the_whole_document(tmp_path):
    """Finding 3: the locator's `stop` ends at the next heading, so migrating
    would delete only the first section and leave the second stranded --
    `sources` becomes authored, the fallback stops firing, and nothing can
    ever see the second section again. All-or-nothing refuses before either
    section is touched.
    """
    body = "# Citations\n\n- [A](a.md)\n\n# Notes\n\nSome prose in between.\n\n# Citations\n\n- [B](b.md)\n"
    loaded = make(tmp_path, {"a.md": f"---\ntype: Metric\ntitle: T\n---\n\n{body}"})
    result = only(migrate(loaded))
    assert not result.changed
    assert result.after == result.before
    assert [u.reason for u in result.unmigrated] == ["multiple-citations-sections"]
    assert result.after.count("# Citations") == 2
    assert "- [A](a.md)" in result.after
    assert "- [B](b.md)" in result.after


def test_the_multiple_sections_message_says_a_human_must_consolidate(tmp_path):
    body = "# Citations\n\n- [A](a.md)\n\n# Citations\n\n- [B](b.md)\n"
    loaded = make(tmp_path, {"a.md": f"---\ntype: Metric\ntitle: T\n---\n\n{body}"})
    result = only(migrate(loaded))
    assert "more than one" in result.unmigrated[0].message
    assert "consolidate" in result.unmigrated[0].message


def test_a_blockquoted_second_citations_heading_does_not_trigger_the_refusal(tmp_path):
    """A quoted heading is somebody else's content, exactly as `_md.citations_section`
    already treats it for both the anchor and the stop boundary -- this refusal
    must not undo that adjudicated behaviour by counting a blockquoted heading
    as a second real section. Placed inside an unrelated later section (rather
    than the one being migrated) so it also cannot make that section impure --
    a blockquoted heading `test_a_blockquoted_heading_inside_a_real_section_does_not_truncate_it`
    already proves does, for the separate reason this test is not about.
    """
    body = "# Citations\n\n- [A](a.md)\n\n# Notes\n\n> # Citations\n> quoted, not real\n"
    loaded = make(tmp_path, {"a.md": f"---\ntype: Metric\ntitle: T\n---\n\n{body}"})
    result = only(migrate(loaded))
    assert result.changed
    assert result.changes[0].kind == "sources"
    assert result.unmigrated == ()
