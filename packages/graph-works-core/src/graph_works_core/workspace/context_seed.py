"""Render the workspace's `AGENTS.md` -- the file
`graph_works_core.prompts.project_context` reads for its `## Style` and
`## Log format` sections -- and name the `CLAUDE.md` pointer beside it.

Two regions, one boundary. Everything above the whole-line heading
`## Local Conventions` is gw's and is regenerated from
`assets/AGENTS.md.template` on every render; that heading through end-of-file
is the human's and is carried byte for byte. When the heading is absent the
tail is an empty `## Local Conventions` section, which also means a file
written before this renderer existed is replaced whole. No HTML-comment
markers: the boundary is a heading a reader already understands, and the gw
body mentions it only as inline code, never as a line of its own.

Nothing in the rendered body varies run to run. `initialized_at` comes from
the manifest, the two directory names from the layout, the topic from the
manifest -- so a second render is a no-op and `plan_init` stays idempotent.

`CLAUDE.md` is not rendered here at all: it is the one-line pointer
`CLAUDE_POINTER`, and the caller compares the file to that constant.
"""

from __future__ import annotations

from pathlib import Path

LOCAL_CONVENTIONS_HEADING = "## Local Conventions"
CLAUDE_POINTER = "@AGENTS.md\n"

_TEMPLATE_PATH = Path(__file__).resolve().parent / "assets" / "AGENTS.md.template"
_UNSET_TOPIC = "(unset)"


def _render_body(*, topic: str | None, initialized_at: str, bundle_dir: str, config_dir: str) -> str:
    """The gw region: the template with its four placeholders filled, ending
    in exactly one newline."""
    text = _TEMPLATE_PATH.read_text(encoding="utf-8")
    text = text.replace("{{TOPIC}}", topic if topic is not None else _UNSET_TOPIC)
    text = text.replace("{{INITIALIZED_AT}}", initialized_at)
    text = text.replace("{{BUNDLE_DIR}}", bundle_dir)
    text = text.replace("{{CONFIG_DIR}}", config_dir)
    return text.rstrip("\n") + "\n"


def _local_tail(existing: str | None) -> str:
    """The human region: from the first whole line equal to
    `## Local Conventions` (trailing whitespace ignored) through end-of-file,
    byte for byte; an empty section when *existing* is `None` or has no such
    line."""
    if existing is not None:
        lines = existing.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.rstrip() == LOCAL_CONVENTIONS_HEADING:
                return "".join(lines[index:])
    return LOCAL_CONVENTIONS_HEADING + "\n"


def render_context_file(
    existing: str | None,
    *,
    topic: str | None,
    initialized_at: str,
    bundle_dir: str,
    config_dir: str,
) -> str:
    """Render the full text `AGENTS.md` should hold, given its *existing* text
    (`None` when the file does not yet exist).

    `body + "\\n" + tail`: the gw region freshly rendered, one blank line,
    then the human region carried from *existing*. Pure: writes nothing. The
    caller decides whether the result differs from *existing* and is worth
    writing. Never raises on content -- a heading on the first line simply
    means the gw region was empty and is replaced whole.
    """
    body = _render_body(topic=topic, initialized_at=initialized_at, bundle_dir=bundle_dir, config_dir=config_dir)
    return body + "\n" + _local_tail(existing)


__all__ = ["CLAUDE_POINTER", "LOCAL_CONVENTIONS_HEADING", "render_context_file"]
