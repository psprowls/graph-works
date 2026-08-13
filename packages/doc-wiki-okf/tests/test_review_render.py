"""The review artifact: seven sections, pinned whole, byte-stable."""

from doc_wiki_okf.proposals.lanes import Lane
from doc_wiki_okf.proposals.render import ReviewRenderer
from okf_ext.proposals import HEADER, BodyRenderer

ADR = Lane(name="adr", directory="adrs/", type_name="Explanation", dated=True)
REFERENCE = Lane(name="reference", directory="references/", type_name="Reference", dated=False)

SOURCES = (
    {
        "id": "src-a",
        "resource": "sources/2026-08-spec.md",
        "title": "The spec",
        "rationale": "It settles the write protocol.",
        "evidence": ["Staging happens before any live file changes.", "Commit is os.replace."],
        "existing_pages_considered": ["adrs/0003-writers.md", "nothing-with-a-slash"],
        "reasoning_summary": "One posture, reusable across capabilities.",
        "potential_conflicts": ["ADR-0003 says the same thing more narrowly."],
        "implementation_notes": ["Name the temp sibling with a uuid."],
    },
)


def _renderer(lane=ADR, target="adrs/bulk-write.md", mode="create") -> ReviewRenderer:
    return ReviewRenderer(lane=lane, target=target, mode=mode)


def test_the_rendered_body_is_exactly_this():
    """Pinned whole, not by fragments -- a body regenerated on every merge is
    worth exactly as much as the reviewer's ability to diff it."""
    assert _renderer()(description="Two sources argue for one page.", sources=SOURCES) == (
        f"{HEADER}\n"
        "\n"
        "## Suggested Action\n"
        "\n"
        "Create new Explanation page `adrs/bulk-write.md`.\n"
        "\n"
        "## Evidence From Source\n"
        "\n"
        "- Staging happens before any live file changes.\n"
        "- Commit is os.replace.\n"
        "\n"
        "## Existing Pages Considered\n"
        "\n"
        "- [/adrs/0003-writers.md](/adrs/0003-writers.md)\n"
        "- nothing-with-a-slash\n"
        "\n"
        "## Reasoning Summary\n"
        "\n"
        "One posture, reusable across capabilities.\n"
        "\n"
        "## Potential Conflicts\n"
        "\n"
        "- ADR-0003 says the same thing more narrowly.\n"
        "\n"
        "## Implementation Notes\n"
        "\n"
        "- Name the temp sibling with a uuid.\n"
        "\n"
        "## Origins\n"
        "\n"
        "**The spec · [/sources/2026-08-spec.md](/sources/2026-08-spec.md)**\n"
        "\n"
        "It settles the write protocol.\n"
    )


def test_every_empty_section_says_so_rather_than_rendering_a_bare_heading():
    assert _renderer()(description="why", sources=()) == (
        f"{HEADER}\n"
        "\n"
        "## Suggested Action\n"
        "\n"
        "Create new Explanation page `adrs/bulk-write.md`.\n"
        "\n"
        "## Evidence From Source\n"
        "\n"
        "- No source evidence was captured.\n"
        "\n"
        "## Existing Pages Considered\n"
        "\n"
        "- No existing pages were cited by the proposal reasoner.\n"
        "\n"
        "## Reasoning Summary\n"
        "\n"
        "No reasoning summary was captured.\n"
        "\n"
        "## Potential Conflicts\n"
        "\n"
        "- No conflicts identified.\n"
        "\n"
        "## Implementation Notes\n"
        "\n"
        "- No implementation notes captured.\n"
        "\n"
        "## Origins\n"
        "\n"
        "No origins were captured.\n"
    )


def test_the_verb_derives_from_the_mode_and_the_sentence_from_the_lane():
    body = _renderer(lane=REFERENCE, target="references/flags.md", mode="update")(description="", sources=())
    assert "Update existing Reference page `references/flags.md`." in body


def test_a_scalar_rides_through_as_a_one_item_list():
    """A producer writing `evidence: one line` is content, not an error."""
    body = _renderer()(description="", sources=[{"id": "a", "resource": "r", "evidence": "just one"}])
    assert "- just one\n" in body


def test_a_source_with_no_label_still_names_itself():
    body = _renderer()(description="", sources=[{"rationale": "because"}])
    assert "**(untitled source)**\n" in body
    assert "because" in body


def test_it_satisfies_the_body_renderer_protocol():
    assert isinstance(_renderer(), BodyRenderer)


def test_rendering_twice_is_byte_identical():
    first = _renderer()(description="why", sources=SOURCES)
    assert first == _renderer()(description="why", sources=SOURCES)


def test_the_newline_is_honoured_throughout():
    rendered = _renderer()(description="why", sources=SOURCES, newline="\r\n")
    assert "\n" not in rendered.replace("\r\n", "")
    assert rendered.endswith("\r\n")


def test_it_never_builds_a_wikilink():
    body = _renderer()(description="", sources=SOURCES)
    assert "[[" not in body
