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

from work_tracker_okf.items import WORK_DIR
from work_tracker_okf.paths import item_page
from work_tracker_okf.vocabulary import SLUG_PREFIXES

#: Why a filing did not happen. A closed vocabulary following child 3's
#: `RefusalReason`: a page that already exists is a fact about the vault, not an
#: exception (`work-io` raised `FileExistsError`). There is no `force=` --
#: overwriting a filed work item is not an operation this lane should make one
#: flag away.
FilingRefusal = Literal["page-exists", "directory-exists", "unknown-type"]

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_WORDS_CLEAN = 4
_MAX_WORDS_HARD_CAP = 6

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
    "affects",
    "parent",
    "depends_on",
)


@dataclass(frozen=True, slots=True)
class FilingPlan:
    """What filing would write, and what it declines to write.

    Shares the writer vocabulary `AdvancePlan`, `IndexUpdate` and `BundleInstall`
    already use: `changed`, and a `diff()` that renders on demand and writes
    nothing.
    """

    slug: str
    target: Path
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
    `file_item` checks first and returns an `unknown-type` refusal instead, so
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
    type: str,  # see compose_slug
    title: str,
    description: str,
    on: date,
    affects: Sequence[str],
    parent: str | None,
    depends_on: Sequence[str],
    tags: Sequence[str],
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
        "type": type,
        "title": title,
        "description": description,
        "status": "draft",
        "workflow_status": "open",
        "opened": on,
        # A distinct `date` object, not `on` again: `Document.set` stores the
        # value as-is, and ruamel's representer aliases by object identity, so
        # the same object on both keys would render `updated: *id001` instead
        # of the bare date this format documents.
        "updated": date(on.year, on.month, on.day),
    }
    if tags:
        seeded["tags"] = list(tags)
    if affects:
        seeded["affects"] = list(affects)
    if parent is not None:
        seeded["parent"] = parent
    if depends_on:
        seeded["depends_on"] = list(depends_on)
    return seeded


def _refused(slug: str, target: Path, refusal: FilingRefusal, detail: str) -> FilingPlan:
    """A refusal carries no frontmatter and no body, which is what makes `apply`
    safe with one guard rather than several."""
    return FilingPlan(
        slug=slug,
        target=target,
        frontmatter=MappingProxyType({}),
        body="",
        warnings=(),
        refusal=refusal,
        detail=detail,
    )


def file_item(
    root: Path,
    *,
    type: str,  # see compose_slug
    title: str,
    description: str,
    on: date,
    words: str | None = None,
    epic_child: bool = False,
    parent: str | None = None,
    depends_on: Sequence[str] = (),
    affects: Sequence[str] = (),
    tags: Sequence[str] = (),
    section_set: SectionSet,
) -> FilingPlan:
    """Plan a new item page under *root*. Writes nothing; reads two paths.

    *words* defaults to *title*. *section_set* is passed in, never loaded here --
    this package never discovers paths.

    Raises `KeyError` when *section_set* carries no declaration for a type
    `SLUG_PREFIXES` knows. That is a configuration error, not content, and it is
    the same failure `code_wiki_okf.mirror.create.write_new_page` documents.
    """
    if type not in SLUG_PREFIXES:
        return _refused(
            "",
            root,
            "unknown-type",
            f"unknown type {type!r}; expected one of {sorted(SLUG_PREFIXES)}",
        )

    slug, warnings = compose_slug(type, title if words is None else words, on=on, epic_child=epic_child)
    target = item_page(slug).path(root)
    if target.exists():
        return _refused(slug, target, "page-exists", f"{target}: a page already exists here")

    directory = root / WORK_DIR / slug
    if directory.exists():
        return _refused(slug, target, "directory-exists", f"{directory}: a working directory already exists here")

    return FilingPlan(
        slug=slug,
        target=target,
        frontmatter=MappingProxyType(
            _seed(
                type=type,
                title=title,
                description=description,
                on=on,
                affects=affects,
                parent=parent,
                depends_on=depends_on,
                tags=tags,
            )
        ),
        body=render_skeleton(section_set.types[type]),
        warnings=warnings,
        refusal=None,
        detail=f"file {slug} as {type}",
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
    document = parse("")
    for key in _KEY_ORDER:
        if key in plan.frontmatter:
            document.set(key, plan.frontmatter[key])
    document.set_body(plan.body)
    plan.target.parent.mkdir(parents=True, exist_ok=True)
    plan.target.write_text(document.serialize(), encoding="utf-8")
    return plan.target


__all__ = ["FilingPlan", "FilingRefusal", "apply", "compose_slug", "file_item", "slugify"]
