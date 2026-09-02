from __future__ import annotations

import unicodedata
from pathlib import Path

import helpers
import pytest
from helpers import BUNDLES, write_tree
from okf_io import bundle, links

CONCEPT = "---\ntype: Metric\ntitle: T\ndescription: D\n---\n\n"


def make(tmp_path: Path, files: dict[str, str]) -> bundle.Bundle:
    return bundle.load(write_tree(tmp_path, files))


def targets(graph: links.LinkGraph) -> list[str | None]:
    return [link.target for link in graph.links]


def test_fragment_bearing_links_resolve(tmp_path):
    """Regression: okf-schema never strips `#anchor`, so it reports false broken links."""
    loaded = make(
        tmp_path,
        {
            "metrics/a.md": CONCEPT + "See [orders](../tables/orders.md#order_status).\n",
            "tables/orders.md": CONCEPT + "# Orders\n",
        },
    )
    graph = links.build(loaded)
    assert targets(graph) == ["tables/orders.md"]
    assert graph.links[0].fragment == "order_status"
    assert graph.broken == ()


def test_links_inside_code_blocks_are_not_edges(tmp_path):
    """Regression: every regex link scanner treats a fenced destination as an edge."""
    body = "Text.\n\n```sql\n[fenced](/nope.md)\n```\n\n    [indented](/also-nope.md)\n"
    graph = links.build(make(tmp_path, {"a.md": CONCEPT + body}))
    assert graph.links == ()
    assert graph.broken == ()


def test_root_absolute_links_resolve(tmp_path):
    """Regression: the reference viewer discards the `/`-prefixed form in `# Cited by`."""
    loaded = make(
        tmp_path,
        {
            "metrics/a.md": CONCEPT + "# Cited by\n\n- [policy](/policies/p.md)\n",
            "policies/p.md": CONCEPT + "# P\n",
        },
    )
    graph = links.build(loaded)
    assert targets(graph) == ["policies/p.md"]
    assert graph.broken == ()


def test_percent_encoded_destinations_resolve(tmp_path):
    """Regression: markdown-it normalizes on output, so a naive consumer breaks on café.md."""
    loaded = make(tmp_path, {"a.md": CONCEPT + "[c](./café.md)\n", "café.md": CONCEPT + "# C\n"})
    graph = links.build(loaded)
    assert graph.links[0].raw == "./caf%C3%A9.md"
    assert targets(graph) == ["café.md"]
    assert graph.broken == ()


def test_an_escaped_hash_is_a_literal_not_a_fragment_delimiter(tmp_path):
    """Regression: decoding before the fragment split turns `%23` into a delimiter."""
    loaded = make(
        tmp_path,
        {"a.md": CONCEPT + "[r](report%23v2.md)\n", "report#v2.md": CONCEPT + "# R\n"},
    )
    graph = links.build(loaded)
    assert targets(graph) == ["report#v2.md"]
    assert graph.links[0].fragment is None
    assert graph.broken == ()


def test_an_escaped_hash_coexists_with_a_real_fragment(tmp_path):
    loaded = make(
        tmp_path,
        {"a.md": CONCEPT + "[r](report%23v2.md#section)\n", "report#v2.md": CONCEPT + "# R\n"},
    )
    graph = links.build(loaded)
    assert targets(graph) == ["report#v2.md"]
    assert graph.links[0].fragment == "section"
    assert graph.broken == ()


def test_an_escaped_colon_is_a_path_not_a_scheme(tmp_path):
    """RFC 3986 §3.1: a scheme is never percent-encoded, so `a%3Ab.md` is a path.

    `a:b.md` is unwritable on Windows (colon is the drive separator), so the fixture is
    loaded through the unwritable-name helper rather than written directly.
    """
    loaded = helpers.load_tree_admitting_unwritable_names(
        tmp_path,
        {"x.md": CONCEPT + "[a](a%3Ab.md)\n", "a:b.md": CONCEPT + "# A\n"},
    )
    graph = links.build(loaded)
    assert [link.external for link in graph.links] == [False]
    assert targets(graph) == ["a:b.md"]
    assert graph.broken == ()


