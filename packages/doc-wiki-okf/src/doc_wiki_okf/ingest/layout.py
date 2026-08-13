"""Where things live, as a value you can hold.

`RAW_FOLDER_TYPE_MAP`, `BATCH_KIND_FOLDERS`, `raw/_archive/` and the
`sources/<month>-<slug>.md` template were module constants in the legacy
`wiki_io.ingest_source`. Here they are fields on one frozen `IngestLayout` that
every builder takes as a defaulted keyword.

The default is the point. This package is tier 3 -- the tier allowed to carry
vault vocabulary -- so the rule that lower bands never know a directory name
does not bind here. What the injection buys is that the names are a value you
can replace rather than a constant you must edit, the shape `DIATAXIS_LANES`
already ships in.

`source_types` maps a **path component** to the source type it decides, and is
consulted in insertion order. It carries both `skill` and `skills`: legacy's
chain checked the singular only, and the plural was decided by
`build_skill_ingest_brief` hardcoding its answer -- the one function this port
declines. Every path legacy actually decided keeps its answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

#: A `work/` document whose stem ends in this is a spec wherever it sits.
WORK_SPEC_SUFFIX = "-spec"


@dataclass(frozen=True, slots=True)
class IngestLayout:
    """The directory names and the source-page template one vault uses."""

    raw_dir: str
    archive_dir: str
    source_types: Mapping[str, str]
    batch_kinds: frozenset[str]
    source_page_template: str


#: Today's values, and the default every builder takes.
GRAPH_WIKI_LAYOUT = IngestLayout(
    raw_dir="raw",
    archive_dir="_archive",
    source_types=MappingProxyType(
        {
            "specs": "spec",
            "articles": "article",
            "prs": "pr",
            "tickets": "ticket",
            "transcripts": "transcript",
            "examples": "example",
            "skill": "skill",
            "skills": "skill",
        }
    ),
    batch_kinds=frozenset({"specs", "articles", "prs", "tickets", "transcripts", "examples", "skills"}),
    source_page_template="sources/{month}-{slug}.md",
)


def guess_source_type(
    rel_to_workspace: Path | None,
    rel_to_repo: Path | None,
    *,
    layout: IngestLayout = GRAPH_WIKI_LAYOUT,
) -> str:
    """Guess a source type from where the file lives.

    `rel_to_workspace` is the source relative to the **workspace** root, not the
    wiki: `raw/` is a sibling of `wiki/`, and measuring from the wiki misses it
    entirely. `rel_to_repo` is the repo-relative path of an in-repo doc. Either
    may be `None`.

    The `work/…-spec` rule is checked first. It cannot change any answer by
    being first -- the only key that could precede it is `specs`, which decides
    `spec` too -- and it keeps the loop a plain mapping walk.
    """
    if rel_to_workspace is not None:
        parts = set(rel_to_workspace.parts)
        if "work" in parts and rel_to_workspace.stem.endswith(WORK_SPEC_SUFFIX):
            return "spec"
        for folder, source_type in layout.source_types.items():
            if folder in parts:
                return source_type
    if rel_to_repo is not None:
        return "doc"
    return "note"


def archive_destination(raw: Path, unit: Path, *, layout: IngestLayout = GRAPH_WIKI_LAYOUT) -> Path | None:
    """Where an ingested unit is filed once it is done, or `None`.

    Pure path math, no I/O: the caller performs (or skips) the move. `None` when
    `unit` is not under `raw`, is `raw` itself, or is already archived.
    """
    try:
        rel = unit.relative_to(raw)
    except ValueError:
        return None
    if not rel.parts or rel.parts[0] == layout.archive_dir:
        return None
    return raw / layout.archive_dir / rel


def resolve_source_path(source_path: Path, repo: Path) -> Path:
    """The path a caller-supplied source actually names.

    Legacy kept this private. It is public here because the CLI's
    batch -> folder -> single cascade has to know whether a path is a directory
    *after* resolution, and duplicating the rule in `cli.py` is how two copies
    of it drift.
    """
    if source_path.is_absolute():
        return source_path
    candidate = repo / source_path
    return candidate if candidate.exists() else source_path.resolve()
