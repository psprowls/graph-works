from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from helpers import BUNDLES, FIXTURES, read, write
from okf_io import Document, log


def load(path: Path) -> Document:
    return Document.parse(read(path), path=path)


ACME = BUNDLES / "acme_retail" / "log.md"
NONCONFORMANT = FIXTURES / "nonconformant" / "log.md"


def test_parse_reads_every_dated_section_newest_first():
    parsed = log.parse(load(ACME))
    assert [section.date for section in parsed.sections] == [
        date(2026, 7, 1),
        date(2026, 6, 30),
        date(2026, 4, 15),
        date(2026, 2, 10),
    ]
    assert all(len(section.entries) == 1 for section in parsed.sections)
    assert parsed.sections[0].entries[0].text.startswith("**Verified** the full bundle")


def test_parse_tolerates_a_heading_that_is_not_a_date():
    """§9 says headings MUST be ISO dates, but parsing is not the place to
    reject: `reserved.log-heading-not-date` already reports it, and a parser
    that raised would make the writer unable to append to the very file the
    rule is warning about."""
    parsed = log.parse(load(NONCONFORMANT))
    assert [(s.heading, s.date) for s in parsed.sections] == [
        ("2026-07-01", date(2026, 7, 1)),
        ("Q3 2026", None),
    ]


def test_entries_are_attributed_by_line_not_by_heading_text():
    doc = Document.parse("# H\n\n## 2026-01-01\n\n- a\n\n## 2026-01-01\n\n- b\n")
    parsed = log.parse(doc)
    assert [[entry.text for entry in s.entries] for s in parsed.sections] == [["a"], ["b"]]


def test_a_section_with_no_entries_parses():
    parsed = log.parse(Document.parse("## 2026-01-01\n"))
    assert parsed.sections[0].entries == ()


def test_a_log_with_no_sections_parses():
    assert log.parse(Document.parse("# Bundle history\n")).sections == ()


def test_on_finds_a_section_by_date():
    parsed = log.parse(load(ACME))
    found = parsed.on(date(2026, 4, 15))
    assert found is not None and found.heading == "2026-04-15"
    assert parsed.on(date(1999, 1, 1)) is None


def test_iso_date_accepts_only_the_strict_form():
    assert log.iso_date("2026-07-01") == date(2026, 7, 1)
    assert log.iso_date("  2026-07-01  ") == date(2026, 7, 1)
    assert log.iso_date("20260701") is None
    assert log.iso_date("2026-W27-3") is None
    assert log.iso_date("2026-13-45") is None
    assert log.iso_date("Q3 2026") is None


def test_entry_lines_are_body_relative_and_inclusive():
    doc = load(ACME)
    entry = log.parse(doc).sections[0].entries[0]
    assert entry.line == entry.end
    assert doc.body.splitlines()[entry.line - 1].startswith("- **Verified**")


def test_parse_preserves_document_order():
    """Document order is the invariant contract. Callers needing chronological
    order must sort themselves. `_new_section_anchor` assumes document order
    and silently breaks if sections are sorted by date instead."""
    # Deliberately oldest-first, not newest-first
    doc = Document.parse("# H\n\n## 2026-01-01\n\n- Oldest.\n\n## 2026-06-01\n\n- Newest.\n")
    parsed = log.parse(doc)
    assert [section.date for section in parsed.sections] == [
        date(2026, 1, 1),
        date(2026, 6, 1),
    ]


NEW = "**Update** appended by a test."


def append(text: str, **kwargs) -> log.LogAppend:
    return log.append(Document.parse(text), NEW, **kwargs)


BASE = (
    "---\ntype: Log\n---\n\n"
    "# Bundle history\n"
    "\n"
    "## 2026-07-01\n"
    "\n"
    "- **Verified** the bundle.\n"
    "\n"
    "## 2026-04-15\n"
    "\n"
    "- **Deprecated** the legacy metric.\n"
)


def test_appending_to_an_existing_date_section(tmp_path):
    result = append(BASE, on=date(2026, 7, 1))
    assert result.section_created is False
    assert result.after == BASE.replace("- **Verified** the bundle.\n", f"- **Verified** the bundle.\n- {NEW}\n")


