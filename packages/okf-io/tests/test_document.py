from __future__ import annotations

import difflib
import json
from datetime import date, datetime

import pytest
from helpers import BUNDLES, EDGE, all_concept_files, fixture_id, read
from okf_io import _yaml
from okf_io.document import Document, parse, rendered_with_body


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("malformed_unterminated.md", "unterminated"),
        ("malformed_yaml.md", "yaml"),
        ("malformed_not_mapping.md", "not-a-mapping"),
    ],
)
def test_malformed_fixtures_report_their_kind(name, kind):
    doc = Document.parse(read(EDGE / name))
    assert doc.parse_error is not None
    assert doc.parse_error.kind == kind
    assert len(doc.fm_raw) == 0


def test_well_formed_document_has_no_parse_error():
    doc = Document.parse(read(EDGE / "dialect_flow.md"))
    assert doc.parse_error is None
    assert doc.fm.type == "Reference"


def test_no_frontmatter_is_not_an_error():
    doc = Document.parse(read(EDGE / "no_frontmatter.md"))
    assert doc.parse_error is None
    assert len(doc.fm_raw) == 0
    assert doc.body.startswith("# Subdirectories")


@pytest.mark.parametrize("path", all_concept_files(), ids=fixture_id)
def test_clean_document_serializes_to_original_bytes(path):
    text = read(path)
    assert Document.parse(text).serialize() == text


def test_load_propagates_oserror(tmp_path):
    with pytest.raises(OSError):
        Document.load(tmp_path / "does-not-exist.md")


def test_load_and_save_roundtrip(tmp_path):
    src = BUNDLES / "acme_retail/metrics/revenue.md"
    target = tmp_path / "revenue.md"
    target.write_bytes(src.read_bytes())
    doc = Document.load(target)
    doc.save()
    assert target.read_bytes() == src.read_bytes()


def test_fm_data_iso_survives_json_dumps():
    doc = Document.parse(read(BUNDLES / "acme_retail/tables/orders.md"))
    data = doc.fm_data()
    encoded = json.dumps(data)  # no custom encoder, on purpose
    assert json.loads(encoded)["usage_window"]["from"] == "2026-04-01"
    assert isinstance(data["sources"][0]["usage_count"], int)


def test_fm_data_native_keeps_date_objects():
    doc = Document.parse(read(EDGE / "dates_preparsed.md"))
    data = doc.fm_data(dates="native")
    assert isinstance(data["stale_after"], date)
    assert isinstance(data["generated"]["at"], datetime)


def test_fm_data_flattens_ruamel_types():
    doc = Document.parse(read(BUNDLES / "acme_retail/metrics/revenue.md"))
    data = doc.fm_data()
    assert type(data) is dict
    assert type(data["tags"]) is list
    assert all(type(t) is str for t in data["tags"])


def test_view_is_memoized_and_refreshable():
    doc = Document.parse(read(EDGE / "dialect_flow.md"))
    assert doc.fm is doc.fm
    doc.refresh()
    assert doc.fm.type == "Reference"


def test_refresh_actually_rebuilds_the_view():
    """Asserting only a value cannot distinguish a rebuild from a no-op."""
    doc = Document.parse(read(EDGE / "dialect_flow.md"))
    first = doc.fm
    doc.fm_raw["type"] = "Metric"

    assert doc.fm is first, "still memoized before refresh"
    assert doc.fm.type == "Reference", "the cached view predates the mutation"

    doc.refresh()
    assert doc.fm is not first
    assert doc.fm.type == "Metric"


def test_mark_dirty_makes_serialize_emit_the_mutation():
    """The dirty path end to end. Task 9 replaces the branch this exercises."""
    text = read(EDGE / "dialect_indented.md")
    doc = Document.parse(text)
    doc.fm_raw["title"] = "Changed"

    assert doc.serialize() == text, "an undeclared mutation must not be written"

    doc.mark_dirty()
    out = doc.serialize()
    assert out != text
    assert "title: Changed" in out
    assert Document.parse(out).fm.title == "Changed"


