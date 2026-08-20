"""Filing a new work item: one page, planned then applied.

Plan-and-apply rather than a direct write, because this package wrote the
convention down two children ago -- `advance()` returns an `AdvancePlan` and
mutates nothing -- and a filing dry-run is something a CLI wants rather than
something it should reconstruct. `dry_run` is **not** a parameter: not calling
`apply` *is* the dry run, which is the same split `okf_ext.moves` and
`okf_ext.tags` make (C2-E).

**Filing writes one page** (C2-F). `work/index.md` reconciliation and the
`log.md` line are a composing CLI's, out of okf-io's `update_index` and
`append_log_entry` -- not for `work-io`'s reason, which has evaporated now that
okf-io ships both writers, but because index reconciliation is bundle-wide:
folding it in here means the per-item writer takes a loaded `Bundle` and a
`FilingPlan` then spans three files in two directories. `update_index`
reconciles rather than regenerates (ADR-0009), so the staleness a forgetful
caller leaves behind is self-healing.

`work_io.filing.validate`'s seven presence checks and its `category == "work"`
assertion are **deleted, not ported** (C2-K / D-E): the schema is the validator,
and a subset re-implemented in Python is a second place to disagree about what a
work item requires.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from okf_ext.sections import render_skeleton
from okf_ext.shape import SectionSet
from okf_io import parse

from work_tracker_okf._selection import active_preferred_slug_index
from work_tracker_okf.dependencies import DependencyEdge, serialize_dependencies, validate_dependencies
from work_tracker_okf.hierarchy import unknown_depends_on
from work_tracker_okf.items import WORK_DIR, WorkItem
from work_tracker_okf.paths import item_page
from work_tracker_okf.vocabulary import BLAST_RADII, EFFORTS, PARENT_TYPES, SLUG_PREFIXES, TERMINAL_STATUSES

#: Why a filing did not happen. A closed vocabulary following child 3's
#: `RefusalReason`: a page that already exists is a fact about the vault, not an
#: exception (`work-io` raised `FileExistsError`). There is no `force=` --
#: overwriting a filed work item is not an operation this lane should make one
#: flag away.
FilingRefusal = Literal[
    "page-exists",
    "directory-exists",
    "unknown-type",
    "unknown-parent",
    "invalid-parent-type",
    "inactive-parent",
    "unknown-dependency",
    "invalid-dependency",
    "invalid-effort",
    "invalid-blast-radius",
    "invalid-target",
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_WORDS_CLEAN = 4
_MAX_WORDS_HARD_CAP = 6
_TARGET_RE = re.compile(r"^([0-9]{4}-Q[1-4]|[0-9]{4}-(0[1-9]|1[0-2]))$")

#: The order keys are set in -- and, for the six keys okf-io's core schema does
#: not know, the order they appear in. `Document.set` places a *known* key per
#: `PREFERRED_KEY_ORDER` regardless of when it is set, which is why `tags` is
#: listed above `status` here: that is where it lands either way, and listing it
#: where it lands is what keeps this tuple readable as the page's shape.
_KEY_ORDER: tuple[str, ...] = (
    "type",
    "title",
    "description",
    "tags",
    "status",
    "workflow_status",
    "opened",
    "updated",
    "effort",
    "blast_radius",
    "target",
    "owner",
    "affects",
    "parent",
    "depends_on",
)


@dataclass(frozen=True, slots=True)
class FilingSeed:
    """Caller-supplied facts for a prospective work item.

    Dates and the current item graph are supplied by the caller; this module
    plans one page and does not discover either on its own.
    """

    type: str
    title: str
    description: str
    on: date
    words: str | None = None
    effort: str | None = None
    blast_radius: str | None = None
    target: str | None = None
    owner: str | None = None
    parent: str | None = None
    depends_on: tuple[DependencyEdge, ...] = ()
    affects: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FilingPlan:
    """What filing would write, and what it declines to write.

    Shares the writer vocabulary `AdvancePlan`, `IndexUpdate` and `BundleInstall`
    already use: `changed`, and a `diff()` that renders on demand and writes
    nothing.
    """

    seed: FilingSeed
    slug: str
    target: Path
    work_directory: Path
    frontmatter: Mapping[str, Any]
    body: str
    warnings: tuple[str, ...]
    refusal: FilingRefusal | None
    detail: str

    @property
    def changed(self) -> bool:
        return self.refusal is None

    def diff(self) -> str:
        """Render the plan. Writes nothing."""
        if self.refusal is not None:
            return f"{self.slug}: refused ({self.refusal}) -- {self.detail}"
        lines = [f"+ {self.target}"]
        lines.extend(f"  {key}: {value!r}" for key, value in self.frontmatter.items())
        return "\n".join(lines)


def slugify(title: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to `-`, strip the edges.

    Empty or all-punctuation input degrades to `untitled` -- silently, because
    there is nothing to warn about.
    """
    return _SLUG_RE.sub("-", title.lower()).strip("-") or "untitled"