def test_a_name_the_host_can_hold_is_written_and_one_it_cannot_is_injected(tmp_path):
    """A directory occupying a name blocks the write on every platform, standing in for
    a genuinely unwritable name (D-039/D-041) without depending on host-specific rules."""
    (tmp_path / "occupied.md").mkdir()

    loaded = helpers.load_tree_admitting_unwritable_names(
        tmp_path,
        {"b.md": CONCEPT + "# B\n", "occupied.md": CONCEPT + "# U\n"},
    )

    assert (tmp_path / "b.md").is_file()
    assert loaded.concept("b") is not None
    assert loaded.concept("occupied") is not None
    assert loaded.concept("occupied").fm.title == "T"


def test_non_ascii_unwritable_member_is_refused(tmp_path):
    """D-045: a non-ASCII member that could not be written is refused, not injected --
    injecting it would desynchronise `Bundle._canonical`, which this helper does not
    maintain; the NFC/NFD collision case has its own dedicated fixture."""
    name = "café.md"
    (tmp_path / name).mkdir()

    with pytest.raises(ValueError, match="not ASCII"):
        helpers.load_tree_admitting_unwritable_names(tmp_path, {name: CONCEPT + "# C\n"})


def test_silent_mangle_is_refused(tmp_path, monkeypatch):
    """D-047: a write that reports success but lands under a name the walk disagrees
    with must be caught loudly, not laundered into a bundle holding both a stray real
    file and an injected virtual member under the requested id."""
    name = "mangled.md"
    real_member_id = bundle.Bundle.member_id

    def disagreeing_member_id(self: bundle.Bundle, path: str) -> str | None:
        if path == name:
            return None
        return real_member_id(self, path)

    monkeypatch.setattr(bundle.Bundle, "member_id", disagreeing_member_id)

    with pytest.raises(ValueError, match="mangle"):
        helpers.load_tree_admitting_unwritable_names(tmp_path, {name: CONCEPT + "# M\n"})


def test_dot_segments_normalize(tmp_path):
    loaded = make(
        tmp_path,
        {"x/y/a.md": CONCEPT + "[b](./../y/./b.md)\n", "x/y/b.md": CONCEPT + "# B\n"},
    )
    assert targets(links.build(loaded)) == ["x/y/b.md"]


def test_a_path_escaping_the_root_is_broken(tmp_path):
    graph = links.build(make(tmp_path, {"a.md": CONCEPT + "[out](../../outside.md)\n"}))
    assert graph.links[0].target is None
    assert graph.broken == graph.links


def test_external_destinations_are_never_resolved_or_broken(tmp_path):
    body = "[a](https://example.com/x) [b](mailto:x@y.z) [c](//cdn.example.com/x.md)\n"
    graph = links.build(make(tmp_path, {"a.md": CONCEPT + body}))
    assert [link.external for link in graph.links] == [True, True, True]
    assert targets(graph) == [None, None, None]
    assert graph.broken == ()


def test_a_fragment_only_destination_is_not_an_edge(tmp_path):
    graph = links.build(make(tmp_path, {"a.md": CONCEPT + "[s](#section)\n"}))
    assert graph.links == ()


def test_images_are_checked_but_never_become_backlinks(tmp_path):
    loaded = make(
        tmp_path,
        {
            "a.md": CONCEPT + "![ok](/assets/d.svg)\n\n![gone](/assets/none.png)\n",
            "b.md": CONCEPT + "[a](/a.md)\n",
            "assets/d.svg": "<svg/>",
        },
    )
    graph = links.build(loaded)
    assert [link.target for link in graph.broken] == ["assets/none.png"]
    assert graph.backlinks == {"a": ("b",)}


def test_backlinks_are_derived_and_sorted(tmp_path):
    loaded = make(
        tmp_path,
        {
            "hub.md": CONCEPT + "# Hub\n",
            "z.md": CONCEPT + "[h](/hub.md)\n",
            "a.md": CONCEPT + "[h](./hub.md)\n",
        },
    )
    graph = links.build(loaded)
    assert graph.backlinks["hub"] == ("a", "z")


def test_a_self_link_does_produce_a_backlink(tmp_path):
    """Records current behaviour rather than deciding it.

    Whether a concept citing itself belongs in its own "cited by" list is a
    design question outside this task. If that ever changes, this test should
    fail and be updated deliberately, not drift silently.
    """
    graph = links.build(make(tmp_path, {"a.md": CONCEPT + "[self](/a.md)\n"}))
    assert graph.backlinks == {"a": ("a",)}


