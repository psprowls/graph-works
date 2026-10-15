"""The workflow file `workflow_config` names, and what may live in it.

Pure functions over mappings and paths: nothing here reads a file. `manifest`
binds them to the workflow-file stores, `dispatch_config` to the dispatch read
and `init` to bootstrap. `workspace.yaml` carries no `workflow:` block any more;
its one say over the workflow file is the top-level pointer (ADR 2026-09-12 D1
keeps discovery manifest-owned; only the key's name and position changed).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from config_io import dotted

from gw_kernel.workspace.errors import WorkspaceError

#: The top-level manifest key naming the workflow file.
WORKFLOW_CONFIG_KEY = "workflow_config"
#: The file a new workspace's pointer names.
DEFAULT_WORKFLOW_FILE = "workflow.yaml"
#: The only top-level keys a workflow file may hold.
WORKFLOW_SECTIONS: frozenset[str] = frozenset({"workflow", "pipeline"})
#: Retired before the move; each keeps its own refusal in `dispatch_config`.
RETIRED_KEYS: tuple[str, ...] = (
    "workflow.pipeline",
    "workflow.auto_drive.models",
    "workflow.auto_drive.overrides",
    "workflow.auto_drive.permission_mode",
)
_LEGACY_POINTER = "workflow.dispatch_rules"


def _leaves(value: object, prefix: str) -> list[str]:
    """Dotted leaf keys beneath *prefix*; a non-mapping value is its own leaf."""
    if not isinstance(value, Mapping):
        return [prefix]
    return [leaf for key, child in value.items() for leaf in _leaves(child, f"{prefix}.{key}")]


def _retired(key: str) -> bool:
    return any(key == retired or key.startswith(retired + ".") for retired in RETIRED_KEYS)


def legacy_workflow_keys(layer: Mapping[str, object]) -> list[str]:
    """Every `workflow.*` leaf a manifest layer still carries, retired keys excluded, sorted."""
    if "workflow" not in layer:
        return []
    return sorted(key for key in _leaves(layer["workflow"], "workflow") if not _retired(key))


def check_legacy_workflow_keys(
    *,
    manifest_path: Path,
    base: Mapping[str, object],
    local_manifest_path: Path,
    overlay: Mapping[str, object],
) -> None:
    """Refuse a manifest layer that still carries a moved `workflow.*` key (D-002: reject only)."""
    for path, layer, home in (
        (manifest_path, base, "the workflow file named by workflow_config"),
        (local_manifest_path, overlay, "the .local sibling of the workflow file named by workflow_config"),
    ):
        keys = legacy_workflow_keys(layer)
        if _LEGACY_POINTER in keys:
            raise WorkspaceError(
                f"{path}: workflow.dispatch_rules is replaced by the top-level key workflow_config; "
                "set workflow_config to the same file, then move every other workflow.* key into that file"
            )
        if keys:
            raise WorkspaceError(
                f"{path}: workflow.* keys no longer live in {path.name}: {', '.join(keys)}; "
                f"move them under workflow: in {home}"
            )


def workflow_file_path(*, manifest_path: Path, base: Mapping[str, object], overlay: Mapping[str, object]) -> Path:
    """The shared workflow file the merged pointer names, resolved against the manifest's directory."""
    reference = dotted.get(dotted.merge(base, overlay), WORKFLOW_CONFIG_KEY)
    if reference is None:
        raise WorkspaceError(
            f"{manifest_path}: workflow_config is not set, so there is no workflow file; run `gw bootstrap`"
        )
    if not isinstance(reference, str) or not reference.strip():
        raise WorkspaceError(
            f"{manifest_path}: workflow_config must be a non-empty path naming the workflow file, got {reference!r}"
        )
    expanded = Path(reference).expanduser()
    return expanded if expanded.is_absolute() else manifest_path.parent / expanded


def missing_workflow_file(path: Path) -> WorkspaceError:
    """The one refusal for a pointer whose shared file is absent."""
    return WorkspaceError(f"{path}: the workflow file workflow_config names does not exist; run `gw bootstrap`")


def validate_workflow_document(path: Path, document: Mapping[str, object], *, keys: Iterable[str]) -> None:
    """Refuse an unknown top-level key, or an unknown or misshapen key under `workflow:`.

    *keys* is the catalog's set of concrete `workflow.*` keys. `pipeline:` is
    not looked at: a broken dispatch rule fails dispatch, not a `workflow.*` read.
    """
    unknown = [str(key) for key in document if key not in WORKFLOW_SECTIONS]
    if unknown:
        raise WorkspaceError(
            f"{path}: unknown top-level key {unknown[0]!r}; a workflow file holds only workflow: and pipeline:"
        )
    if "workflow" not in document:
        return
    block = document["workflow"]
    if not isinstance(block, Mapping):
        raise WorkspaceError(f"{path}: workflow must be a mapping, got {block!r}")
    allowed = frozenset(keys)
    branches = {".".join(key.split(".")[:depth]) for key in allowed for depth in range(2, len(key.split(".")))}
    for leaf in _leaves(block, "workflow"):
        if leaf in allowed:
            continue
        if leaf in branches:
            raise WorkspaceError(f"{path}: {leaf} must be a mapping")
        raise WorkspaceError(f"{path}: unknown key {leaf}; expected one of {sorted(allowed)}")
