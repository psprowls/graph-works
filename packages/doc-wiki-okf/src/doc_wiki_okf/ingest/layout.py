"""Where a source page goes, as a value you can hold.

`sources/<month>-<slug>.md` was a module constant in the legacy
`wiki_io.ingest_source`. Here it is the one field on a frozen `IngestLayout`
that `plan_document_brief` and `doc_wiki_okf.sources` both take as a defaulted
keyword -- which is what makes the brief's prediction and the writer's target
provably the same string.

This package is tier 3 -- the tier allowed to carry vault vocabulary -- so the
rule that lower bands never know a directory name does not bind here. What the
injection buys is that the name is a value you can replace rather than a
constant you must edit, the shape `DIATAXIS_LANES` already ships in. One field
is still worth a dataclass rather than a bare constant, for exactly that reason.

`raw/` is gone. Material is ingested from outside the workspace, so
`source_kind` and batch `kind` are explicit arguments now rather than inferred
from a folder.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class IngestLayout:
    """The source-page template one vault uses."""

    source_page_template: str


#: Today's value, and the default every caller takes.
GRAPH_WIKI_LAYOUT = IngestLayout(source_page_template="sources/{month}-{slug}.md")


def resolve_source_path(source_path: Path, repo: Path) -> Path:
    """The path a caller-supplied source actually names.

    Legacy kept this private. It is public here because the CLI has to know
    whether a path is a directory *after* resolution, and duplicating the rule
    in `cli.py` is how two copies of it drift.
    """
    if source_path.is_absolute():
        return source_path
    candidate = repo / source_path
    return candidate if candidate.exists() else source_path.resolve()
