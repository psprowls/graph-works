"""Tolerant, path-keyed work-item projection."""

from __future__ import annotations

from dataclasses import dataclass, replace

from okf_ext.schemas import DEFAULT_IGNORE as _SCHEMA_IGNORE
from okf_ext.schemas import SchemaSet, declared_directories
from okf_ext.shape import DEFAULT_IGNORE as _SECTIONS_IGNORE
from okf_io import Bundle, Document, Source

from work_tracker_okf.dependencies import DependencyEdge, DependencyIssue, parse_dependencies
from work_tracker_okf.paths import ItemLocation, parse_item_path
from work_tracker_okf.vocabulary import PLAN_SOURCE_ID, SPEC_SOURCE_ID, TYPES

WORK_DIR = "work"
ARCHIVE_DIR = "work/_archive"
IGNORE: tuple[str, ...] = ("*/references/*", "*/.DS_Store", *_SCHEMA_IGNORE, *_SECTIONS_IGNORE)
ARCHIVE_IGNORE: tuple[str, ...] = (*_SCHEMA_IGNORE, *_SECTIONS_IGNORE)


def placement_directories(schema_set: SchemaSet) -> dict[str, str]:
    """`declared_directories(schema_set)`, narrowed to this lane's own `TYPES`.

    The map `compose.rule_set` hands `okf_ext.placement.placement_rule`, and
    the one place the narrowing lives -- beside the `WORK_DIR` it must stay
    consistent with, which is what the drift test in
    `tests/test_placement_adoption.py` anchors on.

    This must remain an **allow**-list. A composed workspace points every lane
    installer at one shared `schema/`, which also contains the seven code-wiki
    types. Their repository/ecosystem-aware paths are owned and validated by
    `code_wiki_okf.placement`; passing every declared directory to this generic
    static-prefix rule would duplicate that authority and misclassify canonical
    pages.
    """
    return {
        type_name: directory for type_name, directory in declared_directories(schema_set).items() if type_name in TYPES
    }


@dataclass(frozen=True, slots=True)
class WorkItem:
    """Tolerant item view, retaining lossy phase/effort projection failures.

    `invalid_optional_fields` names authored phase/effort values that were
    neither null nor nonempty text. Their projected values remain `None`
    for existing readers; placement must distinguish them from absence.
    This is read metadata, never a frontmatter field. Direct constructors
    may omit it; vocabulary validation still checks their supplied values.
    """

    path: str
    page_path: str
    basename: str
    archived: bool
    type: str
    title: str
    description: str
    status: str
    work_status: str
    phase: str | None
    effort: str | None
    blast_radius: str | None
    target: str | None
    opened: str
    updated: str
    affects: tuple[str, ...]
    parent_path: str | None
    ancestor_paths: tuple[str, ...]
    child_paths: tuple[str, ...]
    dependency_edges: tuple[DependencyEdge, ...]
    dependency_issues: tuple[DependencyIssue, ...]
    owner: str | None
    resolved_in: str | None
    worktree: str | None
    branch: str | None
    superseded_by: str | None
    tags: tuple[str, ...]
    sources: tuple[Source, ...]
    has_design_artifact: bool
    has_plan_artifact: bool
    version: str | None
    target_date: str | None
    released_at: str | None
    invalid_optional_fields: tuple[str, ...] = ()


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list):
        return ()
    return tuple(entry for entry in value if isinstance(entry, str))


def _project(location: ItemLocation, document: Document) -> WorkItem:
    data = document.fm_data(dates="iso")
    fm = document.fm
    source_ids = {source.id for source in fm.sources if source.id is not None}
    dependencies = parse_dependencies(data.get("depends_on"))
    return WorkItem(
        path=location.path,
        page_path=location.page,
        basename=location.basename,
        archived=location.archived,
        type=_text(data.get("type")),
        title=_text(data.get("title")),
        description=_text(data.get("description")),
        status=_text(data.get("status")),
        work_status=_text(data.get("work_status")),
        phase=_optional_text(data.get("phase")),
        effort=_optional_text(data.get("effort")),
        blast_radius=_optional_text(data.get("blast_radius")),
        target=_optional_text(data.get("target")),
        opened=_text(data.get("opened")),
        updated=_text(data.get("updated")),
        affects=_text_tuple(data.get("affects")),
        parent_path=location.parent_path,
        ancestor_paths=location.ancestor_paths,
        child_paths=(),
        dependency_edges=dependencies.edges,
        dependency_issues=dependencies.issues,
        owner=_optional_text(data.get("owner")),
        resolved_in=_optional_text(data.get("resolved_in")),
        worktree=_optional_text(data.get("worktree")),
        branch=_optional_text(data.get("branch")),
        superseded_by=_optional_text(data.get("superseded_by")),
        tags=fm.tags,
        sources=fm.sources,
        has_design_artifact=SPEC_SOURCE_ID in source_ids,
        has_plan_artifact=PLAN_SOURCE_ID in source_ids,
        version=_optional_text(data.get("version")),
        target_date=_optional_text(data.get("target_date")),
        released_at=_optional_text(data.get("released_at")),
        invalid_optional_fields=tuple(
            field
            for field in ("phase", "effort")
            if data.get(field) is not None and _optional_text(data[field]) is None
        ),
    )


def item_index(items: tuple[WorkItem, ...] | list[WorkItem]) -> dict[str, WorkItem]:
    """Index a projection by permanent extensionless bundle path."""
    return {item.path: item for item in items}


def load_items(bundle: Bundle) -> tuple[WorkItem, ...]:
    """Project every concept whose extensionless id passes ``parse_item_path``."""
    projected: list[WorkItem] = []
    for concept_id, document in bundle.concepts.items():
        location = parse_item_path(concept_id)
        if location is not None:
            projected.append(_project(location, document))

    children: dict[str, list[str]] = {}
    for item in projected:
        if item.parent_path is not None:
            children.setdefault(item.parent_path, []).append(item.path)
    return tuple(
        replace(item, child_paths=tuple(sorted(children.get(item.path, ()))))
        for item in sorted(projected, key=lambda item: item.path)
    )


def unreadable_detail(bundle: Bundle, path: str) -> str | None:
    """Why *path*'s page could not be read, or `None` if that is not why it is missing."""
    return bundle.unreadable.get(f"{path}.md")


__all__ = [
    "ARCHIVE_DIR",
    "ARCHIVE_IGNORE",
    "IGNORE",
    "WORK_DIR",
    "WorkItem",
    "item_index",
    "load_items",
    "placement_directories",
    "unreadable_detail",
]
