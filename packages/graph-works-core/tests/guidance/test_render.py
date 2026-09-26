"""The guidance file's markdown: stream and page headings only when they change."""

from __future__ import annotations

from graph_works_core.guidance.render import (
    NOTE,
    Candidate,
    GuidanceEntry,
    claim_block,
    ledger_block,
    page_heading,
    render_guidance,
)


def _c(stream, group, entry_id, text, kind="claim"):
    return Candidate(stream, group, GuidanceEntry(kind, "/p.md", entry_id, "why", text, 1))


def test_empty_renders_nothing() -> None:
    assert render_guidance("plan", "work/a", ()) == ""


def test_block_formatters_collapse_whitespace() -> None:
    assert claim_block("D1", "The walk\n  skips dots.", ()) == "- **D1** The walk skips dots."
    assert claim_block("C2", "X.", ("a.py", "b/")) == "- **C2** X. _(constrains: `a.py`, `b/`)_"
    assert ledger_block("D-3", "Which?\n", "This one,\nbecause.") == "- **D-3** Which? — This one, because."
    assert page_heading("T", "/adrs/x.md") == "### [T](/adrs/x.md)"
    assert page_heading("T", "/x.md", "Purpose") == "### [T](/x.md) — Purpose"


def test_exact_document_shape() -> None:
    a, b = page_heading("A", "/a.md"), page_heading("B", "/b.md")
    sec = page_heading("P", "/p.md", "Purpose")
    led = page_heading("Epic", "/work/e/references/00-decisions.md")
    rendered = render_guidance(
        "plan",
        "work/e/children/f",
        (
            _c("claims", a, "D1", "- **D1** one"),
            _c("claims", a, "D2", "- **D2** two"),
            _c("claims", b, "C1", "- **C1** three"),
            _c("claims", a, "D9", "- **D9** back to a"),
            _c("sections", sec, "Purpose", "Section text.", kind="section"),
            _c("ledger", led, "D-1", "- **D-1** q — a", kind="ledger"),
        ),
    )
    assert rendered == (
        "# Guidance — plan — work/e/children/f\n\n"
        f"{NOTE}\n\n"
        "## Claims\n\n"
        f"{a}\n\n- **D1** one\n\n- **D2** two\n\n"
        f"{b}\n\n- **C1** three\n\n"
        f"{a}\n\n- **D9** back to a\n\n"
        "## Agent sections\n\n"
        f"{sec}\n\nSection text.\n\n"
        "## Decisions in flight\n\n"
        f"{led}\n\n- **D-1** q — a\n"
    )


def test_a_stream_with_no_candidates_emits_no_heading() -> None:
    rendered = render_guidance("design", "work/a", (_c("ledger", page_heading("E", "/l.md"), "D-1", "- x", "ledger"),))
    assert "## Claims" not in rendered and "## Agent sections" not in rendered
    assert "## Decisions in flight" in rendered
