"""The canonical design artifact preview shared by dispatch IO shells."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from work_tracker_okf.compose import ArtifactBody, stage_artifact_for_source, stage_artifact_ref, stamp_for
from work_tracker_okf.items import WorkItem
from work_tracker_okf.paths import ArtifactRef
from work_tracker_okf.pipeline import PipelineDefinition
from work_tracker_okf.snapshot import WorkSnapshot
from work_tracker_okf.vocabulary import SPEC_SOURCE_ID


def missing_design_source(
    bundle_root: Path,
    item: WorkItem,
    *,
    definition: PipelineDefinition,
    body_of: ArtifactBody | None = None,
) -> tuple[ArtifactRef, str] | None:
    """An existing canonical design whose source stamp is missing, without writing.

    *body_of* titles the design from an already-verified body (the queue's read
    session); without it the title is read from the file.
    """
    if item.has_design_artifact:
        return None
    ref = stage_artifact_ref(item.path, stage_artifact_for_source(definition=definition, source_id=SPEC_SOURCE_ID))
    if not ref.path(bundle_root).exists():
        return None
    return stamp_for(bundle_root, item, SPEC_SOURCE_ID, definition=definition, body_of=body_of)


def routing_items(
    bundle_root: Path,
    items: Sequence[WorkItem],
    *,
    definition: PipelineDefinition,
    body_of: ArtifactBody | None = None,
) -> WorkSnapshot:
    """Recognize unstamped canonical designs for routing without repairing pages."""
    routed = tuple(
        replace(item, has_design_artifact=True)
        if missing_design_source(bundle_root, item, definition=definition, body_of=body_of) is not None
        else item
        for item in items
    )
    if isinstance(items, WorkSnapshot) and all(new is old for new, old in zip(routed, items, strict=True)):
        return items
    return WorkSnapshot(routed)
