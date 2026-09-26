"""extract — six ported from wiki-io's test_ingest_source.py, six new."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from doc_wiki_okf.reading import extract

UNICODE_ENCODINGS = [
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
]


@pytest.mark.parametrize(("bom", "encoding"), UNICODE_ENCODINGS)
def test_bom_text_decodes_exactly_and_finds_first_heading(tmp_path, bom, encoding):
    source = tmp_path / "notes.txt"
    content = "# Café 🌍\r\n\r\nBody text.\r\n"
    source.write_bytes(bom + content.encode(encoding))
    assert extract(source) == (content, "Café 🌍", False)


@pytest.mark.parametrize(("bom", "encoding"), UNICODE_ENCODINGS)
@pytest.mark.parametrize(
    ("suffix", "content", "expected", "title"),
    [
        ("html", "<title>Café</title><style>hidden</style><p>🌍 body</p><script>secret</script>", "🌍 body", "Café"),
        ("json", '{"a":1}', '{\n  "a": 1\n}', None),
        ("csv", "row\r\n" * 51, "\n".join(["row"] * 50), None),
        ("other", "Café 🌍\r\n", "Café 🌍\r\n", None),
        ("md", "\n" * 20 + "# Late", "\n" * 20 + "# Late", None),
    ],
)
def test_bom_formats_keep_their_existing_behavior(tmp_path, bom, encoding, suffix, content, expected, title):
    source = tmp_path / f"source.{suffix}"
    source.write_bytes(bom + content.encode(encoding))
    assert extract(source) == (expected, title, False)


@pytest.mark.parametrize(("bom", "encoding"), UNICODE_ENCODINGS)
def test_bom_json_retains_formatting_limit(tmp_path, bom, encoding):
    source = tmp_path / "large.json"
    source.write_bytes(bom + json.dumps({"a": "x" * 100_000}).encode(encoding))
    text, title, binary = extract(source)
    assert text == '{\n  "a": "' + "x" * 99_990
    assert title is None
    assert binary is False


@pytest.mark.parametrize("data", [b"", *(bom for bom, _ in UNICODE_ENCODINGS)])
def test_empty_and_bom_only_are_valid_empty_text(tmp_path, data):
    source = tmp_path / "empty.txt"
    source.write_bytes(data)
    assert extract(source) == ("", None, False)


@pytest.mark.parametrize(("bom", "encoding"), UNICODE_ENCODINGS)
def test_only_one_leading_bom_is_consumed(tmp_path, bom, encoding):
    source = tmp_path / "double.txt"
    content = "\ufeff# Content"
    source.write_bytes(bom + content.encode(encoding))
    assert extract(source) == (content, None, False)


@pytest.mark.parametrize(
    "data",
    [
        b"caf\xe9",
        b"\x80",
        b"\xef\xbb\xbf\xff",
        b"\xff\xfeA",
        b"\xfe\xff\x00",
        b"\xff\xfe\x00\xd8",
        b"\xfe\xff\xd8\x00",
        b"\xff\xfe\x00\x00A\x00",
        b"\x00\x00\xfe\xff\x00\x00",
        b"\xff\xfe\x00\x00\x00\x00\x11\x00",
        b"\x00\x00\xfe\xff\x00\x11\x00\x00",
    ],
)
def test_unsupported_or_malformed_unicode_never_falls_back(tmp_path, data):
    source = tmp_path / "unreadable.txt"
    source.write_bytes(data)
    assert extract(source) == ("", None, True)


def test_extract_md_returns_text_and_heading_title(tmp_path: Path) -> None:
    md = tmp_path / "article.md"
    md.write_text("# My Article\n\nSome body text.", encoding="utf-8")
    text, title, _binary = extract(md)
    assert "My Article" in text
    assert title == "My Article"


def test_extract_md_no_heading_returns_none_title(tmp_path: Path) -> None:
    md = tmp_path / "no-heading.md"
    md.write_text("Just some text without a heading.", encoding="utf-8")
    text, title, _binary = extract(md)
    assert "Just some text" in text
    assert title is None


def test_extract_txt(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("Plain text content.", encoding="utf-8")
    text, title, _binary = extract(txt)
    assert "Plain text content" in text
    assert title is None


def test_extract_html(tmp_path: Path) -> None:
    html = tmp_path / "page.html"
    html.write_text(
        "<html><head><title>Page Title</title></head><body><p>Hello world</p></body></html>",
        encoding="utf-8",
    )
    text, title, _binary = extract(html)
    assert "Hello world" in text
    assert title == "Page Title"


def test_extract_json(tmp_path: Path) -> None:
    j = tmp_path / "data.json"
    j.write_text(json.dumps({"key": "value"}), encoding="utf-8")
    text, title, _binary = extract(j)
    assert "key" in text
    assert title is None


def test_extract_csv(tmp_path: Path) -> None:
    csv = tmp_path / "data.csv"
    csv.write_text("col1,col2\n1,2\n3,4\n", encoding="utf-8")
    text, title, _binary = extract(csv)
    assert "col1" in text
    assert title is None


# --- new coverage, not ported --------------------------------------------


def test_extract_md_heading_below_the_twenty_line_window_is_ignored(tmp_path: Path) -> None:
    """Only the first 20 lines are scanned for a `# ` heading."""
    md = tmp_path / "late.md"
    md.write_text("\n" * 25 + "# Too Late\n", encoding="utf-8")
    text, title, _binary = extract(md)
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
    text, title, _binary = extract(html)
    assert title == "T"
    assert "Visible" in text
    assert "secret" not in text
    assert "color:red" not in text


def test_extract_html_without_a_title_returns_none(tmp_path: Path) -> None:
    html = tmp_path / "untitled.html"
    html.write_text("<html><body><p>Body only</p></body></html>", encoding="utf-8")
    text, title, _binary = extract(html)
    assert "Body only" in text
    assert title is None


def test_extract_invalid_json_falls_back_to_raw_text(tmp_path: Path) -> None:
    """The `except` arm of the JSON branch."""
    j = tmp_path / "broken.json"
    j.write_text("{not json at all", encoding="utf-8")
    text, title, _binary = extract(j)
    assert text == "{not json at all"
    assert title is None


def test_extract_unknown_extension_returns_decoded_bytes(tmp_path: Path) -> None:
    """The tail return. No ported test passes an unmapped extension."""
    other = tmp_path / "script.py"
    other.write_text("print('hi')\n", encoding="utf-8")
    text, title, _binary = extract(other)
    assert text == "print('hi')\n"
    assert title is None


def test_extract_html_ignores_whitespace_only_text_nodes(tmp_path: Path) -> None:
    """HTML with whitespace-only text nodes between tags (e.g., indentation) are ignored."""
    html = tmp_path / "indented.html"
    html.write_text(
        "<html>\n  <body>\n    <p>Hello</p>\n    <p>World</p>\n  </body>\n</html>",
        encoding="utf-8",
    )
    text, title, _binary = extract(html)
    # Visible text should be present
    assert "Hello" in text
    assert "World" in text
    # No stray blank lines (parts should only contain the trimmed text nodes)
    lines = text.split("\n")
    assert all(line.strip() for line in lines), "No empty lines should be present"
    assert title is None


def test_extract_non_utf8_returns_no_text_and_flags_binary(tmp_path: Path) -> None:
    """B-G: honest non-extraction. A string of replacement characters is not
    the material, and handing one to a model composes a page about noise."""
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n\xff\xfe\x00binary\n")
    text, title, binary = extract(pdf)
    assert text == ""
    assert title is None
    assert binary is True
    assert "�" not in text


def test_extract_flags_binary_whatever_the_suffix_claims(tmp_path: Path) -> None:
    """P-3: the strict decode is uniform. A PDF named `.md` is still a PDF."""
    mislabelled = tmp_path / "scan.md"
    mislabelled.write_bytes(b"# A Thing\n\n\xff\xfe not utf-8\n")
    text, title, binary = extract(mislabelled)
    assert (text, title, binary) == ("", None, True)


def test_extract_flags_a_decodable_file_as_not_binary(tmp_path: Path) -> None:
    md = tmp_path / "article.md"
    md.write_text("# My Article\n\nBody.\n", encoding="utf-8")
    _text, _title, binary = extract(md)
    assert binary is False