def compose_slug(
    type: str,  # the lane's own field name, as `WorkItem.type` already is
    words: str,
    *,
    on: date,
    epic_child: bool = False,
) -> tuple[str, tuple[str, ...]]:
    """`<on>-[epic-]<type-kebab>-<w1..w4>`, plus any warnings.

    **The date prefix stays** (C2-A). `work-io` carried two answers --
    `compose_slug` returned no date and `write_work_item` prepended one -- and the
    filename silently won: every consumer treats the date-prefixed stem as the
    slug. Dropping it would rename every live item and invalidate every
    `resource` and link pointing at them. The cost is real and worth recording:
    the slug asserts a date that can lie when an item is filed on a day other
    than its `opened`, and it is eleven characters in every `sources[].resource`.

    The 4/6-word ladder is `work-io`'s, minus the CLI flag name its message
    carried: 1-4 tokens pass clean, 5-6 are kept with a warning, 7+ are truncated
    to 6 with one.

    Raises `ValueError` on a type outside `SLUG_PREFIXES` -- caller error.
    `plan_filing` checks first and returns an `unknown-type` refusal instead, so
    this raise is unreachable through that door.
    """
    kebab = SLUG_PREFIXES.get(type)
    if kebab is None:
        raise ValueError(f"unknown type {type!r}; expected one of {sorted(SLUG_PREFIXES)}")
    prefix = f"epic-{kebab}" if epic_child else kebab
    tokens = slugify(words).split("-")
    warnings: list[str] = []
    if len(tokens) > _MAX_WORDS_HARD_CAP:
        kept = "-".join(tokens[:_MAX_WORDS_HARD_CAP])
        warnings.append(f"slug words: {len(tokens)} words truncated to {_MAX_WORDS_HARD_CAP} ({kept})")
        tokens = tokens[:_MAX_WORDS_HARD_CAP]
    elif len(tokens) > _MAX_WORDS_CLEAN:
        warnings.append(f"slug words: {len(tokens)} words kept (recommended 1-{_MAX_WORDS_CLEAN})")
    return f"{on.isoformat()}-{prefix}-{'-'.join(tokens)}", tuple(warnings)


