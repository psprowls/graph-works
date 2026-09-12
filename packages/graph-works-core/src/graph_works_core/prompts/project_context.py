"""Project-context renderer for subagent system prompts.

Reads the workspace's own `AGENTS.md` (or `CLAUDE.md` as fallback) from the
workspace root and emits a compact deterministic block covering the `## Style`
rules and the `## Log format` section.

Pure: no LLM calls, no network, no mutation. Returns the empty string when
neither file exists, so callers can pass the result through unchanged.

The location is the workspace root, `layout.root`, and only that: gw owns the
context files it writes there and never reads a catalogued repo's own root
files (work item `feature-unified-context-files-local-overlays`, D-001). It
takes the **layout**, not a directory, so that two consumers of one renderer
cannot look in two places — which is exactly what `ingest` and `lint` did,
and one of the two read a directory nothing ever writes a context file into.

`AGENTS.md` is tried first because `CLAUDE.md` at the workspace root is, by
construction, the one-line `@AGENTS.md` pointer `workspace.init` writes; a
`CLAUDE.md`-first order would return `""` for every bootstrapped workspace.
No `@` pointer is followed (D-002).

The fence-aware section walker is kept verbatim — it exists so a
`## [YYYY-MM-DD]` sample inside a fenced code block does not falsely terminate
the `## Log format` section, which is a named regression, not incidental care.
"""

from __future__ import annotations

from pathlib import Path

from graph_works_core.workspace.layout import WorkspaceLayout

# AGENTS.md takes priority; CLAUDE.md is the fallback (it is normally a pointer).
_CANDIDATES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")


def render_project_context(layout: WorkspaceLayout) -> str:
    """Render a compact project-context block for subagent system prompts.

    Reads `AGENTS.md` if it exists, else `CLAUDE.md`, from the workspace root.
    Composes `## Project style (<file> §Style)` and
    `## Log format (<file> §Log format)`, each omitted when its section is
    absent. Returns `""` when neither file exists, so a workspace that
    declares no project context simply contributes no block; never raises for
    a missing file.
    """
    context_dir = layout.root
    for name in _CANDIDATES:
        schema = context_dir / name
        if schema.exists():
            return _render(schema)
    return ""


def _render(schema_path: Path) -> str:
    """Compose the rendered block for one schema file."""
    filename = schema_path.name
    sections: list[str] = []

    style_body = _extract_section(schema_path, "Style")
    if style_body:
        sections.append(f"## Project style ({filename} §Style)\n\n{style_body}")

    log_body = _extract_section(schema_path, "Log format")
    if log_body:
        sections.append(f"## Log format ({filename} §Log format)\n\n{log_body}")

    return "\n\n".join(sections).rstrip()


def _extract_section(schema_path: Path, heading: str) -> str:
    """Return the body of the first `## <heading>` section in *schema_path*.

    Body starts after the matching `## <heading>` line and continues until the
    next document-level `## ` heading or end-of-file. Fenced code blocks
    (``` or ~~~) are tracked so headings inside them do not terminate the
    section. Leading and trailing blank lines are stripped.
    """
    text = schema_path.read_text(encoding="utf-8")
    target = f"## {heading}"
    body: list[str] = []
    in_section = False
    in_fence = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if in_section:
            if not in_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
                in_fence = True
            elif in_fence and (stripped.startswith("```") or stripped.startswith("~~~")):
                in_fence = False
            elif not in_fence and line.startswith("## "):
                break
            body.append(line)
        elif line.strip() == target:
            in_section = True
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body)


__all__ = ["render_project_context"]
