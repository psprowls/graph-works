"""The refresher's prompt, its parser, and the sanitizer both phases share."""

from __future__ import annotations

import json

import pytest
from graph_works_core.scan.prose_refresher import (
    MAX_PROMPT_DIFF_CHARS,
    PROSE_REFRESHER_SYSTEM,
    build_prose_refresh_prompt,
    parse_prose_refresher_output,
    sanitize_prose_result,
)
from graph_works_core.scan.scan_contract import ProseRefreshTask

ALLOWED = ("## Purpose", "## Public API")


def _task(**overrides) -> ProseRefreshTask:
    base = {
        "uri": "pkg:acme/demo/widgets",
        "kind": "Package",
        "name": "widgets",
        "page_path": "packages/widgets.md",
        "entity_root": "/repo/packages/widgets",
        "trigger": "first_fill",
        "diff": None,
        "changed_files": (),
        "page_content": "---\ntype: Package\n---\n\n## Purpose\n\n> TODO: <One paragraph>\n",
        "prose_sections": {"## Purpose": "> TODO: <One paragraph>", "## Public API": ""},
        "graph_context": "package widgets (python)",
        "owning_short_head": "0123456789ab",
    }
    base.update(overrides)
    return ProseRefreshTask(**base)


def test_the_system_prompt_forbids_touching_generated_sections():
    assert "generated" in PROSE_REFRESHER_SYSTEM.lower()
    assert "json" in PROSE_REFRESHER_SYSTEM.lower()


def test_the_prompt_names_every_declared_heading_and_its_current_body():
    prompt = build_prose_refresh_prompt(_task())
    assert "## Purpose" in prompt
    assert "## Public API" in prompt
    assert "> TODO: <One paragraph>" in prompt
    assert "widgets" in prompt
    assert "0123456789ab" in prompt


def test_a_rewritten_history_tells_the_model_to_re_read():
    prompt = build_prose_refresh_prompt(_task(trigger="diff", diff=None))
    assert "history" in prompt.lower()
    assert "re-read" in prompt.lower()


def test_a_diff_trigger_renders_the_changed_files():
    prompt = build_prose_refresh_prompt(
        _task(trigger="diff", diff="src/a.py\nsrc/b.py", changed_files=("src/a.py", "src/b.py"))
    )
    assert "src/a.py" in prompt
    assert "src/b.py" in prompt


@pytest.mark.parametrize(
    "text",
    [
        json.dumps({"## Purpose": "Real prose."}),
        json.dumps({"sections": {"## Purpose": "Real prose."}}),
        "```json\n" + json.dumps({"sections": {"## Purpose": "Real prose."}}) + "\n```",
    ],
)
def test_every_accepted_output_shape_parses(text):
    sections, parsed = parse_prose_refresher_output(text)
    assert parsed
    assert sections == {"## Purpose": "Real prose."}


def test_an_empty_section_map_is_an_answer_not_a_failure():
    assert parse_prose_refresher_output(json.dumps({"sections": {}})) == ({}, True)


@pytest.mark.parametrize("text", ["not json at all", "", "[1, 2, 3]", json.dumps({"sections": 7})])
def test_an_unusable_shape_does_not_parse(text):
    sections, parsed = parse_prose_refresher_output(text)
    assert (sections, parsed) == ({}, False)


def test_sanitize_drops_an_undeclared_heading():
    assert sanitize_prose_result({"## Files": "generated!"}, allowed=ALLOWED) == {}


@pytest.mark.parametrize(
    "body",
    ["", "   \n\n", "> TODO: <One paragraph>", "TODO: write this", "tbd", "FIXME later", "> fixme"],
)
def test_sanitize_drops_a_todo_shaped_body(body):
    assert sanitize_prose_result({"## Purpose": body}, allowed=ALLOWED) == {}


def test_sanitize_keeps_real_prose_and_strips_its_edges():
    cleaned = sanitize_prose_result({"## Purpose": "\n\nReal prose.\n\n"}, allowed=ALLOWED)
    assert cleaned == {"## Purpose": "Real prose."}


def test_a_first_fill_that_is_also_diff_stale_carries_its_changed_files():
    """F1: `first_fill` wins the trigger when both apply, and the prompt used to
    read only the trigger -- so the task's `diff` and `changed_files` were built,
    carried across the wire, and then never shown to the model."""
    prompt = build_prose_refresh_prompt(
        _task(trigger="first_fill", diff="src/a.py\nsrc/b.py", changed_files=("src/a.py", "src/b.py"))
    )
    assert "never been written" in prompt
    assert "src/a.py" in prompt
    assert "src/b.py" in prompt


def test_a_plain_first_fill_still_names_only_the_placeholder():
    prompt = build_prose_refresh_prompt(_task(trigger="first_fill", diff=None))
    assert "never been written" in prompt
    assert "Changed files:" not in prompt


def test_the_truncation_marker_reaches_the_model():
    """The `[TRUNCATED …]` line `_render_diff` produces rides in the diff string
    the prompt renders verbatim -- it is not a contract field, so this is where
    it has to be visible."""
    marker = "[TRUNCATED after 200 of 431 changed files]"
    prompt = build_prose_refresh_prompt(_task(trigger="diff", diff=f"{marker}\nsrc/a.py"))
    assert marker in prompt


def test_the_truncation_marker_survives_the_prompt_character_budget():
    """A large enough change set renders past `MAX_PROMPT_DIFF_CHARS`, and the
    prompt truncates `task.diff` with `truncate_text`, which cuts from the END.
    A marker `_render_diff` trailed used to be discarded in exactly that case --
    real monorepo-shaped paths were measured at ~14,000 rendered chars against
    the 8,000-char budget, right where the file-count cut fires. Leading the
    rendered diff with the marker instead means it survives the cut."""
    from graph_works_core.scan.commands import MAX_TASK_DIFF_FILES, _render_diff

    total = MAX_TASK_DIFF_FILES + 50
    changed = [f"packages/some-package-{n}/src/some_package_{n}/module_{n}.py" for n in range(total)]
    rendered, kept = _render_diff(changed)
    assert len(kept) == MAX_TASK_DIFF_FILES
    assert len(rendered) > MAX_PROMPT_DIFF_CHARS

    marker = f"[TRUNCATED after {MAX_TASK_DIFF_FILES} of {total} changed files]"
    assert rendered.startswith(marker)

    prompt = build_prose_refresh_prompt(_task(trigger="diff", diff=rendered))
    assert marker in prompt
