"""The canonical design artifact preview shared by dispatch IO shells."""

from pathlib import Path

from work_tracker_okf.compose import stamp_for
from work_tracker_okf.items import WorkItem
from work_tracker_okf.paths import ArtifactRef
from work_tracker_okf.vocabulary import SPEC_SOURCE_ID


def missing_design_source(bundle_root: Path, item: WorkItem) -> tuple[ArtifactRef, str] | None:
    """An existing canonical design whose source stamp is missing, without writing."""
    if item.has_design_artifact:
        return None
    ref, title = stamp_for(bundle_root, item, SPEC_SOURCE_ID)
    if not ref.path(bundle_root).exists():
        return None
    return ref, title