def test_save_without_mark_dirty_writes_the_original_bytes(tmp_path):
    """Pins the documented hazard so it stays a decision, not a surprise."""
    src = EDGE / "dialect_indented.md"
    target = tmp_path / "doc.md"
    target.write_bytes(src.read_bytes())

    doc = Document.load(target)
    doc.fm_raw["title"] = "Changed"
    doc.refresh()
    doc.save()

    assert doc.fm.title == "Changed", "the view reflects the edit"
    assert target.read_bytes() == src.read_bytes(), "but save reverts it"


def test_parse_survives_frontmatter_nested_too_deeply():
    """A bundle walk must survive one pathological file."""
    deep = "".join(f"{' ' * (2 * i)}k{i}:\n" for i in range(400))
    doc = Document.parse(f"---\n{deep}---\n\n# Body\n")
    assert doc.parse_error is not None
    assert len(doc.fm_raw) == 0


def changed_lines(before: str, after: str) -> list[str]:
    diff = list(
        difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), n=0)
    )
    return [line for line in diff[2:] if line.startswith(("+", "-"))]


def test_set_existing_key_edits_in_place():
    text = read(BUNDLES / "acme_retail/metrics/revenue.md")
    doc = Document.parse(text)
    doc.set("status", "deprecated")
    changed = changed_lines(text, doc.serialize())
    assert changed == ["-status: stable\n", "+status: deprecated\n"]


def test_mutation_does_not_reflow_prefolded_scalars():
    """The ga4 dialect pre-folds long plain scalars; ruamel would un-fold them."""
    text = read(BUNDLES / "ga4/references/metrics/purchasers.md")
    doc = Document.parse(text)
    doc.set("status", "deprecated")
    changed = changed_lines(text, doc.serialize())
    assert changed == ["+status: deprecated\n"]
    assert "completed a purchase or\n" in doc.serialize()


def test_new_key_lands_after_the_last_preceding_preferred_key():
    text = read(EDGE / "dialect_indented.md")
    doc = Document.parse(text)
    doc.set("status", "stable")
    keys = list(doc.fm_raw.keys())
    assert keys.index("status") == keys.index("tags") + 1


def test_new_key_with_nothing_preceding_goes_to_the_front():
    doc = Document.parse(read(EDGE / "footnotes_join.md"))
    doc.delete("type")
    doc.set("type", "Metric")
    assert next(iter(doc.fm_raw.keys())) == "type"


def test_unknown_key_is_appended():
    doc = Document.parse(read(EDGE / "dialect_indented.md"))
    doc.set("custom_extension", "value")
    assert list(doc.fm_raw.keys())[-1] == "custom_extension"


def test_existing_keys_are_never_reordered():
    text = read(BUNDLES / "acme_retail/tables/orders.md")
    doc = Document.parse(text)
    before = list(doc.fm_raw.keys())
    doc.set("status", "deprecated")
    assert list(doc.fm_raw.keys()) == before


def test_delete_removes_only_that_line():
    text = read(EDGE / "dialect_flow.md")
    doc = Document.parse(text)
    doc.set("status", "stable")
    doc.serialize()
    doc2 = Document.parse(doc.serialize())
    before = doc2.serialize()
    doc2.delete("status")
    assert changed_lines(before, doc2.serialize()) == ["-status: stable\n"]


def test_mutating_a_malformed_document_raises():
    doc = Document.parse(read(EDGE / "malformed_yaml.md"))
    with pytest.raises(ValueError, match="failed to parse"):
        doc.set("status", "stable")
    with pytest.raises(ValueError, match="failed to parse"):
        doc.delete("status")


def test_mutating_a_document_without_frontmatter_synthesizes_a_block():
    doc = Document.parse(read(EDGE / "no_frontmatter.md"))
    doc.set("type", "Index")
    out = doc.serialize()
    assert out.startswith("---\ntype: Index\n---\n\n")
    assert "# Subdirectories" in out
    assert Document.parse(out).fm.type == "Index"


