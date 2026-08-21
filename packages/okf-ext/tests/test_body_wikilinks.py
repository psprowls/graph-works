"""`okf_ext.body.wikilinks` and `resolve_wikilink`: the shared locator two
capabilities (`render`'s lint rule, `work_tracker_okf`'s archive count) both
consume."""

from __future__ import annotations

from okf_ext.body import Wikilink, resolve_wikilink, wikilinks
from okf_io import load_bundle


def test_a_plain_wikilink_is_found():
    found = wikilinks("See [[work/foo]] here.\n")
    assert found == (Wikilink(raw="[[work/foo]]", target="work/foo", line=1, column=4, embed=False),)


def test_an_anchored_wikilink_strips_the_anchor_from_target():
    found = wikilinks("See [[work/foo#section]] here.\n")
    assert len(found) == 1
    assert found[0].target == "work/foo"


def test_an_aliased_wikilink_strips_the_alias_from_target():
    found = wikilinks("See [[work/foo|Foo]] here.\n")
    assert len(found) == 1
    assert found[0].target == "work/foo"
    assert found[0].raw == "[[work/foo|Foo]]"


def test_an_embed_is_flagged_and_keeps_its_bang_in_raw():
    found = wikilinks("An embed ![[assets/x.png]] here.\n")
    assert len(found) == 1
    assert found[0].embed is True
    assert found[0].raw == "![[assets/x.png]]"
    assert found[0].target == "assets/x.png"


def test_an_escaped_pipe_alias_separator_is_honoured():
    found = wikilinks("| [[work/foo\\|Foo]] |\n")
    assert len(found) == 1
    assert found[0].target == "work/foo"


def test_an_unbalanced_wikilink_has_no_target():
    found = wikilinks("See [[dangling for more.\n")
    assert len(found) == 1
    assert found[0].target is None


def test_an_empty_target_wikilink_has_no_target():
    found = wikilinks("See [[ ]] here.\n")
    assert len(found) == 1
    assert found[0].target is None


def test_a_zero_length_wikilink_is_unbalanced_not_empty():
    found = wikilinks("See [[]] here.\n")
    assert len(found) == 1
    assert found[0].target is None


def test_a_wikilink_broken_across_a_line_break_is_two_findings_neither_valid():
    found = wikilinks("See [[broken-here\nand-then]] more text.\n")
    assert len(found) == 1  # only the opener on line 1 is a candidate at all
    assert found[0].line == 1
    assert found[0].target is None


def test_a_fenced_code_block_is_excluded():
    body = "text\n```\n[[work/foo]]\n```\n[[work/bar]]\n"
    found = wikilinks(body)
    assert [w.target for w in found] == ["work/bar"]
    assert found[0].line == 5


def test_an_indented_code_block_is_excluded():
    body = "text\n\n    [[work/foo]]\n\n[[work/bar]]\n"
    found = wikilinks(body)
    assert [w.target for w in found] == ["work/bar"]


def test_a_raw_html_block_is_excluded():
    body = "text\n\n<pre>\n[[work/foo]]\n</pre>\n\n[[work/bar]]\n"
    found = wikilinks(body)
    assert [w.target for w in found] == ["work/bar"]


def test_an_inline_code_span_is_excluded():
    found = wikilinks("Use `[[work/foo]]` inline, and a real [[work/bar]] link.\n")
    assert [w.target for w in found] == ["work/bar"]


def test_a_double_backtick_span_correctly_pairs_and_excludes():
    found = wikilinks("Backticked ``[[a `` and a real [[work/bar]] link.\n")
    assert [w.target for w in found] == ["work/bar"]


def test_multiple_wikilinks_on_one_line_are_each_found_with_their_own_column():
    found = wikilinks("[[a]] and [[b]]\n")
    assert [(w.target, w.column) for w in found] == [("a", 0), ("b", 10)]


def test_line_numbers_are_one_based_and_body_relative():
    found = wikilinks("first\nsecond [[work/foo]]\nthird\n")
    assert len(found) == 1
    assert found[0].line == 2


def test_no_wikilinks_returns_empty_tuple():
    assert wikilinks("Nothing here.\n") == ()


def test_the_last_line_needs_no_trailing_newline():
    found = wikilinks("first\n[[work/foo]]")
    assert len(found) == 1
    assert found[0].target == "work/foo"
    assert found[0].line == 2


def test_a_backtick_run_skips_a_mismatched_width_before_finding_its_pair():
    """The opening double-backtick's search for its own width must step past
    the two single-backtick runs in between rather than stopping at the first
    later run it sees, which has the wrong width to close it."""
    found = wikilinks("`` `[[work/foo]]` `` and a real [[work/bar]] link.\n")
    assert [w.target for w in found] == ["work/bar"]


def test_resolve_wikilink_tries_dot_md_first(tmp_path):
    (tmp_path / "foo.md").write_text("---\ntype: Note\ntitle: Foo\n---\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)
    assert resolve_wikilink("foo", bundle=bundle) == "foo.md"


def test_resolve_wikilink_tries_the_literal_path_too(tmp_path):
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    bundle = load_bundle(tmp_path)
    assert resolve_wikilink("image.png", bundle=bundle) == "image.png"


def test_resolve_wikilink_returns_none_for_a_dangling_target(tmp_path):
    bundle = load_bundle(tmp_path)
    assert resolve_wikilink("nowhere", bundle=bundle) is None


def test_resolve_wikilink_does_no_basename_matching(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "foo.md").write_text("---\ntype: Note\ntitle: Foo\n---\n", encoding="utf-8")
    bundle = load_bundle(tmp_path)
    assert resolve_wikilink("foo", bundle=bundle) is None
