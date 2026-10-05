"""Tolerant, path-keyed work-item projection."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from okf_ext.schemas import DEFAULT_IGNORE as _SCHEMA_IGNORE
from okf_ext.schemas import SchemaSet, declared_directories
from okf_ext.shape import DEFAULT_IGNORE as _SECTIONS_IGNORE
from okf_io import Bundle, Source

from work_tracker_okf.dependencies import DependencyEdge, DependencyIssue, parse_dependencies
from work_tracker_okf.obligations import Obligation, parse_obligations
from work_tracker_okf.paths import ItemLocation
from work_tracker_okf.returns import KEY as RETURN_KEY
from work_tracker_okf.returns import ExecuteReturn, parse_execute_return
from work_tracker_okf.vocabulary import PLAN_SOURCE_ID, SPEC_SOURCE_ID, TYPES

if TYPE_CHECKING:
    from work_tracker_okf.snapshot import WorkSnapshot

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


#: A full lowercase commit object ID (SHA-1 or SHA-256). Placement baselines
#: must be exact: an abbreviated or symbolic ref is not a recorded fact.
COMMIT_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def is_commit_oid(value: object) -> bool:
    return isinstance(value, str) and COMMIT_OID.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class SpecBaseline:
    """Commits a design spec was written against, stamped at design exit.

    Either repository may be absent. Re-stamping overwrites both; the
    spec's reconciled headings preserve the history.
    """

    code: str | None = None
    workspace: str | None = None


_SPEC_BASELINE_KEYS = frozenset({"code", "workspace"})


def spec_baseline_of(value: object) -> tuple[SpecBaseline | None, bool]:
    """Return (projection, malformed); one bad key voids the field. Never raises."""
    if value is None:
        return None, False
    if not isinstance(value, dict) or not set(value) <= _SPEC_BASELINE_KEYS:
        return None, True
    if any(not is_commit_oid(entry) for entry in value.values()):
        return None, True
    return SpecBaseline(code=value.get("code"), workspace=value.get("workspace")), False


@dataclass(frozen=True, slots=True)
class Stamp:
    """One `repo_stamps` entry: where an item runs in a repository other than its own,
    and the commit that placement's work started from (`None` for a stamp that predates baselines)."""

    worktree: str
    branch: str
    start_sha: str | None = None


