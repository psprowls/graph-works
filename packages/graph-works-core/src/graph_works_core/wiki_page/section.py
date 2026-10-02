"""Replace one existing, prose-owned `##` section of a wiki page (D-005).

Plan by default. A live call plans, calls `before_apply` with the candidate,
writes and commits while holding the bundle root lock, so nothing another gw
verb does can land between the reviewed plan and the write.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from code_wiki_okf import is_code_wiki_type
from okf_ext.body import find_section, sections
from okf_ext.bundle import SCHEMA_DIRNAME
from okf_ext.schemas import SchemaError, declares_property, load_schemas
from okf_ext.shape import SectionSet
from okf_ext.splice import splice_sections
from okf_ext.writing import PendingWrite, write_all
from okf_io import Document
from okf_io import parse as parse_document

from graph_works_core.wiki_page.declarations import load_declarations
from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.commits import CommitOutcome, WorkspaceCommit
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.transactions import commit_pending, held_bundle_lock

SectionRefusal = Literal[
    "unknown-page",
    "work-item",
    "missing-section",
    "duplicate-section",
    "generated-section",
    "heading-in-body",
    "unbalanced-body",
]


@dataclass(frozen=True, slots=True)
class SectionWriteRun:
    """One section write: the section's body before and after, and what landed.

    `heading` is the page's own spelling once the section is found, else the
    requested one. `before`/`after` are the lines under the heading (equal on
    a refusal). `written`, `failures` and `commit` stay empty until applied.
    """

    id: str
    heading: str
    before: str
    after: str
    refusal: SectionRefusal | None
    applied: bool = False
    written: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    commit: CommitOutcome | None = None


@dataclass(frozen=True, slots=True)
class _Draft:
    """What applying a planned, unrefused write needs beyond the run itself."""

    document: Document
    body: str
    bump_updated: bool


def _writable(declarations: SectionSet, type_name: str, heading: str) -> bool:
    """Whether the type's declarations leave `## heading` to a person.

    A code-graph page is generated: only a heading declared `prose` is
    writable. A curated page is the person's: any heading is, unless a
    declaration gives it to a generator or template.
    """
    declaration = declarations.types.get(type_name)
    key = heading.casefold()
    specs = (
        ()
        if declaration is None
        else tuple(s for s in declaration.sections if s.level == 2 and s.heading.strip().casefold() == key)
    )
    if is_code_wiki_type(type_name):
        return bool(specs) and all(spec.ownership == "prose" for spec in specs)
    return all(spec.ownership == "prose" for spec in specs)


def _declares_updated(layout: WorkspaceLayout, type_name: str) -> bool:
    try:
        schemas = load_schemas(layout.config_dir / SCHEMA_DIRNAME)
    except FileNotFoundError:
        return False
    except SchemaError as exc:
        raise WorkspaceError(str(exc)) from exc
    return declares_property(schemas, type_name, "updated")


def _outline(body: str) -> list[tuple[int, str]]:
    """The page's `#`/`##` headings: what a body must leave exactly as it found them."""
    return [(parsed.level, parsed.heading) for parsed in sections(body) if parsed.level <= 2]


def _plan(layout: WorkspaceLayout, page_id: str, wanted: str, body: str) -> tuple[SectionWriteRun, _Draft | None]:
    def refused(kind: SectionRefusal, heading: str = wanted, before: str = "") -> tuple[SectionWriteRun, None]:
        return SectionWriteRun(page_id, heading, before, before, kind), None

    if page_id == "work" or page_id.startswith("work/"):
        return refused("work-item")
    document = load_workspace_bundle(layout).concept(page_id)
    if document is None:
        return refused("unknown-page")
    key = wanted.casefold()
    matches = [s for s in sections(document.body) if s.level == 2 and s.heading.casefold() == key]
    if not matches:
        return refused("missing-section")
    if len(matches) > 1:
        return refused("duplicate-section")
    section = matches[0]
    before = section.slice(document.body)
    type_name = (document.fm.type or "").strip()
    if not _writable(load_declarations(layout), type_name, section.heading):
        return refused("generated-section", section.heading, before)
    # markdown-it decides what is a heading, so a `#` inside a fence is text.
    if any(parsed.level <= 2 for parsed in sections(body)):
        return refused("heading-in-body", section.heading, before)
    spliced, _ = splice_sections(document.body, {f"## {section.heading}": body})
    # An unclosed fence or HTML comment swallows every later heading of the page.
    if _outline(spliced) != _outline(document.body):
        return refused("unbalanced-body", section.heading, before)
    written = find_section(spliced, section.heading, level=2)
    assert written is not None  # the outline is unchanged, so this section's heading survived
    run = SectionWriteRun(page_id, section.heading, before, written.slice(spliced), None)
    return run, _Draft(document, spliced, _declares_updated(layout, type_name))


def _apply(layout: WorkspaceLayout, run: SectionWriteRun, draft: _Draft, today: date) -> SectionWriteRun:
    scratch = parse_document(draft.document.raw_text, path=draft.document.path)
    scratch.set_body(draft.body)
    if draft.bump_updated:
        scratch.set("updated", today)  # a `date`, not a string: ruamel would quote an ISO string
    member = f"{run.id}.md"
    result = write_all(
        [PendingWrite(member=member, path=layout.bundle_dir / member, rendered=scratch.serialize(), on_written=_noop)]
    )
    failures = tuple(f"{failure.path}: {failure.kind} -- {failure.error}" for failure in result.failed)
    commit = None
    if result.written:
        subject = f"workspace: write {run.id} section {run.heading}"
        commit = commit_pending(layout, WorkspaceCommit(subject, extra_paths=tuple(result.written)))
    return replace(run, applied=True, written=tuple(result.written), failures=failures, commit=commit)


def _noop() -> None:
    """Nothing in memory to keep in step: each call reloads the bundle."""


def run_section_write(
    layout: WorkspaceLayout,
    page_id: str,
    heading: str,
    body: str,
    *,
    today: date,
    dry_run: bool = True,
    before_apply: Callable[[SectionWriteRun], None] | None = None,
) -> SectionWriteRun:
    """Plan (default) or apply replacing *page_id*'s `## heading` body with *body*.

    The heading matches case-insensitively and must name exactly one `##`
    section. Refusals are results, never raised. Applying keeps frontmatter
    bytes, bumps `updated:` to *today* when the type's schema declares it, and
    commits the page under `workflow.workspace_commits`.

    `before_apply`, on a live call, sees the candidate with application fields
    empty before any write, refusals included. Raising aborts the call. Dry
    runs never invoke it. A malformed section or schema declaration raises
    `WorkspaceError`.
    """
    wanted = heading.strip()
    if dry_run:
        return _plan(layout, page_id, wanted, body)[0]
    with held_bundle_lock(layout):
        run, draft = _plan(layout, page_id, wanted, body)
        if before_apply is not None:
            before_apply(run)
        if draft is None:
            return run
        return _apply(layout, run, draft, today)
