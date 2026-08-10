"""Frozen values the bundle capability passes between its stages.

All frozen and slotted, matching `okf_ext.proposals.model` and
`okf_ext.moves.model`. `Skipped`, `WriteFailure` and `ApplyResult` are **not**
here: they come from `okf_ext.writing`, the shared layer, so a caller
discriminating a refusal has one type to match rather than one per capability.

This module imports stdlib and the shared write layer only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from okf_ext.writing import Skipped, WriteFailure

#: Members that live under the *declaration* directory rather than the bundle
#: root. Two shapes because `_schema/` and `_sections/` are directories and
#: `_tags.yaml` is a file; there is no third rule hiding here. Every other
#: member a caller names resolves against the bundle root.
DECLARATION_PREFIXES: tuple[str, ...] = ("_schema/", "_sections/")
DECLARATION_MEMBERS: tuple[str, ...] = ("_tags.yaml",)

#: The three files `plan_scaffold` writes. These are tier 2's to write, not
#: any tier-3 package's -- a `plan_install` call naming one of them is refused
#: rather than planned, so a tier-3 author knows to leave them to the
#: scaffold rather than shipping its own.
SCAFFOLD_MEMBERS: tuple[str, ...] = ("index.md", "log.md", "_tags.yaml")

#: The empty vocabulary the scaffold writes. A module constant rather than
#: package data: eight lines is not worth adding package-data machinery to
#: okf-ext for, and this is the one file in the scaffold whose content is a
#: fixed template rather than something derived from the bundle.
EMPTY_TAGS_YAML = """version: 1
tags: []
# Add tags here as the bundle needs them, e.g.:
#   - name: example-tag
#     description: What this tag means.
#     deprecated: false
# See okf_ext.tags.load_vocabulary for the full shape (name, description,
# deprecated, replaced_by).
"""


@dataclass(frozen=True, slots=True)
class PlannedFile:
    """One file this plan will create, and where it lands.

    `member` is bundle-relative posix, for reporting; `path` is the resolved
    target, under `declarations_dir` for a declaration member and under `root`
    for every other. Both are carried because the two differ the moment a
    caller relocates its declarations, and a refusal naming the resolved path
    is what tells a human which of two directories they actually edited.
    """

    member: str
    path: Path
    content: str


@dataclass(frozen=True, slots=True)
class ScaffoldPlan:
    """A preview of creating the files every bundle has, whoever installs into it.

    A value rather than a `dry_run=True` flag, following `plan_regenerate`,
    `plan_sections` and `plan_rename`. **Idempotence surfaces as an empty
    plan**: a scaffold over a bundle that already carries all three files
    contributes no `PlannedFile` at all, and `is_empty` is the signal.

    Unlike the plan types in `moves` and `proposals`, a refusal here does not
    invalidate the whole plan -- `ok` reports it and `apply` writes the rest.
    A refusal names one file the caller does not own; the others are still
    theirs, and refusing all of them because of one is the all-or-nothing
    behaviour this capability exists to narrow.
    """

    root: Path
    declarations_dir: Path
    writes: tuple[PlannedFile, ...]
    skipped: tuple[Skipped, ...]
    refusals: tuple[WriteFailure, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def is_empty(self) -> bool:
        return not self.writes


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """A preview of one package installing its own files into a bundle.

    Same five fields as `ScaffoldPlan` and deliberately a separate type: the
    two are produced by two different planners under two different check
    regimes, and a caller reporting "scaffolded 3, installed 14" needs to tell
    them apart by more than the order it happened to call them in. This is the
    call `proposals` makes for its own three same-shaped plan types.
    """

    root: Path
    declarations_dir: Path
    writes: tuple[PlannedFile, ...]
    skipped: tuple[Skipped, ...]
    refusals: tuple[WriteFailure, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def is_empty(self) -> bool:
        return not self.writes


#: Every plan `apply` accepts. A union rather than a base class: the two share
#: every field `apply` reads, so an ancestor would be inheritance for five
#: attributes and nothing else.
Plan = ScaffoldPlan | InstallPlan


#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this.
__all__ = [
    "DECLARATION_MEMBERS",
    "DECLARATION_PREFIXES",
    "EMPTY_TAGS_YAML",
    "SCAFFOLD_MEMBERS",
    "InstallPlan",
    "Plan",
    "PlannedFile",
    "ScaffoldPlan",
]
