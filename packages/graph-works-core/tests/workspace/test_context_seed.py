"""`context_seed`'s two-region renderer: the gw body above `## Local
Conventions` is regenerated whole; the heading and everything below it is the
human's and survives byte for byte."""

from __future__ import annotations

from pathlib import Path

from graph_works_core.workspace.context_seed import (
    CLAUDE_POINTER,
    LOCAL_CONVENTIONS_HEADING,
    render_context_file,
)

_CLAUDE_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "graph_works_core" / "workspace" / "assets" / "CLAUDE.md.template"
)


def _render(existing: str | None = None, **overrides):
    kwargs = {"topic": "Demo", "initialized_at": "2026-08-20", "bundle_dir": "okf", "config_dir": ".gw"}
    kwargs.update(overrides)
    return render_context_file(existing, **kwargs)


def _heading_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.rstrip() == LOCAL_CONVENTIONS_HEADING]


# --- first render -------------------------------------------------------------


def test_first_render_ends_with_an_empty_local_conventions_section():
    text = _render()
    assert text.endswith("\n\n## Local Conventions\n")
    assert _heading_lines(text) == [LOCAL_CONVENTIONS_HEADING]


def test_first_render_substitutes_every_placeholder():
    text = _render()
    assert "`Demo`" in text
    assert "2026-08-20" in text
    assert "okf/" in text
    assert ".gw/" in text
    assert "{{" not in text


def test_a_custom_layout_is_named_not_assumed():
    text = _render(bundle_dir="content", config_dir="control")
    assert "content/" in text
    assert "control/" in text


def test_the_body_carries_no_machine_path_no_marker_and_no_legacy_skill_name():
    text = _render()
    assert "/Users/" not in text
    assert "graph-works:auto" not in text
    assert "<!--" not in text
    assert "/graph-works:" not in text
    assert "/gw:" in text


def test_the_body_teaches_the_iso_log_heading_and_the_local_yaml_layer():
    text = _render()
    assert "## YYYY-MM-DD" in text
    assert "[YYYY-MM-DD]" not in text
    assert "workspace.local.yaml" in text
    assert "## Style" in text
    assert "## Log format" in text


def test_an_unset_topic_renders_without_raising():
    assert "(unset)" in _render(topic=None)


# --- the human tail -----------------------------------------------------------


def test_a_tail_beneath_the_heading_survives_byte_for_byte():
    tail = "## Local Conventions   \n\nTeam note.  \n- keep this\n\n\nno trailing newline"
    head, _, _ = _render().rpartition("## Local Conventions\n")
    assert _render(head + tail).endswith(tail)


def test_a_crlf_tail_survives_byte_for_byte():
    tail = "## Local Conventions\r\n\r\nMine.\r\n"
    head, _, _ = _render().rpartition("## Local Conventions\n")
    assert _render(head + tail).endswith(tail)


def test_stale_gw_prose_above_the_heading_is_replaced():
    stale = "# Old header\n\n/graph-works:scan lives here\n\n## Local Conventions\n\nMine.\n"
    text = _render(stale)
    assert "# Old header" not in text
    assert "/graph-works:scan" not in text
    assert text.endswith("\n\n## Local Conventions\n\nMine.\n")
    assert _heading_lines(text) == [LOCAL_CONVENTIONS_HEADING]


def test_a_file_without_the_heading_is_replaced_whole_and_gains_the_heading():
    text = _render("# My Workspace\n\nCompletely custom content, no heading at all.\n")
    assert "Completely custom content" not in text
    assert text == _render()


def test_a_heading_on_the_first_line_replaces_the_whole_gw_region():
    text = _render("## Local Conventions\nOnly mine.\n")
    assert text.startswith("# Graph-Works Workspace")
    assert text.endswith("\n\n## Local Conventions\nOnly mine.\n")


def test_only_a_whole_line_heading_is_the_boundary():
    # An inline mention of the heading (as the body's own closing paragraph
    # makes) is not a boundary; neither is a deeper heading.
    decoy = "### Local Conventions\nnot the boundary\n"
    text = _render(decoy)
    assert "not the boundary" not in text
    assert text == _render()


# --- idempotence --------------------------------------------------------------


def test_rendering_is_idempotent():
    once = _render()
    assert _render(once) == once
    with_tail = once + "\nMy rule.\n"
    assert _render(with_tail) == with_tail
    assert _render(_render(with_tail)) == with_tail


# --- the pointer --------------------------------------------------------------


def test_the_claude_pointer_is_one_line():
    assert CLAUDE_POINTER == "@AGENTS.md\n"


def test_the_shipped_claude_template_matches_the_pointer_constant():
    # `CLAUDE.md.template` is not read by any code path -- `CLAUDE_POINTER` is
    # used directly instead -- but it ships deliberately, for a human looking
    # for a template beside `AGENTS.md.template`. Nothing else pins the two
    # together, so a drift between them would go unnoticed.
    assert _CLAUDE_TEMPLATE_PATH.read_text(encoding="utf-8") == CLAUDE_POINTER
