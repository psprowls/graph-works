"""The pure half of the ingest command: parse, type, gate, compose."""

from __future__ import annotations

from pathlib import Path

from code_wiki_okf.config import Config, StateGateConfig
from doc_wiki_okf.sources import seed_source_kinds
from graph_works_core.ingest.commands import (
    _log_line,
    compose_body,
    compose_frontmatter,
    parse_ingestor_response,
    state_gate_adapter,
    validated_source_kind,
)

_PAGE = "---\ntitle: A Thing\ndescription: One line.\nsource_kind: spec\n---\n\n## TL;DR\n\nIt is a thing.\n"


def test_a_well_formed_response_splits_into_frontmatter_and_body():
    frontmatter, body, parsed = parse_ingestor_response(_PAGE)
    assert parsed is True
    assert frontmatter["source_kind"] == "spec"
    assert body.startswith("## TL;DR")


def test_a_fenced_response_is_unwrapped_before_parsing():
    frontmatter, _body, parsed = parse_ingestor_response("```yaml\n" + _PAGE + "```\n")
    assert parsed is True
    assert frontmatter["title"] == "A Thing"


def test_a_response_with_no_frontmatter_keeps_its_whole_text_as_body():
    frontmatter, body, parsed = parse_ingestor_response("# Just a heading\n\nAnd prose.\n")
    assert (frontmatter, parsed) == ({}, False)
    assert body == "# Just a heading\n\nAnd prose.\n"


def test_a_fence_with_no_newline_is_not_frontmatter():
    assert parse_ingestor_response("```")[2] is False


# The two degenerate inputs where `ingest`'s former private fence stripper
# disagreed with the shared one: it returned the text untouched where the
# shared one strips, and `""` for a bare opening fence. Both wash out here --
# a response that yields no frontmatter comes back as its *original* text
# either way. Pinned, so the consolidation is checked rather than argued.


def test_a_bare_opening_fence_keeps_its_whole_text_as_body():
    frontmatter, body, parsed = parse_ingestor_response("```yaml")
    assert (frontmatter, parsed) == ({}, False)
    assert body == "```yaml"


def test_a_fenceless_response_keeps_its_surrounding_whitespace_in_the_body():
    text = "\n\n# Just a heading\n\nAnd prose.\n\n"
    frontmatter, body, parsed = parse_ingestor_response(text)
    assert (frontmatter, parsed) == ({}, False)
    assert body == text


def test_a_fence_opened_but_never_closed_still_parses():
    """`_strip_fence` finds no closing ``` -- the whole remainder is the text.

    The frontmatter splitter still works on it: only the leading fence line is
    gone, so `okf_io.parse` sees a normal `---`-delimited block.
    """
    text = "```json\n---\ntitle: X\ndescription: d\nsource_kind: doc\n---\n\nbody\n"
    frontmatter, body, parsed = parse_ingestor_response(text)
    assert parsed is True
    assert frontmatter["title"] == "X"
    assert frontmatter["source_kind"] == "doc"
    assert body.strip() == "body"


def test_broken_frontmatter_is_a_parse_miss_not_a_raise():
    frontmatter, _body, parsed = parse_ingestor_response("---\n: : :\n---\n\nbody\n")
    assert parsed is False
    assert frontmatter == {}


_KINDS = seed_source_kinds()


def test_the_llm_value_wins_when_it_is_in_the_enum():
    assert validated_source_kind({"source_kind": " SPEC "}, hint="doc", kinds=_KINDS) == "spec"


def test_a_value_outside_the_enum_falls_back_to_the_hint():
    assert validated_source_kind({"source_kind": "blogpost"}, hint="article", kinds=_KINDS) == "article"
    assert validated_source_kind({}, hint="doc", kinds=_KINDS) == "doc"
    assert validated_source_kind({"source_kind": None}, hint="doc", kinds=_KINDS) == "doc"


def test_the_enum_is_the_callers_not_a_module_constant():
    """K-D: pass a vault's own vocabulary and it is what decides."""
    assert validated_source_kind({"source_kind": "memo"}, hint="doc", kinds=("memo",)) == "memo"
    assert validated_source_kind({"source_kind": "spec"}, hint="memo", kinds=("memo",)) == "memo"


def _config(tmp_path: Path, *, enabled: bool = True) -> Config:
    return Config(
        graph_dir=tmp_path / "_cache/graph",
        declarations_dir=tmp_path / "okf",
        repos=(),
        state_gate=StateGateConfig(enabled=enabled, branches=("main",)),
    )


def test_the_adapter_returns_the_three_gate_fields(tmp_path):
    gate = state_gate_adapter(_config(tmp_path, enabled=False))(tmp_path, workspace=tmp_path)
    assert set(gate) == {"allowed", "reason", "head_commit"}
    assert gate["allowed"] is True


def test_compose_frontmatter_writes_only_the_entity_uri_it_owns():
    """K-F: the drift stamp was this function's only reader of the state gate
    and of the kind, and both parameters went with it. The suggest-phase
    status went too: it is not page frontmatter."""
    frontmatter = compose_frontmatter({"description": "One line."}, entity_uri="pkg:okf-io")
    assert frontmatter["entity_uri"] == "pkg:okf-io"
    assert "proposal_status" not in frontmatter
    assert "last_sync_commit" not in frontmatter


