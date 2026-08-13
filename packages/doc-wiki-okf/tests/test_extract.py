"""extract — six ported from wiki-io's test_ingest_source.py, six new."""

from __future__ import annotations

import json
from pathlib import Path

from doc_wiki_okf.reading import extract


def test_extract_md_returns_text_and_heading_title(tmp_path: Path) -> None:
    md = tmp_path / "article.md"
    md.write_text("# My Article\n\nSome body text.", encoding="utf-8")
    text, title = extract(md)
    assert "My Article" in text
    assert title == "My Article"


def test_extract_md_no_heading_returns_none_title(tmp_path: Path) -> None:
    md = tmp_path / "no-heading.md"
    md.write_text("Just some text without a heading.", encoding="utf-8")
    text, title = extract(md)
    assert "Just some text" in text
    assert title is None


def test_extract_txt(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("Plain text content.", encoding="utf-8")
    text, title = extract(txt)
    assert "Plain text content" in text
    assert title is None


def test_extract_html(tmp_path: Path) -> None:
    html = tmp_path / "page.html"
    html.write_text(
        "<html><head><title>Page Title</title></head><body><p>Hello world</p></body></html>",
        encoding="utf-8",
    )
    text, title = extract(html)
    assert "Hello world" in text
    assert title == "Page Title"


def test_extract_json(tmp_path: Path) -> None:
    j = tmp_path / "data.json"
    j.write_text(json.dumps({"key": "value"}), encoding="utf-8")
    text, title = extract(j)
    assert "key" in text
    assert title is None


def test_extract_csv(tmp_path: Path) -> None:
    csv = tmp_path / "data.csv"
    csv.write_text("col1,col2\n1,2\n3,4\n", encoding="utf-8")
    text, title = extract(csv)
    assert "col1" in text
    assert title is None


# --- new coverage, not ported --------------------------------------------


def test_extract_md_heading_below_the_twenty_line_window_is_ignored(tmp_path: Path) -> None:
    """Only the first 20 lines are scanned for a `# ` heading."""
    md = tmp_path / "late.md"
    md.write_text("\n" * 25 + "# Too Late\n", encoding="utf-8")
    text, title = extract(md)
    assert "Too Late" in text
    assert title is None


def test_extract_html_skips_script_and_style_bodies(tmp_path: Path) -> None:
    """The `_skip` branch of the handlers. No ported test reaches it."""
    html = tmp_path / "noisy.html"
    html.write_text(
        "<html><head><title>T</title><style>body{color:red}</style></head>"
        "<body><script>var secret = 1;</script><p>Visible</p></body></html>",
        encoding="utf-8",
    )
    text, title = extract(html)
    assert title == "T"
    assert "Visible" in text
    assert "secret" not in text
    assert "color:red" not in text


def test_extract_html_without_a_title_returns_none(tmp_path: Path) -> None:
    html = tmp_path / "untitled.html"
    html.write_text("<html><body><p>Body only</p></body></html>", encoding="utf-8")
    text, title = extract(html)
    assert "Body only" in text
    assert title is None


def test_extract_invalid_json_falls_back_to_raw_text(tmp_path: Path) -> None:
    """The `except` arm of the JSON branch."""
    j = tmp_path / "broken.json"
    j.write_text("{not json at all", encoding="utf-8")
    text, title = extract(j)
    assert text == "{not json at all"
    assert title is None


def test_extract_unknown_extension_returns_decoded_bytes(tmp_path: Path) -> None:
    """The tail return. No ported test passes an unmapped extension."""
    other = tmp_path / "script.py"
    other.write_text("print('hi')\n", encoding="utf-8")
    text, title = extract(other)
    assert text == "print('hi')\n"
    assert title is None


def test_extract_html_ignores_whitespace_only_text_nodes(tmp_path: Path) -> None:
    """HTML with whitespace-only text nodes between tags (e.g., indentation) are ignored."""
    html = tmp_path / "indented.html"
    html.write_text(
        "<html>\n  <body>\n    <p>Hello</p>\n    <p>World</p>\n  </body>\n</html>",
        encoding="utf-8",
    )
    text, title = extract(html)
    # Visible text should be present
    assert "Hello" in text
    assert "World" in text
    # No stray blank lines (parts should only contain the trimmed text nodes)
    lines = text.split("\n")
    assert all(line.strip() for line in lines), "No empty lines should be present"
    assert title is None