def test_an_nfd_named_member_resolves_a_percent_encoded_nfc_destination(tmp_path):
    """markdown-it normalizes destinations to NFC on the way out; a filesystem
    is free to store the same name as NFD. `has_member` and backlinks must
    agree either way."""
    nfd = unicodedata.normalize("NFD", "café")
    loaded = make(
        tmp_path,
        {
            f"{nfd}.md": CONCEPT + "# Café\n",
            "a.md": CONCEPT + "[c](./caf%C3%A9.md)\n",
        },
    )
    graph = links.build(loaded)
    assert graph.broken == ()
    assert graph.backlinks[nfd] == ("a",)


def test_lines_are_file_accurate(tmp_path):
    """Token maps are body-relative; the offset is what makes a finding point at a line."""
    loaded = make(tmp_path, {"a.md": CONCEPT + "# D\n\nText.\n\n[x](/nope.md)\n"})
    graph = links.build(loaded)
    raw = (tmp_path / "a.md").read_text(encoding="utf-8").splitlines()
    assert graph.links[0].line is not None
    assert "[x](/nope.md)" in raw[graph.links[0].line - 1]


def test_bodies_holds_one_index_per_concept(tmp_path):
    loaded = make(tmp_path, {"a.md": CONCEPT + "# A\n", "b.md": CONCEPT + "# B\n"})
    assert set(links.build(loaded).bodies) == {"a", "b"}


def test_out_is_keyed_by_source_and_order_is_deterministic(tmp_path):
    loaded = make(tmp_path, {"a.md": CONCEPT + "[z](/z.md) [y](/y.md)\n"})
    first = links.build(loaded)
    second = links.build(loaded)
    assert first.links == second.links
    assert [link.raw for link in first.out["a"]] == ["/y.md", "/z.md"]


def test_absolute_form_suggests_the_repair(tmp_path):
    loaded = make(
        tmp_path,
        {"deep/a.md": CONCEPT + "[p](policies/p.md)\n", "policies/p.md": CONCEPT + "# P\n"},
    )
    graph = links.build(loaded)
    assert graph.broken == graph.links
    assert links.absolute_form(graph.links[0]) == "policies/p.md"


def test_absolute_form_keeps_an_escaped_hash_in_the_suggestion(tmp_path):
    loaded = make(
        tmp_path,
        {"deep/a.md": CONCEPT + "[r](report%23v2.md)\n", "report#v2.md": CONCEPT + "# R\n"},
    )
    graph = links.build(loaded)
    assert graph.broken == graph.links
    assert links.absolute_form(graph.links[0]) == "report#v2.md"


def test_resolve_reference_relative_resolves_against_source_directory():
    assert links.resolve_reference("p.md", source_id="tables/orders") == "tables/p.md"


def test_resolve_reference_root_absolute_resolves_against_bundle_root():
    assert links.resolve_reference("/policies/p.md", source_id="tables/orders") == "policies/p.md"


def test_resolve_reference_fragment_is_stripped_before_resolving():
    assert links.resolve_reference("p.md#section", source_id="tables/orders") == "tables/p.md"


def test_resolve_reference_escaping_the_root_is_none():
    assert links.resolve_reference("../../outside.md", source_id="a") is None


def test_resolve_reference_external_value_is_none():
    """`is_external` is how a caller tells this `None` apart from the escape case above."""
    value = "https://example.com/x"
    assert links.resolve_reference(value, source_id="a") is None
    assert links.is_external(value) is True
    assert links.is_external("../../outside.md") is False


def test_resolve_reference_empty_or_whitespace_is_none():
    assert links.resolve_reference("", source_id="a") is None
    assert links.resolve_reference("   ", source_id="a") is None


def test_resolve_reference_does_not_percent_decode():
    """Unlike a markdown destination, a §6.2 value arrives undecoded and stays that way."""
    assert links.resolve_reference("caf%C3%A9.md", source_id="a") == "caf%C3%A9.md"


def test_resolve_reference_a_colon_led_relative_path_reads_as_external():
    """A plausible §6.2 value like `report:v2.sql` reads as a URI scheme (RFC 3986 §4.2)."""
    assert links.is_external("report:v2.sql") is True
    assert links.resolve_reference("report:v2.sql", source_id="a") is None
    assert links.resolve_reference("./report:v2.sql", source_id="a") == "report:v2.sql"


def test_the_vendored_bundles_have_no_escaping_links():
    """A sanity check on the real corpus: every internal link resolves or is merely missing."""
    for name in ("acme_retail", "ga4"):
        graph = links.build(bundle.load(BUNDLES / name))
        assert all(link.external or link.target is not None for link in graph.links)