def test_mark_dirty_pairs_with_direct_fm_raw_mutation():
    text = read(EDGE / "dialect_indented.md")
    doc = Document.parse(text)
    doc.fm_raw["sources"][0]["resource"] = "https://example.com/changed"
    assert doc.serialize() == text, "no mark_dirty() means no write"
    doc.mark_dirty()
    changed = changed_lines(text, doc.serialize())
    assert changed == [
        "-    resource: https://example.com/one\n",
        "+    resource: https://example.com/changed\n",
    ]


def test_synthesized_frontmatter_matches_a_crlf_body():
    """With no frontmatter there is nothing to sniff but the body."""
    doc = Document.parse("# Title\r\n\r\nSome body.\r\n")
    doc.set("type", "Index")
    out = doc.serialize()

    assert out.startswith("---\r\ntype: Index\r\n---\r\n")
    assert "\n" not in out.replace("\r\n", "")
    assert Document.parse(out).fm.type == "Index"


def test_two_edits_do_not_drop_an_untouched_key_between_them():
    """Regression: the splice must never delete a line no edit touched.

    `title` here carries trailing whitespace, which ruamel strips when it
    renders the pristine data. That makes the line an unmatched gap between the
    two edited keys, and an end-boundary that snaps across the gap consumes it.
    """
    text = "---\ntype: Metric\ntitle: Something   \nstatus: stable\ntags:\n- a\n---\n\nBody.\n"
    doc = Document.parse(text)
    doc.set("type", "Reference")
    doc.set("status", "deprecated")
    out = doc.serialize()

    reparsed = Document.parse(out)
    assert reparsed.parse_error is None
    assert list(reparsed.fm_raw.keys()) == ["type", "title", "status", "tags"]
    assert reparsed.fm_raw["title"] == "Something"
    assert reparsed.fm_raw["type"] == "Reference"
    assert reparsed.fm_raw["status"] == "deprecated"


def test_splice_declines_when_the_edited_line_is_not_anchored():
    """The safety valve, exercised deliberately.

    ga4 pre-folds long plain scalars at ~80 columns; ruamel un-folds them, so
    the rendered `description` line has no counterpart in the source. Splicing
    an edit onto it would misattribute the edit. Declining is correct: the
    output is fully re-emitted, so it is valid and carries the new value, and
    only the diff is larger.
    """
    text = read(BUNDLES / "ga4/references/metrics/purchasers.md")
    doc = Document.parse(text)
    doc.set("description", "Short.")
    out = doc.serialize()

    reparsed = Document.parse(out)
    assert reparsed.parse_error is None
    assert reparsed.fm_raw["description"] == "Short."
    # Every original key survives -- no duplicate, nothing eaten.
    assert list(reparsed.fm_raw) == list(Document.parse(text).fm_raw)
    assert out.count("description:") == 1


def test_splice_returning_none_falls_back_to_full_reemission(monkeypatch):
    """Pin the fallback branch itself, independent of any dialect."""
    from okf_io import document as document_module

    monkeypatch.setattr(document_module, "_splice", lambda *_: None)

    text = read(EDGE / "dialect_indented.md")
    doc = Document.parse(text)
    doc.set("status", "deprecated")
    out = doc.serialize()

    reparsed = Document.parse(out)
    assert reparsed.parse_error is None
    assert reparsed.fm.status == "deprecated"
    assert reparsed.fm.title == Document.parse(text).fm.title


def test_body_line_offset_is_zero_without_frontmatter():
    assert parse("# Definition\n\nText.\n").body_line_offset == 0


def test_body_line_offset_is_zero_for_a_bom_with_no_frontmatter():
    """A BOM is not a line. `_lines` would otherwise count the fragment."""
    assert parse("﻿# Definition\n").body_line_offset == 0


def test_body_line_offset_counts_delimiters_and_gap():
    text = "---\ntype: Metric\n---\n\n# Definition\n"
    assert parse(text).body_line_offset == 4


