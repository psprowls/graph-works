"""Plan and apply migrated child-design-spec adoption.

Callers supply the already-loaded bundle and item projection; this module does
not discover a workspace or invoke Git.  ``plan_adoption`` is the dry-run
entrypoint: it classifies only donor drafts in an epic's migrated
``references/child-specs/`` directory, and ``apply_adoption`` performs the
explicit, no-overwrite writes in the resulting immutable plan.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from okf_io import Bundle, Source, load
from okf_io.links import resolve_reference

from work_tracker_okf.items import WorkItem
from work_tracker_okf.paths import ArtifactRef, artifact_path, references_dir
from work_tracker_okf.sources import upsert
from work_tracker_okf.vocabulary import SPEC_SOURCE_ID

AdoptionRefusal = Literal["unknown-epic", "not-an-epic"]

_H1_RE = re.compile(r"^#[ \t]+(\S.*?)[ \t]*$")


@dataclass(frozen=True, slots=True)
class ChildSpecMove:
    """One child design-spec copy and/or canonical source registration."""

    child_slug: str
    draft: Path | None
    destination: Path
    child_page: Path
    source_ref: ArtifactRef
    source_title: str
    register_source: bool


@dataclass(frozen=True, slots=True)
class AmbiguousDraft:
    """A donor draft whose suffix identifies more than one direct child."""

    draft: Path
    candidate_slugs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdoptionPlan:
    """The read-only adoption decision for one epic."""

    epic_slug: str
    adopted: tuple[ChildSpecMove, ...]
    orphaned: tuple[Path, ...]
    unseeded: tuple[str, ...]
    ambiguous: tuple[AmbiguousDraft, ...]
    warnings: tuple[str, ...]
    refusal: AdoptionRefusal | None = None


@dataclass(frozen=True, slots=True)
class AdoptionApplication:
    """Files moved and child pages whose canonical source was registered."""

    moved: tuple[Path, ...] = ()
    registered: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _title_for(path: Path, fallback: str) -> str:
    """Return *path*'s first H1, or *fallback* when it has none."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return fallback
    for line in text.splitlines():
        match = _H1_RE.match(line)
        if match is not None:
            return match.group(1)
    return fallback


def _is_readable_file(path: Path) -> bool:
    """Whether *path* is an artifact we can safely adopt or register."""
    if not path.is_file():
        return False
    try:
        path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return True


def _source_exists(root: Path, item: WorkItem) -> bool:
    """Whether an authored noncanonical design-spec resource remains usable."""
    source_id = item.path.removesuffix(".md")
    for source in item.sources:
        if source.id != SPEC_SOURCE_ID or not source.resource:
            continue
        resolved = resolve_reference(source.resource, source_id=source_id)
        if resolved is not None and (root / resolved).is_file():
            return True
    return False


def _canonical_source_exists(item: WorkItem, reference: ArtifactRef, destination: Path) -> bool:
    """Whether the expected artifact is both present and stamped canonically."""
    return _is_readable_file(destination) and any(
        source.id == SPEC_SOURCE_ID and source.resource == reference.resource for source in item.sources
    )


def _has_authored_noncanonical_source(sources: Sequence[Source], reference: ArtifactRef) -> bool:
    """Whether a child has a design-spec source we must never repoint."""
    return any(source.id == SPEC_SOURCE_ID and source.resource != reference.resource for source in sources)


def _move_for(item: WorkItem, root: Path, draft: Path | None) -> ChildSpecMove:
    reference = artifact_path(item.slug, "design", "spec", archived=item.archived)
    destination = reference.path(root)
    fallback = f"Design spec — {item.title}"
    title_path = draft if draft is not None else destination
    return ChildSpecMove(
        child_slug=item.slug,
        draft=draft,
        destination=destination,
        child_page=root / item.path,
        source_ref=reference,
        source_title=_title_for(title_path, fallback),
        register_source=True,
    )


def _refusal(epic_slug: str, reason: AdoptionRefusal) -> AdoptionPlan:
    return AdoptionPlan(
        epic_slug=epic_slug,
        adopted=(),
        orphaned=(),
        unseeded=(),
        ambiguous=(),
        warnings=(),
        refusal=reason,
    )


