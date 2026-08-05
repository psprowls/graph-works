from __future__ import annotations

import pytest
from helpers import BUNDLES, EDGE, all_concept_files, fixture_id, read
from okf_io import _yaml
from ruamel.yaml import YAMLError


@pytest.mark.parametrize("path", all_concept_files(), ids=fixture_id)
def test_split_join_is_identity(path):
    text = read(path)
    assert _yaml.join(_yaml.split(text)) == text


def test_no_frontmatter():
    split = _yaml.split(read(EDGE / "no_frontmatter.md"))
    assert not split.has_frontmatter
    assert not split.unterminated
    assert split.fm_text == ""
    assert split.body.startswith("# Subdirectories")


def test_empty_frontmatter():
    split = _yaml.split(read(EDGE / "empty_frontmatter.md"))
    assert split.has_frontmatter
    assert not split.unterminated
    assert split.fm_text == ""


def test_unterminated():
    split = _yaml.split(read(EDGE / "malformed_unterminated.md"))
    assert split.has_frontmatter
    assert split.unterminated
    assert split.close_delim == ""


@pytest.mark.parametrize(
    "sep",
    ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
    ids=["vt", "ff", "fs", "gs", "rs", "nel", "ls", "ps"],
)
def test_non_yaml_line_boundaries_do_not_close_the_frontmatter(sep):
    """`str.splitlines` breaks on these; YAML does not.

    Using it would let a scalar containing one manufacture a phantom `---`
    line, closing the frontmatter early and handing real keys back as body.
    A round-trip assertion cannot catch this, because `join` reconstructs the
    original bytes whichever side of the boundary each piece landed on.
    """
    text = f"---\ndesc: one{sep}---\nreal_key: value\n---\n\n# Body\n"
    split = _yaml.split(text)

    assert _yaml.join(split) == text
    assert "real_key: value" in split.fm_text
    assert "real_key" not in split.body
    assert split.body == "# Body\n"


def test_indented_delimiter_in_block_scalar_is_content():
    """The `.strip()` vs `.rstrip()` regression."""
    split = _yaml.split(read(EDGE / "dashes_in_scalar.md"))
    assert not split.unterminated
    assert "A horizontal rule below:" in split.fm_text
    assert "...and text after it." in split.fm_text
    assert split.body.startswith("# Definition")


def test_body_delimiters_stay_in_the_body():
    split = _yaml.split(read(EDGE / "body_delimiters.md"))
    assert split.fm_text.strip().startswith("type: Policy")
    assert "\n---\n" in split.body
    assert "type: Example" in split.body


def test_bom_is_captured_separately():
    split = _yaml.split(read(EDGE / "encoding/bom.md"))
    assert split.bom == "\ufeff"
    assert split.has_frontmatter
    assert split.open_delim.startswith("---")


def test_crlf_line_endings_survive():
    text = read(EDGE / "encoding/crlf.md")
    split = _yaml.split(text)
    assert split.open_delim.endswith("\r\n")
    assert split.close_delim.endswith("\r\n")
    assert _yaml.join(split) == text


def test_missing_trailing_newline_survives():
    text = read(EDGE / "encoding/no_trailing_newline.md")
    assert not text.endswith("\n")
    assert _yaml.join(_yaml.split(text)) == text


def _fm(path):
    return _yaml.split(read(path)).fm_text


def test_load_empty_frontmatter_is_an_empty_mapping():
    assert len(_yaml.load_fm("")) == 0


def test_load_invalid_yaml_raises():
    with pytest.raises(YAMLError):
        _yaml.load_fm(_fm(EDGE / "malformed_yaml.md"))


def test_load_non_mapping_raises():
    with pytest.raises(_yaml.NotAMappingError):
        _yaml.load_fm(_fm(EDGE / "malformed_not_mapping.md"))


def test_sniff_indented_sequence_dialect():
    style = _yaml.sniff_style(_fm(EDGE / "dialect_indented.md"))
    assert style.mapping_indent == 2
    assert style.sequence_dash_offset == 2
    assert style.sequence_indent == 4


def test_sniff_block_dialect_dashes_at_parent_indent():
    style = _yaml.sniff_style(_fm(EDGE / "dialect_block.md"))
    assert style.sequence_dash_offset == 0
    assert style.sequence_indent == 2


def test_sniff_detects_padded_flow_and_crlf():
    assert _yaml.sniff_style(_fm(EDGE / "dialect_flow.md")).padded_flow
    assert not _yaml.sniff_style(_fm(EDGE / "dialect_block.md")).padded_flow
    assert _yaml.sniff_style(_fm(EDGE / "encoding/crlf.md")).newline == "\r\n"


def test_padded_flow_braces_are_reproduced():
    """ruamel emits `{a: b}`; the acme_retail dialect writes `{ a: b }`."""
    fm = _fm(BUNDLES / "acme_retail/metrics/revenue.md")
    style = _yaml.sniff_style(fm)
    out = _yaml.dump_fm(_yaml.load_fm(fm), style=style)
    assert "generated: { by: reference_agent/gemini-2.5-pro," in out
    assert out == fm, "forced re-dump of a padded-flow document must be exact"


def test_crlf_document_redumps_exactly():
    """The CRLF counterpart of the padded-flow re-dump. The write-back depends on it."""
    fm = _fm(EDGE / "encoding/crlf.md")
    style = _yaml.sniff_style(fm)
    assert style.newline == "\r\n"
    assert _yaml.dump_fm(_yaml.load_fm(fm), style=style) == fm


def test_crlf_rewrite_does_not_double_a_carriage_return():
    """A scalar carrying its own CRLF must not become CR CR LF.

    `load_fm` never produces such a value -- ruamel normalizes line breaks on
    parse -- but a caller assigning one directly can, and a blanket
    `replace("\\n", "\\r\\n")` corrupts it into a spurious blank line.
    """
    from ruamel.yaml.comments import CommentedMap
    from ruamel.yaml.scalarstring import LiteralScalarString

    data = CommentedMap({"description": LiteralScalarString("line one\r\nline two\n")})
    out = _yaml.dump_fm(data, style=_yaml.Style(newline="\r\n"))

    assert "\r\r\n" not in out
    assert _yaml.load_fm(out)["description"] == "line one\nline two\n"


def test_sniff_falls_back_to_defaults_when_nothing_matches():
    """Flat frontmatter has no dashes and no indented lines to sniff."""
    style = _yaml.sniff_style("type: Metric\ntitle: Flat\nstatus: stable\n")
    assert style == _yaml.Style()
