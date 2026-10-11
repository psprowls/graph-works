"""Document table reads preserve string results without relying on string LRUs."""

import pytest
from ext_helpers import FIXTURES
from markdown_it import MarkdownIt
from okf_ext import body, tables
from okf_ext.tables import Column, TableSpec, read_section
from okf_io import Document

SPEC = TableSpec(columns=(Column("action"), Column("done when"), Column("rationale")))
BODIES = [path.read_text(encoding="utf-8") for path in sorted((FIXTURES / "tabled").glob("*.md"))] + [
    "## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n| x \\| y | yes | why |\n",
    "> ## Plan\n> | Action | Done when | Rationale |\n\n## Plan\n\n```md\n| Action | Done when | Rationale |\n```\n",
    "Plan\n----\n\n| Action | Done when |\n| --- | --- |\n| x | |\n\n## Later\n| Other |\n",
    "## Plan\r\n\r\n| Action | Done when |\r\n| --- | --- |\r\n",
    "## Other\n\nNo plan.\n",
]


@pytest.mark.parametrize("heading", ["Plan", "Entries", "File map", "Example"])
@pytest.mark.parametrize("text", BODIES, ids=range(len(BODIES)))
def test_document_table_section_matches_string_reader_with_cold_caches(text, heading, monkeypatch):
    document = Document.parse(text)
    expected = read_section(document.body, heading, SPEC)
    document.markdown()
    body._skeleton.cache_clear()
    # read_section's generic scanner also has a string LRU; neither may mask a reparse.
    from okf_ext.tables.read import _scan

    _scan.cache_clear()

    def unexpected(*args, **kwargs):
        pytest.fail("Document table reads must share its existing CommonMark tokens")

    monkeypatch.setattr(MarkdownIt, "parse", unexpected)
    assert tables.document_read_section(document, heading, SPEC) == expected
