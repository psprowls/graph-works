"""A directory in, a manifest of ingest units out.

The manifest carries no file contents: a batch is briefed so a caller can show
an honest "ingesting the first N of M" confirm, and the per-unit prep runs later
against each unit on its own.

`kind` is an argument rather than a folder name. `raw/<kind>/` was what defined
a batch, and `raw/` is retired; briefing thirty downloaded articles as "the
first 10 of 30" is independently useful and survives the retirement, re-based on
an explicit kind. `DIRECTORY_KINDS` keeps its meaning.

`plan_batch_brief` returns `None` only for a path that is not a directory, so a
caller's routing stays a single check.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from doc_wiki_okf.ingest.layout import resolve_source_path
from doc_wiki_okf.ingest.seams import StateGate, read_state_gate

#: Directory names that are never an ingest unit.
EXCLUDED_DIRS = frozenset({"_archive", "assets"})

#: Kinds whose units are directories rather than files. One rule, because
#: `skills` is the one that earns its keep: a skill is a directory. `examples`
#: was the other member and its only effect was that loose files alongside
#: subdirectories also counted; it is a flat kind now like every other.
DIRECTORY_KINDS = frozenset({"skills"})

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


def enumerate_batch_units(kind: str, root: Path) -> tuple[BatchUnit, ...]:
    """The ingest units inside a kind-folder root, sorted by path.

    `skills` takes each immediate subdirectory; every other kind takes every
    file recursively. `_archive` / `assets` components and dotfiles are
    excluded either way.
    """
    units: list[BatchUnit] = []

    if kind in DIRECTORY_KINDS:
        for child in sorted(root.iterdir()):
            if child.name in EXCLUDED_DIRS or child.name.startswith("."):
                continue
            if child.is_dir():
                units.append(_unit(child, root, "dir"))
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
    kind: str,
    repo: Path,
    workspace_root: Path,
    limit: int | None = DEFAULT_LIMIT,
    state_gate: StateGate | None = None,
) -> BatchBrief | None:
    """Compute the brief for a directory of *kind*, or `None` for a non-directory.

    `kind` decides enumeration, not location: `skills` takes each immediate
    subdirectory, everything else recurses.
    """
    root = resolve_source_path(source_path, repo)
    if not root.is_dir():
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
