"""The item view: one projection, replacing three independent walks.

`work_io` walked `work/*.md` three times -- `lifecycle_lint.load_items`,
`sidecar.build_sidecar`, `archive.plan_archive` -- with three separately
maintained opinions about which files count and what an unparseable page means.
okf-io walks once, and this is what every consumer reads off that walk:
routing, hierarchy, every lane rule, the rollup, resume selection and archive
eligibility.

**Nothing here raises.** A field of the wrong shape lands at its empty value
and the page still projects. okf-io's tolerance rule, inherited: the *rules*
report malformed content, the *reader* does not refuse it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from okf_ext.schemas import DEFAULT_IGNORE as _SCHEMA_IGNORE
from okf_ext.shape import DEFAULT_IGNORE as _SECTIONS_IGNORE
from okf_io import Bundle, Document, Source

from work_tracker_okf.dependencies import DependencyEdge, DependencyIssue, parse_dependencies
from work_tracker_okf.vocabulary import PLAN_SOURCE_ID, SPEC_SOURCE_ID

#: The lane's two directories, bundle-relative posix. An item page sits at
#: exactly one segment under either.
WORK_DIR = "work"
ARCHIVE_DIR = "work/_archive"

#: The `ignore=` recipe for loading a vault carrying this lane.
#:
#: The **composed** value, not the lane pattern alone (C1-D): every consumer
#: that loads this vault needs all three, and three copies of the composition
#: is how two consumers end up loading the same bundle differently. The cost is
#: real and worth recording -- this package's exported contract now moves if
#: either tier-2 default does -- which is why `test_ignore.py` asserts the
#: composed value written out literally rather than recomputing it here.
#:
#: `*/references/*` covers active and archived items with one pattern because
#: okf-io's `*` crosses `/` (`bundle.py:146`), and it has to be positively
#: nameable because `ignore=` has no negation. One thing to know: it ignores a
#: `references/` directory **anywhere** in the bundle, not only under `work/`.
#: Harmless today -- the entity lane has none -- but it is a claim on a
#: directory name outside this lane, in a bundle three packages share.
#:
#: `*/.DS_Store` joins it for the nested half of ADR-0028's root-scoped dot
#: exclusion, and stays out of `ARCHIVE_IGNORE` below for that constant's own
#: stated reason: `okf_ext.moves` never reads `bundle.ignored`, so an ignored
#: `.DS_Store` under `work/<slug>/` would be left behind and the directory
#: would never empty for `apply` to prune.
IGNORE: tuple[str, ...] = ("*/references/*", "*/.DS_Store", *_SCHEMA_IGNORE, *_SECTIONS_IGNORE)

#: `IGNORE` minus the lane pattern. The recipe the **archive path** plans
#: through, and nothing else (C4-A).
#:
#: `okf_ext.moves` builds its mapping from `bundle.concepts`, `bundle.assets`,
#: `bundle.indexes` and `bundle.logs` -- never from `bundle.ignored` -- so under
#: `IGNORE` a move of `work/<slug>/` finds no members at all and the archive is
#: silently half done. Dropping `*/references/*` is what makes the per-item
#: artifacts visible to the planner.
#:
#: One walk through this recipe serves both eligibility and the move, because
#: `_identify` returns `None` for any concept id whose remainder under `work/`
#: contains a `/` -- so `load_items` projects the same items under either.
#:
#: **It must never reach `validate()`**, which would then schema-check every
#: artifact in every `references/` tree, nor `update_index`, which would then
#: want a `# Subdirectories` entry for every item that has a working directory.
ARCHIVE_IGNORE: tuple[str, ...] = (*_SCHEMA_IGNORE, *_SECTIONS_IGNORE)


@dataclass(frozen=True, slots=True)
class WorkItem:
    """One work item, projected. `slug` is the stable key; `path` is not --
    it changes when the item is archived."""

    slug: str
    path: str
    archived: bool
    type: str
    title: str
    description: str
    status: str
    workflow_status: str
    phase: str | None
    effort: str | None
    blast_radius: str | None
    target: str | None
    opened: str
    updated: str
    affects: tuple[str, ...]
    parent: str | None
    depends_on: tuple[DependencyEdge, ...]
    dependency_issues: tuple[DependencyIssue, ...]
    children: tuple[str, ...]
    owner: str | None
    resolved_in: str | None
    #: Git provenance, written by `advance` from the session that ran a stage.
    #: Nothing in this package reads either: they sit in the same category as
    #: `owner` — caller-supplied facts the lane stores and the dispatch tier
    #: above it consumes.
    worktree: str | None
    branch: str | None
    superseded_by: str | None
    tags: tuple[str, ...]
    sources: tuple[Source, ...]
    has_spec_doc: bool
    has_plan_doc: bool


def _identify(concept_id: str) -> tuple[str, bool] | None:
    """*concept_id* as `(slug, archived)`, or `None` when it is not an item.

    Selection is by **directory, not by `type`** (C1-C). Type-based selection
    is more robust to someone reorganising the vault, but it makes a page with
    a mistyped `type` vanish from the projection entirely -- and since every
    lane rule reads the projection, a page that vanishes is a page nothing can
    report on. Directory-based selection keeps it visible and lets the rules
    say what is wrong with it.

    The archive prefix is tested first: `work/_archive/x` starts with `work/`
    too, and testing in the other order would project it as the item
    `_archive/x`, which is no slug at all.
    """
    for prefix, archived in ((f"{ARCHIVE_DIR}/", True), (f"{WORK_DIR}/", False)):
        if concept_id.startswith(prefix):
            remainder = concept_id[len(prefix) :]
            if remainder and "/" not in remainder:
                return remainder, archived
            return None
    return None


def _text(value: object) -> str:
    """A required string field. Only a `str` counts.

    No stringification of an `int` or a `bool`: a `phase: 3` projected as
    `"3"` would look like a value the enum could hold, and the rules would have
    nothing to complain about.
    """
    if isinstance(value, str):
        return value
    return ""


def _optional_text(value: object) -> str | None:
    """An optional string field. An empty string is absent, not empty."""
    if isinstance(value, str) and value:
        return value
    return None


def _text_tuple(value: object) -> tuple[str, ...]:
    """A list-of-strings field, with non-string entries dropped.

    The `isinstance(value, str)` guard is not redundant with the `list` check
    -- it is documentation. A bare string where a list belongs is the single
    likeliest hand-edit error in this lane (`affects: packages/foo`), and
    iterating it into characters is exactly the failure the guard names.
    """
    if isinstance(value, str) or not isinstance(value, list):
        return ()
    return tuple(entry for entry in value if isinstance(entry, str))


def _project(concept_id: str, slug: str, archived: bool, document: Document) -> WorkItem:
    """One page, projected. `children` is filled in by `load_items`, which is
    the only place that can see the whole set."""
    fm = document.fm
    # The extension keys are read through `fm_data(dates="iso")`, not off
    # `fm.extra`: ruamel parses an unquoted `opened: 2026-08-10` into a
    # `datetime.date`, so the raw extras carry a mix of dates and strings
    # depending only on how a human quoted them. The ISO projection is what
    # makes `opened` and `updated` reliably `str` -- the same reason
    # `okf_ext.schemas.rule` reads frontmatter that way.
    data = document.fm_data(dates="iso")
    source_ids = {source.id for source in fm.sources if source.id is not None}
    dependency_parse = parse_dependencies(data.get("depends_on"))
    return WorkItem(
        slug=slug,
        path=f"{concept_id}.md",
        archived=archived,
        type=_text(fm.type),
        title=_text(fm.title),
        description=_text(fm.description),
        status=_text(fm.status),
        workflow_status=_text(data.get("workflow_status")),
        phase=_optional_text(data.get("phase")),
        effort=_optional_text(data.get("effort")),
        blast_radius=_optional_text(data.get("blast_radius")),
        target=_optional_text(data.get("target")),
        opened=_text(data.get("opened")),
        updated=_text(data.get("updated")),
        affects=_text_tuple(data.get("affects")),
        parent=_optional_text(data.get("parent")),
        depends_on=dependency_parse.edges,
        dependency_issues=dependency_parse.issues,
        children=(),
        owner=_optional_text(data.get("owner")),
        resolved_in=_optional_text(data.get("resolved_in")),
        worktree=_optional_text(data.get("worktree")),
        branch=_optional_text(data.get("branch")),
        superseded_by=_optional_text(data.get("superseded_by")),
        tags=fm.tags,
        sources=fm.sources,
        has_spec_doc=SPEC_SOURCE_ID in source_ids,
        has_plan_doc=PLAN_SOURCE_ID in source_ids,
    )


def load_items(bundle: Bundle) -> tuple[WorkItem, ...]:
    """Every work item in *bundle*, sorted by `(slug, archived)`.

    `children` is derived across the whole set by grouping on `parent`, with
    archived items participating on both ends: a reference to an archived item
    is valid, and an archived child still belongs to its parent's list.

    The ordering is deterministic and nothing more -- child 5's rollup orders
    by `updated` then slug and re-sorts for itself.
    """
    projected: list[WorkItem] = []
    for concept_id, document in bundle.concepts.items():
        identified = _identify(concept_id)
        if identified is None:
            continue
        slug, archived = identified
        projected.append(_project(concept_id, slug, archived, document))

    by_parent: dict[str, set[str]] = {}
    for item in projected:
        if item.parent is not None:
            by_parent.setdefault(item.parent, set()).add(item.slug)

    empty: set[str] = set()
    resolved = [replace(item, children=tuple(sorted(by_parent.get(item.slug, empty)))) for item in projected]
    return tuple(sorted(resolved, key=lambda item: (item.slug, item.archived)))


__all__ = ["ARCHIVE_DIR", "ARCHIVE_IGNORE", "IGNORE", "WORK_DIR", "WorkItem", "load_items"]
