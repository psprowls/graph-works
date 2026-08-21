"""`context_seed`'s three-case renderer: first-create, marker refresh, and
marker-deleted append -- ported from `workspace_io.render`'s own suite for
this package's layout and installer roster."""

from __future__ import annotations

from datetime import date

from graph_works_core.workspace.context_seed import AUTO_END, AUTO_START, render_context_file

TODAY = date(2026, 8, 20)


def _installer(module_name: str):
    def fn(*args, **kwargs):
        raise NotImplementedError

    fn.__module__ = f"{module_name}.init"
    return fn


INSTALLERS = (_installer("code_wiki_okf"), _installer("work_tracker_okf"))


def test_first_render_fills_the_template(tmp_path):
    text = render_context_file(None, workspace=tmp_path, installers=INSTALLERS, today=TODAY)
    assert str(tmp_path) in text
    assert "2026-08-20" in text
    assert "code_wiki_okf" in text
    assert "work_tracker_okf" in text
    assert AUTO_START in text
    assert AUTO_END in text


def test_first_render_with_no_installers_says_so(tmp_path):
    text = render_context_file(None, workspace=tmp_path, installers=(), today=TODAY)
    assert "no installers" in text.lower()


def test_rerender_with_markers_present_preserves_prose_outside_them(tmp_path):
    first = render_context_file(None, workspace=tmp_path, installers=INSTALLERS, today=TODAY)
    edited = first.replace(
        "## Conventions for LLM agents",
        "## Conventions for LLM agents\n\nHand-edited note that must survive.",
    )
    second = render_context_file(edited, workspace=tmp_path, installers=INSTALLERS, today=TODAY)
    assert "Hand-edited note that must survive." in second


def test_rerender_with_an_added_installer_refreshes_only_the_block(tmp_path):
    first = render_context_file(None, workspace=tmp_path, installers=INSTALLERS, today=TODAY)
    grown = (*INSTALLERS, _installer("doc_wiki_okf"))
    second = render_context_file(first, workspace=tmp_path, installers=grown, today=TODAY)
    assert "doc_wiki_okf" in second
    assert "2026-08-20" in second  # header untouched by a block-only refresh


def test_rerender_is_a_no_op_when_nothing_changed(tmp_path):
    first = render_context_file(None, workspace=tmp_path, installers=INSTALLERS, today=TODAY)
    second = render_context_file(first, workspace=tmp_path, installers=INSTALLERS, today=TODAY)
    assert second == first


def test_rerender_with_markers_deleted_appends_rather_than_clobbers(tmp_path):
    hand_written = "# My Workspace\n\nCompletely custom content, no markers at all.\n"
    rendered = render_context_file(hand_written, workspace=tmp_path, installers=INSTALLERS, today=TODAY)
    assert "Completely custom content" in rendered
    assert rendered.index("Completely custom content") < rendered.index(AUTO_START)
    assert "code_wiki_okf" in rendered