@dataclass(frozen=True, slots=True)
class WorkItem:
    """Tolerant item view, retaining lossy phase/effort projection failures.

    `invalid_optional_fields` names authored phase/effort values that were
    neither null nor nonempty text. Their projected values remain `None`
    for existing readers; placement must distinguish them from absence.
    This is read metadata, never a frontmatter field. Direct constructors
    may omit it; vocabulary validation still checks their supplied values.

    `invalid_optional_fields` also names a malformed `repo` (anything but
    null or non-empty text) and a malformed `repo_stamps` (a non-mapping,
    or any entry that is not exactly `{worktree, branch}` of non-empty
    text). The malformed parts project as absent, and well-formed
    `repo_stamps` entries are kept.

    `start_sha` is the scalar placement's execute baseline; a value that is
    not a full lowercase commit OID projects as `None` and names `start_sha`
    in `invalid_optional_fields`.

    `spec_baseline` records the design baselines; malformed values project as
    `None` and name `spec_baseline` in `invalid_optional_fields`.

    `finish_obligations` projects the well-formed `{text, origin, recorded}`
    entries in stored order (`work_tracker_okf.obligations`); a non-list or any
    malformed entry names `finish_obligations` in `invalid_optional_fields`.

    `execute_return` records returned execution scope. Malformed values,
    including explicit null, project as `None` and name `execute_return` in
    `invalid_optional_fields`, so readers can distinguish them from absence.
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
    repo: str | None = None
    repo_stamps: Mapping[str, Stamp] = MappingProxyType({})
    start_sha: str | None = None
    finish_obligations: tuple[Obligation, ...] = ()
    spec_baseline: SpecBaseline | None = None
    execute_return: ExecuteReturn | None = None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, list):
        return ()
    return tuple(entry for entry in value if isinstance(entry, str))


def _repo_stamps(value: object) -> tuple[Mapping[str, Stamp], bool]:
    """`(well-formed entries, whether anything was malformed)`. Never raises."""
    if value is None:
        return MappingProxyType({}), False
    if not isinstance(value, dict):
        return MappingProxyType({}), True
    stamps: dict[str, Stamp] = {}
    malformed = False
    for name, entry in value.items():
        if (
            isinstance(name, str)
            and name
            and isinstance(entry, dict)
            and set(entry) in ({"worktree", "branch"}, {"worktree", "branch", "start_sha"})
            and _optional_text(entry["worktree"]) is not None
            and _optional_text(entry["branch"]) is not None
            and ("start_sha" not in entry or is_commit_oid(entry["start_sha"]))
        ):
            stamps[name] = Stamp(entry["worktree"], entry["branch"], entry.get("start_sha"))
        else:
            malformed = True
    return MappingProxyType(stamps), malformed


def _project(
    location: ItemLocation,
    data: Mapping[str, Any],
    *,
    tags: tuple[str, ...],
    sources: tuple[Source, ...],
) -> WorkItem:
    """Project one item from its plain `fm_data(dates="iso")` mapping (D-008).

    *tags* and *sources* come from the typed frontmatter: the bundle path
    passes `document.fm`'s (which carries the v0.1 body-citation fallback),
    the row path passes `build_frontmatter(data)`'s (which cannot).
    Row reconstruction also sees native YAML dates as ISO strings in tags,
    Source scalar fields and Source.extra. Plain-data conversion recursively
    stringifies mapping keys in Source.extra and collapses collisions with
    the last entry winning, while bundle extras retain nested native keys.
    `WorkSnapshot.from_rows` documents these accepted differences from the
    document's typed values; row reconstruction cannot recover lost entries.
    """
    source_ids = {source.id for source in sources if source.id is not None}
    dependencies = parse_dependencies(data.get("depends_on"))
    repo_stamps, stamps_malformed = _repo_stamps(data.get("repo_stamps"))
    spec_baseline, baseline_malformed = spec_baseline_of(data.get("spec_baseline"))
    obligations, obligations_malformed = parse_obligations(data.get("finish_obligations"))
    if "finish_obligations" in data and data["finish_obligations"] is None:
        obligations_malformed = True
    execute_return, return_malformed = parse_execute_return(data.get(RETURN_KEY))
    if RETURN_KEY in data and data[RETURN_KEY] is None:
        return_malformed = True
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
        tags=tags,
        sources=sources,
        has_design_artifact=SPEC_SOURCE_ID in source_ids,
        has_plan_artifact=PLAN_SOURCE_ID in source_ids,
        version=_optional_text(data.get("version")),
        target_date=_optional_text(data.get("target_date")),
        released_at=_optional_text(data.get("released_at")),
        repo=_optional_text(data.get("repo")),
        repo_stamps=repo_stamps,
        finish_obligations=obligations,
        spec_baseline=spec_baseline,
        execute_return=execute_return,
        start_sha=data.get("start_sha") if is_commit_oid(data.get("start_sha")) else None,
        invalid_optional_fields=(
            *(
                field
                for field in ("phase", "effort", "repo")
                if data.get(field) is not None and _optional_text(data[field]) is None
            ),
            *(("spec_baseline",) if baseline_malformed else ()),
            *(("repo_stamps",) if stamps_malformed else ()),
            *(("finish_obligations",) if obligations_malformed else ()),
            *((RETURN_KEY,) if return_malformed else ()),
            *(("start_sha",) if data.get("start_sha") is not None and not is_commit_oid(data.get("start_sha")) else ()),
        ),
    )


def item_index(items: Iterable[WorkItem]) -> dict[str, WorkItem]:
    """Index a projection by permanent extensionless bundle path. Always a fresh dict."""
    from work_tracker_okf.snapshot import WorkSnapshot

    if isinstance(items, WorkSnapshot):
        return dict(items.by_path)
    return {item.path: item for item in items}


def load_items(bundle: Bundle) -> WorkSnapshot:
    """Project every concept whose extensionless id passes ``parse_item_path``."""
    from work_tracker_okf.snapshot import WorkSnapshot

    return WorkSnapshot.from_bundle(bundle)


def unreadable_detail(bundle: Bundle, path: str) -> str | None:
    """Why *path*'s page could not be read, or `None` if that is not why it is missing."""
    return bundle.unreadable.get(f"{path}.md")


__all__ = [
    "ARCHIVE_DIR",
    "ARCHIVE_IGNORE",
    "COMMIT_OID",
    "IGNORE",
    "WORK_DIR",
    "SpecBaseline",
    "Stamp",
    "WorkItem",
    "is_commit_oid",
    "item_index",
    "load_items",
    "placement_directories",
    "spec_baseline_of",
    "unreadable_detail",
]