def test_appending_a_new_newest_date():
    result = append(BASE, on=date(2026, 8, 1))
    assert result.section_created is True
    assert result.after == BASE.replace("## 2026-07-01\n", f"## 2026-08-01\n\n- {NEW}\n\n## 2026-07-01\n")


def test_backdating_inserts_in_date_order():
    result = append(BASE, on=date(2026, 5, 1))
    assert result.after == BASE.replace("## 2026-04-15\n", f"## 2026-05-01\n\n- {NEW}\n\n## 2026-04-15\n")


def test_backdating_past_every_dated_section_lands_at_the_end():
    result = append(BASE, on=date(2026, 1, 1))
    assert result.after == BASE + f"\n## 2026-01-01\n\n- {NEW}\n"


def test_an_undated_heading_keeps_its_place():
    """§9's insertion positions are relative to dated sections only.

    Also the case that exercises ``_section_end``'s "the section following
    the last dated one, in document order, is itself undated" branch.
    """
    text = "# Bundle history\n\n## 2026-07-01\n\n- Verified nothing.\n\n## Q3 2026\n\n- Deliberately not a date.\n"
    result = append(text, on=date(2026, 6, 1))
    assert result.after == (
        "# Bundle history\n\n## 2026-07-01\n\n- Verified nothing.\n"
        f"\n## 2026-06-01\n\n- {NEW}\n"
        "\n## Q3 2026\n\n- Deliberately not a date.\n"
    )


def test_frontmatter_is_byte_identical_after_an_append():
    doc = Document.parse(read(ACME))
    result = log.append(doc, NEW, on=date(2026, 7, 1))
    assert result.after.partition("\n---\n")[0] == read(ACME).partition("\n---\n")[0]


def test_the_bullet_marker_follows_the_file():
    result = log.append(Document.parse(read(ACME)), NEW, on=date(2026, 7, 1))
    assert f"- {NEW}\n" in result.after
    starred = "# H\n\n## 2026-07-01\n\n* One.\n"
    assert f"* {NEW}\n" in append(starred, on=date(2026, 7, 1)).after


def test_appending_into_a_section_with_no_entries():
    text = "# H\n\n## 2026-07-01\n\n## 2026-04-15\n\n- One.\n"
    result = append(text, on=date(2026, 7, 1))
    assert result.after == f"# H\n\n## 2026-07-01\n\n- {NEW}\n\n## 2026-04-15\n\n- One.\n"


def test_appending_to_a_log_with_no_sections():
    result = append("# Bundle history\n", on=date(2026, 7, 1))
    assert result.after == f"# Bundle history\n\n## 2026-07-01\n\n- {NEW}\n"


def test_today_is_injected_and_on_wins():
    assert "## 2026-08-01" in append(BASE, today=date(2026, 8, 1)).after
    assert "## 2026-08-02" in append(BASE, on=date(2026, 8, 2), today=date(2026, 8, 1)).after


def test_neither_on_nor_today_raises():
    with pytest.raises(ValueError, match="never reads the clock"):
        append(BASE)


def test_dry_run_is_the_default_and_leaves_the_disk_untouched(tmp_path):
    path = tmp_path / "log.md"
    write(path, BASE)
    result = log.append(Document.load(path), NEW, on=date(2026, 7, 1))
    assert path.read_text(encoding="utf-8") == BASE
    assert result.changed is True


def test_dry_run_false_writes_the_file(tmp_path):
    path = tmp_path / "log.md"
    write(path, BASE)
    result = log.append(Document.load(path), NEW, on=date(2026, 7, 1), dry_run=False)
    assert path.read_text(encoding="utf-8") == result.after


def test_writing_a_document_with_no_path_raises():
    with pytest.raises(ValueError, match="no path"):
        log.append(Document.parse(BASE), NEW, on=date(2026, 7, 1), dry_run=False)


