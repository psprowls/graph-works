"""Brief assembly: point at a path, learn what you are about to ingest.

Three builders, one per mode, each returning a frozen brief carrying the fields
its own mode needs. There is no abstract base and no `is_folder` / `is_batch`
discriminator field -- the type is the discriminator -- and there is no
`plan_brief(path)` that routes among them. A command taking a path has no choice
but to decide what the path is; that cascade lives in `cli.py` and is not
offered as an API.

    from doc_wiki_okf.ingest import plan_document_brief, plan_folder_brief, plan_batch_brief

Each brief carries `as_data()`, which returns the dict the legacy
`wiki_io.ingest_source` function returned for the same input, key for key. That
is the parity contract the port is tested against, and what lets the plugin be
repointed later without an argument about behaviour.

The skill brief does **not** port. It builds a guidance-shaped brief -- chunking
a skill directory into `wiki/guidance/<topic>/<slug>.md` -- which is the guidance
flow, a layer above this one. `reading.gather_skill_sources` is the
substrate-neutral half, and it is ready for whoever claims that layer.

This subpackage imports the standard library and `doc_wiki_okf.reading`, and
nothing else -- not `diataxis/`, not `proposals/`. No Diátaxis type touches a
brief: an ingested document produces a source page, which is a record of a
document rather than a document *of a type*, and the type is chosen when a
proposal is filed against a lane. That is a test
(`tests/test_reading_boundaries.py`), not a convention.
"""

from __future__ import annotations

from doc_wiki_okf.ingest.batch import (
    DEFAULT_LIMIT,
    BatchBrief,
    BatchUnit,
    UnitType,
    enumerate_batch_units,
    plan_batch_brief,
    resolve_batch_root,
)
from doc_wiki_okf.ingest.document import PREVIEW_CHARS, DocumentBrief, plan_document_brief
from doc_wiki_okf.ingest.folder import (
    ERROR_FILE_COUNT,
    LARGE_FILE_BYTES,
    WARN_FILE_COUNT,
    FolderBrief,
    FolderFile,
    FolderRefusal,
    FolderWarning,
    RefusalKind,
    WarningKind,
    plan_folder_brief,
)
from doc_wiki_okf.ingest.layout import (
    GRAPH_WIKI_LAYOUT,
    IngestLayout,
    archive_destination,
    guess_source_type,
    resolve_source_path,
)
from doc_wiki_okf.ingest.seams import NO_ENTITY, EntityMatch, EntityMatcher, StateGate

__all__ = [
    "DEFAULT_LIMIT",
    "ERROR_FILE_COUNT",
    "GRAPH_WIKI_LAYOUT",
    "LARGE_FILE_BYTES",
    "NO_ENTITY",
    "PREVIEW_CHARS",
    "WARN_FILE_COUNT",
    "BatchBrief",
    "BatchUnit",
    "DocumentBrief",
    "EntityMatch",
    "EntityMatcher",
    "FolderBrief",
    "FolderFile",
    "FolderRefusal",
    "FolderWarning",
    "IngestLayout",
    "RefusalKind",
    "StateGate",
    "UnitType",
    "WarningKind",
    "archive_destination",
    "enumerate_batch_units",
    "guess_source_type",
    "plan_batch_brief",
    "plan_document_brief",
    "plan_folder_brief",
    "resolve_batch_root",
    "resolve_source_path",
]
