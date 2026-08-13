"""`raw/<kind>/` in, a manifest of ingest units out.

The manifest carries no file contents: a batch is briefed so a caller can show
an honest "ingesting the first N of M" confirm, and the per-unit prep runs later
against each unit on its own.

`plan_batch_brief` returns `None` for any path that is not a kind-folder root.
That keeps a caller's routing a single check, and it is what makes the CLI's
batch -> folder -> single cascade a cascade.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from doc_wiki_okf.ingest.layout import GRAPH_WIKI_LAYOUT, IngestLayout, resolve_source_path
from doc_wiki_okf.ingest.seams import StateGate, read_state_gate

#: Directory names that are never an ingest unit.
EXCLUDED_DIRS = frozenset({"_archive", "assets"})

#: Kinds whose units are directories rather than files.
DIRECTORY_KINDS = frozenset({"skills", "examples"})

#: Directory kinds that additionally count a loose file as a unit.
LOOSE_FILE_KINDS = frozenset({"examples"})

#: How many units a brief shows unless told otherwise.
DEFAULT_LIMIT = 10

UnitType = Literal["file", "dir"]


@dataclass(frozen=True, slots=True)
class BatchUnit:
    """One thing the batch would ingest."""

    path: Path
    rel: str  # root-relative, posix
    unit_type: UnitType

    def as_data(self) -> dict[str, Any]:
        return {"path": str(self.path), "rel": self.rel, "unit_type": self.unit_type}


@dataclass(frozen=True, slots=True)
class BatchBrief:
    """What a kind folder holds, capped at the limit the caller asked for."""

    kind_folder: str
    root: Path
    units: tuple[BatchUnit, ...]
    total_count: int
    state_gate: Mapping[str, Any] | None

    @property
    def unit_count(self) -> int:
        return len(self.units)

    @property
    def limited(self) -> bool:
        return self.total_count > len(self.units)

    def as_data(self) -> dict[str, Any]:
        """The legacy dict, verbatim -- `is_batch` flag included."""
        return {
            "is_batch": True,
            "kind_folder": self.kind_folder,
            "root": str(self.root),
            "unit_count": self.unit_count,
            "total_count": self.total_count,
            "limited": self.limited,
            "units": [unit.as_data() for unit in self.units],
            "state_gate": None if self.state_gate is None else dict(self.state_gate),
        }


def resolve_batch_root(
    source_path: Path, workspace_root: Path, *, layout: IngestLayout = GRAPH_WIKI_LAYOUT
) -> str | None:
    """The kind name when *source_path* IS a top-level kind folder, else `None`.

    Only the exact `<workspace>/<raw_dir>/<kind>` qualifies. Nested directories,
    files, the raw directory itself, and paths outside the workspace are all the
    single-source flow.
    """
    if not source_path.is_dir():
        return None
    try:
        rel = source_path.resolve().relative_to(workspace_root.resolve())
    except ValueError:
        return None
    if len(rel.parts) == 2 and rel.parts[0] == layout.raw_dir and rel.parts[1] in layout.batch_kinds:
        return rel.parts[1]
    return None


def enumerate_batch_units(kind: str, root: Path) -> tuple[BatchUnit, ...]:
    """The ingest units inside a kind-folder root, sorted by path.

    Flat kinds take every file recursively; `skills` takes each immediate
    subdirectory; `examples` takes each immediate subdirectory plus loose files.
    `_archive` / `assets` components and dotfiles are excluded either way.
    """
    units: list[BatchUnit] = []

    if kind in DIRECTORY_KINDS:
        for child in sorted(root.iterdir()):
            if child.name in EXCLUDED_DIRS or child.name.startswith("."):
                continue
            if child.is_dir():
                units.append(_unit(child, root, "dir"))
            elif kind in LOOSE_FILE_KINDS and child.is_file():
                units.append(_unit(child, root, "file"))
        return tuple(units)

    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if any(part in EXCLUDED_DIRS or part.startswith(".") for part in parts):
            continue
        if path.is_file():
            units.append(_unit(path, root, "file"))
    return tuple(units)


def plan_batch_brief(
    source_path: Path,
    *,
    repo: Path,
    workspace_root: Path,
    limit: int | None = DEFAULT_LIMIT,
    layout: IngestLayout = GRAPH_WIKI_LAYOUT,
    state_gate: StateGate | None = None,
) -> BatchBrief | None:
    """Compute the brief for a kind folder, or `None` for any other path."""
    root = resolve_source_path(source_path, repo)
    kind = resolve_batch_root(root, workspace_root, layout=layout)
    if kind is None:
        return None
    all_units = enumerate_batch_units(kind, root)
    return BatchBrief(
        kind_folder=kind,
        root=root,
        units=all_units if limit is None else all_units[:limit],
        total_count=len(all_units),
        state_gate=read_state_gate(state_gate, repo, workspace_root),
    )


def _unit(path: Path, root: Path, unit_type: UnitType) -> BatchUnit:
    return BatchUnit(path=path, rel=path.relative_to(root).as_posix(), unit_type=unit_type)