def _seed(
    *,
    seed: FilingSeed,
) -> dict[str, Any]:
    """The frontmatter a filed item starts with.

    `status: draft` is W-D's, and it is load-bearing: the base schema exempts a
    draft from requiring `effort` and `affects`. **No `phase`** -- child 1 left it
    deliberately unrequired and child 3's router has an entry branch defined for
    `phase: None`, so writing one here would pre-empt a routing decision the
    table owns.

    `opened` and `updated` are `date` objects, not ISO strings: ruamel quotes a
    `str` that would re-parse as a date, and every authored page renders bare.
    """
    seeded: dict[str, Any] = {
        "type": seed.type,
        "title": seed.title,
        "description": seed.description,
        "status": "draft",
        "workflow_status": "open",
        "opened": seed.on,
        # A distinct `date` object, not `on` again: `Document.set` stores the
        # value as-is, and ruamel's representer aliases by object identity, so
        # the same object on both keys would render `updated: *id001` instead
        # of the bare date this format documents.
        "updated": date(seed.on.year, seed.on.month, seed.on.day),
    }
    for key, value in (
        ("effort", seed.effort),
        ("blast_radius", seed.blast_radius),
        ("target", seed.target),
        ("owner", seed.owner),
    ):
        if value is not None:
            seeded[key] = value
    if seed.tags:
        seeded["tags"] = list(seed.tags)
    if seed.affects:
        seeded["affects"] = list(seed.affects)
    if seed.parent is not None:
        seeded["parent"] = seed.parent
    if seed.depends_on:
        seeded["depends_on"] = list(serialize_dependencies(seed.depends_on))
    return seeded