def test_crlf_endings_survive_an_append():
    text = "# H\r\n\r\n## 2026-07-01\r\n\r\n- One.\r\n"
    result = append(text, on=date(2026, 7, 1))
    assert result.after == f"# H\r\n\r\n## 2026-07-01\r\n\r\n- One.\r\n- {NEW}\r\n"


def test_out_of_order_dated_sections_raise_instead_of_misfiling():
    """parse() guarantees document order, not date order (see
    test_parse_preserves_document_order above). append() must not silently
    insert into the wrong slot when a hand-edited log is not newest-first --
    it refuses with a clear error instead of guessing.

    *on* is older than every section here, so it is not the one placement
    (newer than everything) that a disordered log still permits -- see
    ``test_appending_newer_than_a_later_inversion_still_succeeds`` for that
    case, and ``test_a_middle_date_into_an_inversion_raises`` for why "day
    beats the *first* section" is not enough either.
    """
    text = "# Bundle history\n\n## 2026-01-01\n\n- Oldest.\n\n## 2026-06-01\n\n- Newest.\n"
    with pytest.raises(ValueError, match=r"not.*newest.first|not.*descending|out of order"):
        append(text, on=date(2020, 1, 1))
    # The message names the offending pair, not just the failure class.
    with pytest.raises(ValueError, match=r"'2026-01-01'.*line 3.*'2026-06-01'.*line 7"):
        append(text, on=date(2020, 1, 1))


def test_appending_newer_than_a_later_inversion_still_succeeds():
    """A day strictly newer than every dated section belongs at the very
    top no matter how disordered the rest of the log is -- nothing before
    that position could ever need to precede it. The inversion between
    2026-04-15 and 2026-05-01 here must not block the append.
    """
    text = "# H\n\n## 2026-07-01\n\n- a\n\n## 2026-04-15\n\n- b\n\n## 2026-05-01\n\n- c\n"
    result = append(text, on=date(2026, 9, 1))
    assert result.after == text.replace("## 2026-07-01\n", f"## 2026-09-01\n\n- {NEW}\n\n## 2026-07-01\n")


def test_a_middle_date_into_an_inversion_raises():
    """Regression for a hole in an earlier version of this guard: scoping
    the order check to only the sections walked before an anchor was found
    let a day landing *between* two out-of-order sections slip through,
    silently stranding the newer one (2026-06-01, written second) below an
    entry that should have sorted beneath it instead. 2026-03-01 is neither
    newer than every section (which would be unambiguous) nor confined to
    an already-ordered prefix, so this must raise.
    """
    text = "# H\n\n## 2026-01-01\n\n- oldest.\n\n## 2026-06-01\n\n- newest, but written second.\n"
    with pytest.raises(ValueError, match=r"'2026-01-01'.*'2026-06-01'"):
        append(text, on=date(2026, 3, 1))


def test_a_middle_date_past_a_single_inversion_raises():
    """*day* newer than the *first* dated section is not sufficient on its
    own -- it must be newer than *all* of them. 2026-08-01 beats
    2026-07-01 (first in document order) but not 2026-09-15 (second, and
    the one actually newest); inserting at the top would strand 2026-09-15
    beneath it, so this must still raise.
    """
    text = "# H\n\n## 2026-07-01\n\n- a\n\n## 2026-09-15\n\n- b\n"
    with pytest.raises(ValueError, match=r"'2026-07-01'.*'2026-09-15'"):
        append(text, on=date(2026, 8, 1))


def test_appending_to_a_log_with_duplicate_date_sections_uses_the_first():
    """Log.on() returns the first match in document order (documented on
    append()'s docstring), so the entry lands in the first of two sections
    sharing a date and the second is untouched."""
    text = "# H\n\n## 2026-07-01\n\n- a\n\n## 2026-07-01\n\n- b\n"
    result = append(text, on=date(2026, 7, 1))
    assert result.after == text.replace("- a\n", f"- a\n- {NEW}\n")


def test_diff_renders_a_unified_diff_of_the_append():
    result = append(BASE, on=date(2026, 7, 1))
    diff = result.diff()
    assert "a/log.md" in diff
    assert "b/log.md" in diff
    assert f"+- {NEW}" in diff