def test_the_optional_frontmatter_rides_through_and_blanks_do_not():
    frontmatter = compose_frontmatter(
        {"authors": ["A"], "source_date": "2026-08-01", "tags": [], "tokens": 12, "title": "ignored"},
        entity_uri=None,
    )
    assert frontmatter["authors"] == ["A"]
    assert frontmatter["source_date"] == "2026-08-01"
    assert "tokens" not in frontmatter
    assert "tags" not in frontmatter
    assert "title" not in frontmatter


def test_the_entity_link_lands_under_an_existing_touches_section():
    body = compose_body("## TL;DR\n\nx\n\n## Touches\n\n- something\n", entity_page="packages/okf-io")
    assert "[/packages/okf-io.md](/packages/okf-io.md)" in body
    assert body.count("## Touches") == 1


def test_the_entity_link_creates_the_section_when_it_is_absent():
    body = compose_body("## TL;DR\n\nx\n", entity_page="packages/okf-io")
    assert body.rstrip().endswith("[/packages/okf-io.md](/packages/okf-io.md)")
    assert "## Touches" in body


def test_composing_the_link_twice_is_idempotent():
    once = compose_body("## TL;DR\n\nx\n", entity_page="packages/okf-io")
    assert compose_body(once, entity_page="packages/okf-io") == once


def test_no_entity_page_leaves_the_body_alone():
    assert compose_body("## TL;DR\n\nx\n", entity_page=None) == "## TL;DR\n\nx\n"


def test_a_touches_heading_with_no_trailing_newline_is_not_duplicated():
    """The model's last line is the heading and nothing follows it.

    The old marker was the literal `"## Touches\\n"`, so a heading ending the
    string never matched and a *second* section was appended under the first.
    """
    body = compose_body("## TL;DR\n\nx\n\n## Touches", entity_page="packages/okf-io")
    assert body.count("## Touches") == 1
    assert "[/packages/okf-io.md](/packages/okf-io.md)" in body


def test_a_touches_heading_with_trailing_spaces_is_still_the_section():
    body = compose_body("## TL;DR\n\nx\n\n## Touches  \n\n- a\n", entity_page="packages/okf-io")
    assert body.count("## Touches") == 1


def test_a_fenced_touches_heading_is_never_the_insertion_point():
    """A fenced example of the section is not the section.

    `str.find` took the first occurrence anywhere, so a body documenting its
    own format got the link written into the example.
    """
    body = "## TL;DR\n\n```md\n## Touches\n\n- example\n```\n\n## Touches\n\n- real\n"
    composed = compose_body(body, entity_page="packages/okf-io")
    fenced = composed.split("```")[1]
    assert "[/packages/okf-io.md](/packages/okf-io.md)" not in fenced
    assert composed.count("## Touches") == 2


def test_a_body_whose_only_touches_heading_is_fenced_gets_a_real_section():
    """No real section exists, so one is appended rather than the fence used."""
    composed = compose_body("## TL;DR\n\n```md\n## Touches\n```\n", entity_page="packages/okf-io")
    assert composed.count("## Touches") == 2
    assert composed.rstrip().endswith("[/packages/okf-io.md](/packages/okf-io.md)")


def test_an_unclosed_fence_does_not_swallow_the_rest_of_the_body():
    """An opening fence with no close leaves everything after it fenced.

    The conservative reading: there is no usable section, so one is appended.
    """
    composed = compose_body("## TL;DR\n\n```md\n## Touches\n", entity_page="packages/okf-io")
    assert composed.rstrip().endswith("[/packages/okf-io.md](/packages/okf-io.md)")
    assert composed.count("## Touches") == 2


def test_the_log_line_names_every_outcome_kind_separately():
    """Each `_OUTCOME_KEYS` label is pinned here, not by five ingest runs.

    "N suggestion(s) dropped" told a reader which of the five happened only by
    accident. A typo in one label -- or a swap between two of them -- is
    invisible to a coverage gate, because the loop's single branch is covered
    by any one non-empty key.
    """
    line = _log_line(
        "sources/2026-08-a-thing.md",
        "A Thing",
        {
            "proposals": 2,
            "unclassified": ["a: undeclared-type"],
            "refused": ["b: already-decided"],
            "duplicates": ["c: explanations/c.md"],
            "failed": ["d: mkdir-error"],
            "errored": ["e: RuntimeError"],
            "error": "extractor failed",
        },
    )
    assert line == (
        "**Ingest** [A Thing](/sources/2026-08-a-thing.md) — 2 proposal(s) filed"
        "; 1 unclassified; 1 refused; 1 duplicate(s); 1 failed to write; 1 errored"
        "; suggest phase degraded: extractor failed"
    )


def test_the_log_line_omits_every_empty_outcome_kind():
    line = _log_line("sources/2026-08-a-thing.md", "A Thing", {"proposals": 0})
    assert line == "**Ingest** [A Thing](/sources/2026-08-a-thing.md) — 0 proposal(s) filed"