def _freeze_yaml(value: object) -> object:
    """Recursively remove every mutable container from a planned YAML value."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_yaml(nested) for key, nested in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_yaml(nested) for nested in value)
    return value


def _freeze_frontmatter(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: _freeze_yaml(value) for key, value in values.items()})


def _materialize_yaml(value: object) -> object:
    """Return ordinary mutable containers suitable for ``Document.set``."""
    if isinstance(value, Mapping):
        return {key: _materialize_yaml(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_materialize_yaml(nested) for nested in value]
    return value


def _materialize_frontmatter(values: Mapping[str, object]) -> dict[str, object]:
    return {key: _materialize_yaml(value) for key, value in values.items()}


def _refused(
    seed: FilingSeed,
    slug: str,
    target: Path,
    work_directory: Path,
    refusal: FilingRefusal,
    detail: str,
) -> FilingPlan:
    """A refusal carries no frontmatter and no body, which is what makes `apply`
    safe with one guard rather than several."""
    return FilingPlan(
        seed=seed,
        slug=slug,
        target=target,
        work_directory=work_directory,
        frontmatter=MappingProxyType({}),
        body="",
        warnings=(),
        refusal=refusal,
        detail=detail,
    )


def _metadata_refusal(seed: FilingSeed) -> FilingRefusal | None:
    if seed.effort is not None and seed.effort not in EFFORTS:
        return "invalid-effort"
    if seed.blast_radius is not None and seed.blast_radius not in BLAST_RADII:
        return "invalid-blast-radius"
    if seed.target is not None and _TARGET_RE.fullmatch(seed.target) is None:
        return "invalid-target"
    return None


def _parent_note(parent: WorkItem | None) -> str:
    if parent is None:
        return "- Seeded for design.\n"
    if parent.type == "Epic":
        return f"- Designed as part of epic {parent.slug}.\n"
    return f"- Filed as a child of feature {parent.slug}.\n"


def plan_filing(
    root: Path,
    items: Sequence[WorkItem],
    seed: FilingSeed,
    section_set: SectionSet,
) -> FilingPlan:
    """Plan a new item page under *root*. Writes nothing; reads two paths.

    The caller supplies *items*, dates, and declarations. Resolve a parent and
    dependency graph before collision checks, so an invalid plan never proceeds
    to an effectful apply.

    Raises `KeyError` when *section_set* carries no declaration for a type
    `SLUG_PREFIXES` knows. That is a configuration error, not content, and it is
    the same failure `code_wiki_okf.mirror.create.write_new_page` documents.
    """
    work_root = root / WORK_DIR
    if seed.type not in SLUG_PREFIXES:
        return _refused(
            seed,
            "",
            root,
            work_root,
            "unknown-type",
            f"unknown type {seed.type!r}; expected one of {sorted(SLUG_PREFIXES)}",
        )

    metadata_refusal = _metadata_refusal(seed)
    if metadata_refusal is not None:
        return _refused(
            seed,
            "",
            root,
            work_root,
            metadata_refusal,
            f"invalid {metadata_refusal[8:].replace('-', ' ')}",
        )

    by_slug = active_preferred_slug_index(items)
    parent_item: WorkItem | None = None
    if seed.parent is not None:
        parent_item = by_slug.get(seed.parent)
        if parent_item is None:
            return _refused(seed, "", root, work_root, "unknown-parent", f"unknown parent {seed.parent!r}")
        if parent_item.type not in PARENT_TYPES:
            return _refused(
                seed,
                "",
                root,
                work_root,
                "invalid-parent-type",
                f"parent {seed.parent!r} has type {parent_item.type!r}",
            )
        if parent_item.archived or parent_item.workflow_status in TERMINAL_STATUSES:
            return _refused(seed, "", root, work_root, "inactive-parent", f"parent {seed.parent!r} is inactive")

    slug, warnings = compose_slug(
        seed.type,
        seed.title if seed.words is None else seed.words,
        on=seed.on,
        epic_child=parent_item is not None and parent_item.type == "Epic",
    )
    target = item_page(slug).path(root)
    work_directory = root / WORK_DIR / slug

    dependency_issues = validate_dependencies(seed.depends_on, parent=seed.parent, self_slug=slug)
    if dependency_issues:
        detail = "; ".join(f"{issue.code}: {issue.detail}" for issue in dependency_issues)
        return _refused(seed, slug, target, work_directory, "invalid-dependency", detail)

    unknown = unknown_depends_on(items, seed.depends_on)
    if unknown:
        slug_value, hint = next(iter(unknown.items()))
        detail = f"unknown dependency {slug_value!r}"
        if hint is not None:
            detail += f"; did you mean {hint!r}?"
        return _refused(seed, slug, target, work_directory, "unknown-dependency", detail)

    if target.exists():
        return _refused(seed, slug, target, work_directory, "page-exists", f"{target}: a page already exists here")

    if work_directory.exists():
        return _refused(
            seed,
            slug,
            target,
            work_directory,
            "directory-exists",
            f"{work_directory}: a working directory already exists here",
        )

    return FilingPlan(
        seed=seed,
        slug=slug,
        target=target,
        work_directory=work_directory,
        frontmatter=_freeze_frontmatter(_seed(seed=seed)),
        body=render_skeleton(section_set.types[seed.type]) + _parent_note(parent_item),
        warnings=warnings,
        refusal=None,
        detail=f"file {slug} as {seed.type}",
    )


def apply(plan: FilingPlan) -> Path:
    """Write *plan*, and return where it landed.

    Builds an in-memory `Document` and asks it to serialize itself rather than
    hand-building YAML, following `code_wiki_okf.mirror.create.write_new_page`:
    `Document.set` inserts each key where okf-io's `PREFERRED_KEY_ORDER` implies,
    or at the end for a key the core schema does not know.

    Raises `ValueError` on a refused plan. `advance.apply` asserts in the
    equivalent place because a refused `AdvancePlan` carries no changes and
    applying one is a harmless no-op; here the target of a `page-exists` refusal
    is a real page, so a write would clobber exactly what the refusal protects --
    and an `assert` vanishes under `-O`.
    """
    if plan.refusal is not None:
        raise ValueError(f"refused plan ({plan.refusal}) must not be applied: {plan.detail}")
    if plan.target.exists():
        raise FileExistsError(f"{plan.target}: a page already exists here")
    if plan.work_directory.exists():
        raise FileExistsError(f"{plan.work_directory}: a working directory already exists here")
    document = parse("")
    frontmatter = _materialize_frontmatter(plan.frontmatter)
    for key in _KEY_ORDER:
        if key in frontmatter:
            document.set(key, frontmatter[key])
    document.set_body(plan.body)
    plan.target.parent.mkdir(parents=True, exist_ok=True)
    with plan.target.open("x", encoding="utf-8") as stream:
        stream.write(document.serialize())
    return plan.target


__all__ = ["FilingPlan", "FilingRefusal", "FilingSeed", "apply", "compose_slug", "plan_filing", "slugify"]
