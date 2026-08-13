"""Synthetic bundles and lane sets for the proposals subpackage's tests."""

from __future__ import annotations

import importlib.resources
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from doc_wiki_okf.proposals.lanes import LaneSet, lane_set
from doc_wiki_okf.resources import seed_files
from okf_ext.schemas import SchemaSet, load_schemas
from okf_ext.shape import SectionSet, load_sections
from okf_io import Bundle, load_bundle

TODAY = date(2026, 8, 12)
AT = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
BY = "agent:test"


def schema_set() -> SchemaSet:
    return load_schemas(str(importlib.resources.files("doc_wiki_okf") / "assets" / "_schema"))


def section_set() -> SectionSet:
    return load_sections(str(importlib.resources.files("doc_wiki_okf") / "assets" / "_sections"))


def lanes() -> LaneSet:
    return lane_set(schema_set())


def seeded_root(root: Path) -> Path:
    """*root* with an `index.md` and this package's ten declaration files."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    for relative, text in seed_files().items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def build_bundle(root: Path, pages: Mapping[str, str] | None = None) -> Bundle:
    """Load *root* as a bundle, writing *pages* (concept id -> text) first.

    `_schema/` and `_sections/` are ignored so the declarations are not read
    back as concepts -- `okf_ext.schemas.DEFAULT_IGNORE`'s two patterns, plus
    the same pair for `_sections/`.
    """
    seeded_root(root)
    for concept_id, text in (pages or {}).items():
        target = root / f"{concept_id}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return load_bundle(root, ignore=("_schema/*", "*/_schema/*", "_sections/*", "*/_sections/*"))


def source(identifier: str, resource: str, **extra: object) -> dict[str, object]:
    """One `sources[]` entry, in the shape the ingest producer writes."""
    return {"id": identifier, "resource": resource, **extra}


#: Source entries copied from the live ledger at
#: `$GRAPH_WIKI_WORKSPACE/wiki/proposals/`, re-keyed from `origins[]`
#: (`ref` -> `resource`; `source: ingest` dropped -- it was a producer tag with
#: no home in `sources[]`). Inlined rather than read, so this suite depends on
#: nothing outside the repo.
LIVE_SOURCES: dict[str, dict[str, object]] = {
    "many-evidence": {
        "id": "src-scaffold-spec",
        "resource": "sources/2026-08-design-spec-scaffold-okf-ext-and-tag-management.md",
        "rationale": (
            "Not anticipated by the spec -- it emerged from implementing the one-paragraph "
            "serialize-then-write model and is a reusable posture for every future okf-ext "
            "capability that writes."
        ),
        "evidence": [
            "Three failure regimes with different guarantees, documented in okf_ext.tags.rename.apply.",
            "The atomicity guarantee comes from staging, not the probe.",
            "Commit is Path.replace (os.replace): a single filesystem rename.",
            "Two declined non-goals: no journal, and no fsync on temp files.",
            "WriteFailure carries a machine-readable kind alongside the rendered error.",
        ],
    },
    "no-evidence": {
        "id": "src-bare",
        "resource": "sources/2026-08-bare.md",
        "rationale": "One line of reasoning and nothing else captured.",
    },
    "with-considered": {
        "id": "src-declarations",
        "resource": "sources/2026-08-one-declaration-two-readers.md",
        "rationale": "Two capabilities read one declaration set.",
        "evidence": ["sections seeds and validates; generators regenerates."],
        "existing_pages_considered": ["adrs/0012-pascalcase-types.md", "concepts/machine-human-ownership.md"],
        "reasoning_summary": "One declaration, two readers, no second copy.",
        "potential_conflicts": ["The sections README states the narrower claim."],
        "implementation_notes": ["SectionSet keeps its name."],
    },
}
