"""Pure lexical classification of filesystem changes into change events."""

from __future__ import annotations

from pathlib import Path, PurePath

from config_io import PROJECTION_FILENAME
from work_tracker_okf.paths import MANAGED_ARTIFACTS, parse_item_path

from graph_works_core.events.model import Change, ChangeEvent, EventKind
from graph_works_core.workspace.layout import WorkspaceLayout

_LOG = "log.md"
_WORK = "work"
_MARKDOWN = ".md"
_OWNED_SEGMENTS = frozenset({"references", "children"})
_DECISIONS_TAIL = ("references", MANAGED_ARTIFACTS["decisions"])


def classify(
    layout: WorkspaceLayout,
    path: PurePath,
    change: Change,
    *,
    dispatch_documents: tuple[Path, Path] | None = None,
) -> ChangeEvent | None:
    """Classify *path*, rejecting literal backslashes from portable identities."""
    if not path.is_absolute():
        return None
    token = _config_token(layout, path, dispatch_documents)
    if token is not None:
        return ChangeEvent(kind=EventKind.CONFIG, path=token, member=None, change=change)
    try:
        parts = path.relative_to(layout.bundle_dir).parts
    except ValueError:
        return None
    if not parts or any("\\" in part for part in parts) or _is_noise(parts):
        return None
    member = "/".join(parts)
    if parts == (_LOG,):
        return ChangeEvent(kind=EventKind.LOG, path=_LOG, member=member, change=change)
    if parts[0] == _WORK:
        event = _work_event(parts, member, change)
        if event is not None:
            return event
    if member.endswith(_MARKDOWN):
        return ChangeEvent(kind=EventKind.PAGE, path=member, member=member, change=change)
    return None


def _config_token(layout: WorkspaceLayout, path: PurePath, dispatch_documents: tuple[Path, Path] | None) -> str | None:
    candidates: list[tuple[PurePath, str]] = [
        (layout.cache_dir / PROJECTION_FILENAME, "projection"),
        (layout.manifest_path, "manifest"),
        (layout.local_manifest_path, "manifest-local"),
    ]
    if dispatch_documents is not None:
        shared, local = dispatch_documents
        candidates += [(shared, "dispatch"), (local, "dispatch-local")]
    return next((token for candidate, token in candidates if path == candidate), None)


def _is_noise(parts: tuple[str, ...]) -> bool:
    return any(part.startswith(".") for part in parts) or parts[-1].endswith(".lock")


def _work_event(parts: tuple[str, ...], member: str, change: Change) -> ChangeEvent | None:
    """An item page, or a member of the deepest item's owned directory."""
    if member.endswith(_MARKDOWN):
        item = parse_item_path(member[: -len(_MARKDOWN)])
        if item is not None:
            return ChangeEvent(kind=EventKind.WORK_ITEM, path=item.path, member=member, change=change)
    for index in range(len(parts) - 1, 1, -1):
        if parts[index] not in _OWNED_SEGMENTS:
            continue
        owner = parse_item_path("/".join(parts[:index]))
        if owner is None:
            continue
        kind = EventKind.DECISIONS if parts[index:] == _DECISIONS_TAIL else EventKind.WORK_ITEM
        return ChangeEvent(kind=kind, path=owner.path, member=member, change=change)
    return None


__all__ = ["classify"]