@pytest.mark.parametrize("name", ["encoding/bom.md", "encoding/crlf.md", "no_frontmatter.md"])
def test_body_starts_where_the_offset_says(name):
    """The property that makes a finding's line number trustworthy."""
    doc = Document.parse(read(EDGE / name))
    # Reaching into _yaml._lines is deliberate: it is the only CRLF/CR-aware
    # splitter available, and reimplementing it would duplicate the logic under test.
    lines = _yaml._lines(doc.raw_text)
    body_lines = _yaml._lines(doc.body)
    assert body_lines, f"Fixture {name} must have a non-empty body"
    assert lines[doc.body_line_offset] == body_lines[0]


def test_has_frontmatter_separates_empty_from_absent():
    assert Document.parse(read(EDGE / "empty_frontmatter.md")).has_frontmatter is True
    assert Document.parse(read(EDGE / "no_frontmatter.md")).has_frontmatter is False


def test_citations_inside_a_fence_no_longer_end_the_scan():
    """Closes the prior child's SEAM: the regex scanner stopped at a `#` inside a fence."""
    body = "# Citations\n\n```\n# Not a heading\n```\n\n- [Policy](p.md)\n"
    doc = Document.parse(f"---\ntype: Metric\n---\n\n{body}")
    assert [s.resource for s in doc.fm.sources] == ["p.md"]


def test_a_lazily_continued_citation_item_keeps_its_whole_text():
    """Token stream includes continuation lines; regex scanner truncated at the first."""
    body = "# Citations\n\n- some text\n  continued more text\n"
    doc = Document.parse(f"---\ntype: Metric\n---\n\n{body}")
    assert len(doc.fm.sources) == 1
    assert doc.fm.sources[0].resource == "some text\ncontinued more text"
    assert doc.fm.sources[0].title == "some text\ncontinued more text"


def test_an_item_inside_a_fenced_block_is_not_collected():
    """Items inside fences are not visited by the token walk."""
    body = "# Citations\n\n```\n- [Link](x.md)\n```\n"
    doc = Document.parse(f"---\ntype: Metric\n---\n\n{body}")
    assert len(doc.fm.sources) == 0


def test_set_body_writes_through_to_the_split():
    """`_yaml.join` reassembles from `_split.body`, so assigning `body` alone
    leaves `save()` writing the pre-edit body."""
    doc = Document.parse("---\ntype: Log\n---\n\n# H\n")
    doc.set_body("# H\n\n## 2026-01-01\n")
    assert doc.serialize() == "---\ntype: Log\n---\n\n# H\n\n## 2026-01-01\n"


def test_set_body_on_a_frontmatterless_document_adds_no_delimiters():
    """Every index file is frontmatter-less; a body write must not staple one on."""
    doc = Document.parse("# Metric\n\n* [a](a.md)\n")
    doc.set_body("# Metric\n\n* [a](a.md)\n* [b](b.md)\n")
    assert doc.serialize() == "# Metric\n\n* [a](a.md)\n* [b](b.md)\n"


def test_set_body_leaves_frontmatter_byte_identical():
    text = read(BUNDLES / "acme_retail" / "log.md")
    doc = Document.parse(text)
    doc.set_body(doc.body + "\n## 2026-08-01\n\n- Appended.\n")
    before_fm, _, _ = text.partition("\n---\n")
    after_fm, _, _ = doc.serialize().partition("\n---\n")
    assert after_fm == before_fm


def test_set_body_refuses_a_document_that_failed_to_parse():
    doc = Document.parse(read(EDGE / "malformed_yaml.md"))
    with pytest.raises(ValueError, match="Cannot mutate"):
        doc.set_body("# New\n")


def test_rendered_with_body_does_not_mutate_the_document():
    """The writers default to `dry_run=True` and are handed documents that
    belong to a shared Bundle; reading the "after" text must not edit them."""
    text = "# Metric\n\n* [a](a.md)\n"
    doc = Document.parse(text)
    rendered = rendered_with_body(doc, "# Metric\n\n* [a](a.md)\n* [b](b.md)\n")
    assert rendered == "# Metric\n\n* [a](a.md)\n* [b](b.md)\n"
    assert doc.body == "# Metric\n\n* [a](a.md)\n"
    assert doc.serialize() == text
