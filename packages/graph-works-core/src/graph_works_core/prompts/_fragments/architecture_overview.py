# Adopted from plugins/graph-wiki/skills/graph-wiki/SKILL.md §Architecture — rewritten as a renderer.

"""The layout half of the shared prompt, rendered rather than frozen.

As a constant this fragment hard-coded `<repo>/graph-wiki/` with `raw/`,
`work/`, `wiki/` and a fixed lane list. All four graph-works layout members
are manifest-overridable, so freezing any of them into a prompt string would
contradict the workspace's own configuration. Lane names inside the bundle
come from the bundle's own declarations, never from this function.
"""

from __future__ import annotations

from pathlib import Path

from graph_works_core.workspace.layout import MANIFEST_FILENAME, WorkspaceLayout


def _name(path: Path, root: Path) -> str:
    """*path* relative to *root*, or absolute when it is not under it — an
    override may put the cache on another volume, and the tree should say so."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def render_architecture_overview(layout: WorkspaceLayout) -> str:
    """The workspace-layout block for a subagent system prompt."""
    rows = [
        (MANIFEST_FILENAME, "the manifest — version, topic, layout overrides"),
        (
            f"{_name(layout.bundle_dir, layout.root)}/",
            "the OKF bundle: concept documents, in the lanes the bundle declares",
        ),
        (
            f"{_name(layout.config_dir, layout.root)}/",
            "committed declarations: `_schema/`, `_sections/`, `_tags.yaml`",
        ),
        (
            f"{_name(layout.cache_dir, layout.root)}/",
            "gitignored machine state: the graph database",
        ),
        (
            f"{_name(layout.worktrees_dir, layout.root)}/",
            "gitignored feature worktrees",
        ),
    ]
    lines = [
        f"{'└──' if index == len(rows) - 1 else '├──'} {name}  # {note}" for index, (name, note) in enumerate(rows)
    ]
    tree = "\n".join(lines)
    return f"""\
## Workspace layout

The workspace lives at `{layout.root}`. Every directory below is declared in `{MANIFEST_FILENAME}`, so these are
this workspace's names, not fixed constants.

```
{layout.root.name}/
{tree}
```

**The code is the source of truth.** The wiki is a compiled layer above it. If the wiki disagrees with the code, the code wins — the wiki gets updated.\
"""


__all__ = ["render_architecture_overview"]