def plan_adoption(bundle: Bundle, items: Sequence[WorkItem], epic_slug: str) -> AdoptionPlan:
    """Classify migrated donor drafts and plan their safe adoption.

    Only ``work/<epic>/references/child-specs/*.md`` is a donor directory.
    Direct children are considered in both active and archive lanes, while a
    valid existing ``design-spec`` source is treated as authored and seeded.
    """
    epic = next((item for item in items if item.slug == epic_slug), None)
    if epic is None:
        return _refusal(epic_slug, "unknown-epic")
    if epic.type != "Epic":
        return _refusal(epic_slug, "not-an-epic")

    children = tuple(
        sorted((item for item in items if item.parent == epic_slug), key=lambda item: (item.slug, item.archived))
    )
    donor_dir = references_dir(epic_slug).path(bundle.root) / "child-specs"
    drafts = tuple(sorted(donor_dir.glob("*.md"))) if donor_dir.is_dir() else ()

    adopted: list[ChildSpecMove] = []
    orphaned: list[Path] = []
    ambiguous: list[AmbiguousDraft] = []
    warnings: list[str] = []
    draft_for_child: dict[tuple[str, bool], Path] = {}

    for draft in drafts:
        candidates = tuple(child for child in children if child.slug.endswith(f"-{draft.stem}"))
        if not candidates:
            orphaned.append(draft)
            continue
        if len(candidates) != 1:
            ambiguous.append(AmbiguousDraft(draft=draft, candidate_slugs=tuple(child.slug for child in candidates)))
            continue
        child = candidates[0]
        key = (child.slug, child.archived)
        if key in draft_for_child:
            warnings.append(
                f"{draft}: another migrated draft already targets {child.slug}; leaving this draft untouched"
            )
            continue
        draft_for_child[key] = draft

    unseeded: list[str] = []
    for child in children:
        key = (child.slug, child.archived)
        matching_draft = draft_for_child.get(key)
        reference = artifact_path(child.slug, "design", "spec", archived=child.archived)
        destination = reference.path(bundle.root)
        if _canonical_source_exists(child, reference, destination):
            if matching_draft is not None:
                warnings.append(
                    f"{matching_draft}: destination {destination} remains occupied; leaving the donor draft untouched"
                )
            continue
        if _has_authored_noncanonical_source(child.sources, reference):
            if _source_exists(bundle.root, child):
                continue
            warnings.append(
                f"{child.slug}: authored noncanonical design-spec source has no existing artifact; leaving it untouched"
            )
            unseeded.append(child.slug)
            continue
        if destination.exists():
            if not _is_readable_file(destination):
                if matching_draft is None:
                    warnings.append(
                        f"{child.slug}: destination {destination} is not a readable file; leaving it untouched"
                    )
                else:
                    warnings.append(
                        f"{matching_draft}: destination {destination} is not a readable file; "
                        "leaving it and the donor untouched"
                    )
                unseeded.append(child.slug)
                continue
            adopted.append(_move_for(child, bundle.root, None))
            if matching_draft is not None:
                warnings.append(
                    f"{matching_draft}: destination {destination} already exists; leaving the donor draft untouched"
                )
            continue
        if matching_draft is not None:
            adopted.append(_move_for(child, bundle.root, matching_draft))
            continue
        unseeded.append(child.slug)

    return AdoptionPlan(
        epic_slug=epic_slug,
        adopted=tuple(adopted),
        orphaned=tuple(orphaned),
        unseeded=tuple(unseeded),
        ambiguous=tuple(ambiguous),
        warnings=tuple(warnings),
    )


def apply_adoption(plan: AdoptionPlan) -> AdoptionApplication:
    """Apply a non-refused adoption plan without replacing any destination."""
    if plan.refusal is not None:
        return AdoptionApplication()

    moved: list[Path] = []
    registered: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []
    for move in plan.adopted:
        document = load(move.child_page)
        if _has_authored_noncanonical_source(document.fm.sources, move.source_ref):
            skipped.append(move.child_slug)
            warnings.append(
                f"{move.child_slug}: authored noncanonical design-spec source appeared after planning; skipped adoption"
            )
            continue
        if move.draft is None and not _is_readable_file(move.destination):
            skipped.append(move.child_slug)
            warnings.append(f"{move.child_slug}: destination is no longer a readable artifact; skipped registration")
            continue
        if move.draft is not None:
            donor = move.draft.read_bytes()
            move.destination.parent.mkdir(parents=True, exist_ok=True)
            stream = move.destination.open("xb")
            try:
                with stream:
                    written = stream.write(donor)
                    if written != len(donor):
                        raise OSError(f"short write to {move.destination}: wrote {written} of {len(donor)} bytes")
            except BaseException:
                move.destination.unlink(missing_ok=True)
                raise
            move.draft.unlink()
            moved.append(move.destination)
        if move.register_source and upsert(document, move.source_ref, title=move.source_title):
            document.save()
            registered.append(move.child_slug)
    return AdoptionApplication(
        moved=tuple(moved),
        registered=tuple(registered),
        skipped=tuple(skipped),
        warnings=tuple(warnings),
    )


__all__ = [
    "AdoptionApplication",
    "AdoptionPlan",
    "AdoptionRefusal",
    "AmbiguousDraft",
    "ChildSpecMove",
    "apply_adoption",
    "plan_adoption",
]
