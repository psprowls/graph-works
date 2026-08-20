"""Where a mirror page lives. One answer, read by every writer in this lane.

The mirror roots at `repositories/<repo>/fs/` -- one level below the
Repository entity page at `repositories/<repo>.md`. The `fs/` level is what
keeps a repo directory literally named `packages/` from colliding with the
bundle's own `packages/` entity lane, and what makes `repositories/<repo>/`
unambiguously "this repo's mirror" rather than a prefix two lanes share --
the distinction `entities/delete.py`'s `exact_depth=`, `ENTITY_DEPTH`'s
`{"Repository": "exact", "File": "nested"}` and
`sync/snapshot.py::_is_entity_repository_page` each re-derive independently.

`File.schema.json`'s `x-okf-directory` stays `repositories/` and
`ENTITY_DEPTH`'s `"nested"` still holds: `placement/rule.py` reads `nested`
as "at least one slash below the declared directory", which
`repositories/<repo>/fs/<path>` satisfies with room to spare.

A leaf module on purpose. `entities/render.py` writes the links into this
lane and is otherwise a pure `describe_* -> Render` translation that imports
nothing else from this package; reaching into `mirror/plan.py` for the
prefix would pull `code_graph_io`, `okf_ext.moves` and
`code_wiki_okf.resources` into that renderer. This pulls `pathlib`.
"""

from __future__ import annotations

from pathlib import Path

#: The bundle-wide lane both the Repository entity pages and every repo's
#: mirror subtree sit under.
REPOSITORIES_LANE = "repositories"

#: The directory level a repo's mirror roots at, below `repositories/<repo>/`.
MIRROR_SUBDIR = "fs"


def mirror_prefix(repo_name: str) -> str:
    """The bundle-relative concept-id prefix for *repo_name*'s mirror pages."""
    return f"{REPOSITORIES_LANE}/{repo_name}/{MIRROR_SUBDIR}"


def mirror_concept_id(repo_name: str, rel_path: str) -> str:
    """The concept id a fresh mirror page for *rel_path* lands at.

    Conventional, not authoritative: an existing page is always found by its
    `resource` key (`resources.resource_index`), never by reconstructing this
    path -- a page may have been moved. This is where a *create* goes.
    """
    return f"{mirror_prefix(repo_name)}/{rel_path}"


def mirror_page_path(bundle_root: Path, repo_name: str, rel_path: str) -> Path:
    """The filesystem path of *rel_path*'s mirror page under *bundle_root*."""
    return bundle_root / REPOSITORIES_LANE / repo_name / MIRROR_SUBDIR / f"{rel_path}.md"


__all__ = [
    "MIRROR_SUBDIR",
    "REPOSITORIES_LANE",
    "mirror_concept_id",
    "mirror_page_path",
    "mirror_prefix",
]
