"""Pure path grammar and artifact naming for path-native work items."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

_BASENAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ARTIFACT_RE = re.compile(r"[0-9]{2}-(?P<source_id>[a-z0-9]+(?:-[a-z0-9]+)*)\.[a-z0-9]+")
_RESERVED_BASENAMES = frozenset({"children", "index", "references"})

MANAGED_ARTIFACTS: Mapping[str, str] = MappingProxyType(
    {
        "decisions": "00-decisions.md",
        "design": "01-design.md",
        "design-transcript": "01-design-transcript.jsonl",
        "plan": "02-plan.md",
        "plan-transcript": "02-plan-transcript.jsonl",
        "execute-results": "03-execute-results.md",
        "execute-coverage": "03-execute-coverage.md",
        "execute-transcript": "03-execute-transcript.jsonl",
        "finish-results": "04-finish-results.md",
        "finish-receipt": "04-finish-receipt.md",
        "finish-transcript": "04-finish-transcript.jsonl",
    }
)

#: The managed-artifact ordinal each parkable phase's own artifacts carry
#: (``01-design.md``, ``02-plan.md``, ``03-execute-*``, ``04-finish-*``).
PHASE_ORDINALS: Mapping[str, str] = MappingProxyType({"design": "01", "plan": "02", "execute": "03", "finish": "04"})

_DECISION_ID_RE = re.compile(r"D-\d+")


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A bundle-relative member plus its optional source identifier."""

    rel: str
    source_id: str | None = None

    @property
    def resource(self) -> str:
        return f"/{self.rel}"

    def path(self, root: Path) -> Path:
        return root / self.rel


@dataclass(frozen=True, slots=True)
class ItemLocation:
    """The parsed, extensionless identity of an item member."""

    path: str
    page: str
    basename: str
    lane: str
    parent_path: str | None
    ancestor_paths: tuple[str, ...]
    archived: bool


def _is_basename(value: str) -> bool:
    return value not in _RESERVED_BASENAMES and _BASENAME_RE.fullmatch(value) is not None


def parse_item_path(value: str) -> ItemLocation | None:
    """Parse an extensionless, bundle-relative item path without reading disk.

    An `_archive` lane may appear at any depth and does not end the path: an
    archived subtree keeps its internal shape, which is what lets the spec
    require a `children/index.md` and `children/_archive/index.md` "including
    after its subtree is archived". `archived` is therefore sticky -- once any
    lane on the way down is an archive lane, every item below it is archived by
    ancestry.
    """
    parts = value.split("/")
    if not parts or parts[0] != "work":
        return None

    archived = len(parts) > 1 and parts[1] == "_archive"
    basename_index = 2 if archived else 1
    if len(parts) <= basename_index or not _is_basename(parts[basename_index]):
        return None

    item_paths = ["/".join(parts[: basename_index + 1])]
    lane = "work/_archive" if archived else "work"
    cursor = basename_index + 1
    while cursor < len(parts):
        if parts[cursor] != "children":
            return None
        cursor += 1
        lane = f"{item_paths[-1]}/children"
        if cursor < len(parts) and parts[cursor] == "_archive":
            archived = True
            lane = f"{lane}/_archive"
            cursor += 1
        if cursor >= len(parts) or not _is_basename(parts[cursor]):
            return None
        item_paths.append(f"{lane}/{parts[cursor]}")
        cursor += 1

    path = item_paths[-1]
    return ItemLocation(
        path=path,
        page=f"{path}.md",
        basename=parts[-1],
        lane=lane,
        parent_path=item_paths[-2] if len(item_paths) > 1 else None,
        ancestor_paths=tuple(item_paths[:-1]),
        archived=archived,
    )


def child_lane(item_path: str, *, archived: bool = False) -> str:
    """The active or archived child lane owned by *item_path*."""
    suffix = "/_archive" if archived else ""
    return f"{item_path}/children{suffix}"


def item_page(item_path: str) -> ArtifactRef:
    """The markdown page for an extensionless item identity."""
    return ArtifactRef(rel=f"{item_path}.md")


def owned_dir(item_path: str) -> ArtifactRef:
    """The directory an item owns beside its page."""
    return ArtifactRef(rel=item_path)


def references_dir(item_path: str) -> ArtifactRef:
    """The references directory owned by an item."""
    return ArtifactRef(rel=f"{owned_dir(item_path).rel}/references")


def source_id_for_filename(filename: str) -> str:
    """Remove an artifact's ordinal from its filename stem to obtain its id."""
    match = _ARTIFACT_RE.fullmatch(filename)
    if match is None:
        raise ValueError(f"managed artifact filename must begin with NN-: {filename!r}")
    return match.group("source_id")


def artifact_ref(item_path: str, filename: str) -> ArtifactRef:
    """Reference an item-owned artifact using its filename-derived source id."""
    return ArtifactRef(
        rel=f"{references_dir(item_path).rel}/{filename}",
        source_id=source_id_for_filename(filename),
    )


def checkpoint_ref(item_path: str, phase: str, decision_id: str) -> ArtifactRef:
    """Reference a park checkpoint named by phase and decision id.

    This is built directly rather than through :func:`artifact_ref`: the
    uppercase ``D-`` is not a kebab-case source id, and checkpoints are not
    stamped into ``sources[]``. The ledger entry's ``checkpoint:`` is their
    pointer.
    """
    ordinal = PHASE_ORDINALS.get(phase)
    if ordinal is None:
        raise ValueError(f"no checkpoint for phase {phase!r}; expected one of {sorted(PHASE_ORDINALS)}")
    if _DECISION_ID_RE.fullmatch(decision_id) is None:
        raise ValueError(f"malformed decision id {decision_id!r}; expected a form like 'D-014'")
    return ArtifactRef(rel=f"{references_dir(item_path).rel}/{ordinal}-{phase}-checkpoint-{decision_id}.md")


__all__ = [
    "MANAGED_ARTIFACTS",
    "PHASE_ORDINALS",
    "ArtifactRef",
    "ItemLocation",
    "artifact_ref",
    "checkpoint_ref",
    "child_lane",
    "item_page",
    "owned_dir",
    "parse_item_path",
    "references_dir",
    "source_id_for_filename",
]
