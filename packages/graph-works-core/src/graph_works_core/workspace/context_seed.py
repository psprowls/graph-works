"""Render the workspace's `CLAUDE.md` / `AGENTS.md` -- the file
`graph_works_core.prompts.project_context` reads for its `## Style` and
`## Log format` sections.

Three cases, and no others: first render, marker-bounded re-render, and
marker-deleted append. The auto block lists the installed verticals by
top-level module name, since `plan_init(installers=...)` is this package's
per-workspace roster.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

AUTO_START = "<!-- graph-works:auto:installers:start -->"
AUTO_END = "<!-- graph-works:auto:installers:end -->"

_BLOCK_RE = re.compile(re.escape(AUTO_START) + r".*?" + re.escape(AUTO_END), re.DOTALL)

_TEMPLATE_PATH = Path(__file__).resolve().parent / "assets" / "CLAUDE.md.template"


def _render_installer_list(installers: Sequence[Callable[..., object]]) -> str:
    """The markdown body between the auto-block markers.

    Installers are identified by their top-level module -- `code_wiki_okf`,
    not `code_wiki_okf.init` -- since that is the name a human recognizes as
    "the vertical", and de-duplicated while preserving roster order.
    """
    if not installers:
        return "_No installers registered yet._"
    names = list(dict.fromkeys(installer.__module__.split(".")[0] for installer in installers))
    return "\n".join(f"- `{name}`" for name in names)


def _render_full_template(workspace: Path, installers: Sequence[Callable[..., object]], today: date) -> str:
    """Render the full template once (first-create path)."""
    text = _TEMPLATE_PATH.read_text(encoding="utf-8")
    text = text.replace("{{WORKSPACE_PATH}}", str(workspace))
    text = text.replace("{{INITIALIZED_AT}}", today.isoformat())
    text = text.replace("{{INSTALLER_LIST}}", _render_installer_list(installers))
    return text


def _refresh_auto_block(text: str, installers: Sequence[Callable[..., object]]) -> str:
    """Replace the existing auto block in `text` with a freshly rendered one."""
    block = AUTO_START + "\n" + _render_installer_list(installers) + "\n" + AUTO_END
    return _BLOCK_RE.sub(lambda _: block, text, count=1)


def _append_auto_block(text: str, installers: Sequence[Callable[..., object]]) -> str:
    """The markers were deleted -- append a fresh block rather than rewriting a
    file the human has clearly taken over."""
    block = AUTO_START + "\n" + _render_installer_list(installers) + "\n" + AUTO_END + "\n"
    sep = "" if text.endswith("\n") else "\n"
    return text + sep + "\n" + block


def render_context_file(
    existing: str | None,
    *,
    workspace: Path,
    installers: Sequence[Callable[..., object]],
    today: date,
) -> str:
    """Render the full text a context file (`CLAUDE.md` or `AGENTS.md`) should
    hold, given its *existing* text (`None` when the file does not yet exist).

    Three cases:

    - *existing* is `None`: fill the template -- workspace path, `today`, and
      the auto block.
    - *existing* holds both markers: splice a freshly rendered auto block into
      it; everything else is byte-identical.
    - *existing* is missing a marker: append a fresh auto block rather than
      rewriting a file the human has clearly taken over.

    Pure: writes nothing. The caller decides whether the result differs from
    *existing* and is worth writing.
    """
    if existing is None:
        return _render_full_template(workspace, installers, today)
    if AUTO_START in existing and AUTO_END in existing:
        return _refresh_auto_block(existing, installers)
    return _append_auto_block(existing, installers)


__all__ = ["AUTO_END", "AUTO_START", "render_context_file"]
