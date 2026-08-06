"""Rewriting a v0.1 bundle into v0.2 form (OKF v0.2 §13.1, ADR-0003).

The read half already ships: ``models.build_frontmatter`` falls back to a v0.1
top-level ``timestamp`` and to a body ``# Citations`` list, recording both in
``Frontmatter.fallbacks``. This is the write half. It rewrites exactly the
constructs the read fallback fired on, and nothing else.

**The trigger is ``fallbacks`` membership, never an independent re-scan.** That
is the spine of the design: ``_rules.legacy`` keys off the same set, for the
reason its docstring gives, so reader, validator and writer can never disagree
about what counts as v0.1. It also settles the conflict cases without a policy
of their own — a ``timestamp`` beside a real ``generated.at`` fires no
fallback, so it is reported as ``conflicting-provenance`` and left alone rather
than having one of two disagreeing provenance claims silently deleted.

One documented exception, and it is a place where the fallback set is *wider*
than what should be migrated: a ``timestamp`` the reader cannot coerce to an
instant -- blank, or any other shape ``models._as_timestamp`` fails to parse --
fires the fallback (the builder tests ``is not None``, not "parses") but
carries nothing to migrate. ``_migrate_timestamp`` declines every such value,
but only one shape is silent about it: a blank or whitespace-only string,
which ``_rules.legacy`` also treats as absent, so nothing warns and there is
nothing to report. Every other uncoercible shape -- an int, a bool, a list, a
string ``_as_timestamp`` cannot parse -- still fires ``legacy.timestamp``, so
the decline is reported too, as an ``Unmigrated`` with reason
``"not-an-instant"``: the caller learns it needs a human rather than hearing
nothing (§3).

Byte fidelity needs nothing new here. Frontmatter edits go through
``Document.set`` / ``Document.delete`` and are rendered by the ADR-0001 splice;
body edits go through ``_edit.apply``, which copies every unedited line
verbatim. The existing guarantee carries over unchanged: always valid, never
lossy, minimal in the common case.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from ruamel.yaml.comments import CommentedMap

from okf_io import _edit, _md
from okf_io.bundle import Bundle
from okf_io.document import Document
from okf_io.links import is_external
from okf_io.models import parse_actor

#: v0.1 ``timestamp`` carries no author, and §5.2 requires ``by`` inside
#: ``generated``, so a default is unavoidable. Naming the process honestly
#: beats attributing the migration to a person who did not perform it.
DEFAULT_ACTOR = "process:okf-io-migrate"

#: Everything that is not a lowercase ASCII alphanumeric separates slug words.
#: A non-ASCII letter therefore becomes a separator rather than surviving into
#: an id: `id` is a join key, and a key nobody can type is not one.
_SLUG_RE = re.compile(r"[^a-z0-9]+")

MigrationChangeKind = Literal["generated", "sources"]
#: Closed vocabulary a caller can branch on without string matching.
#:
#: - ``"unparseable"`` -- the document has a ``parse_error``.
#: - ``"impure-section"`` (§5.4) -- the citations section holds more than
#:   citation entries.
#: - ``"not-a-resource"`` (§5.2) -- a citation item is prose, not a link or a
#:   URI.
#: - ``"conflicting-provenance"`` (§3) -- a ``timestamp`` sits beside a real
#:   ``generated.at``.
#: - ``"not-an-instant"`` -- a ``timestamp`` fired the read fallback but is
#:   not a shape the reader can coerce to an instant (and is not the blank
#:   string, which is silently exempt -- see the module docstring).
#: - ``"multiple-citations-sections"`` (§5.4) -- the document carries more
#:   than one ``# Citations`` section; migrating the first would strand the
#:   second.
UnmigratedReason = Literal[
    "unparseable",
    "impure-section",
    "not-a-resource",
    "conflicting-provenance",
    "not-an-instant",
    "multiple-citations-sections",
]


@dataclass(frozen=True, slots=True)
class MigrationChange:
    """One construct rewritten, carrying what was written in its place.

    ``kind`` says which fields are meaningful: ``"generated"`` fills ``actor``
    and ``at``, ``"sources"`` fills ``ids``. A caller branches on the closed
    vocabulary rather than matching strings in a message.
    """

    kind: MigrationChangeKind
    actor: str | None = None  # `"generated"`: what `by` holds afterwards
    at: str | None = None  # `"generated"`: the instant, as the view will report it
    ids: tuple[str, ...] = ()  # `"sources"`: the generated ids, in source order


@dataclass(frozen=True, slots=True)
class Unmigrated:
    """One construct the rewriter saw and declined, and why."""

    reason: UnmigratedReason
    message: str


@dataclass(frozen=True, slots=True)
class Migration:
    """What migrating one concept would do. Same shape as ``IndexUpdate``.

    ``changed`` compares and renders nothing; ``diff()`` renders on demand and
    writes nothing. Three writers, one result vocabulary.
    """

    path: str  # bundle-relative posix: the concept's id plus `.md`
    before: str
    after: str
    changes: tuple[MigrationChange, ...]
    unmigrated: tuple[Unmigrated, ...]

    @property
    def changed(self) -> bool:
        return self.after != self.before

    def diff(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=f"a/{self.path}",
                tofile=f"b/{self.path}",
            )
        )


def _as_text(value: Any) -> str:  # noqa: ANN401 -- renders an arbitrary raw YAML scalar
    """The instant as ``Frontmatter.generated.at`` will report it.

    Matches ``models._as_timestamp``'s rendering rather than ``str()``, so the
    reported value is the one a caller will read back rather than ruamel's
    ``repr`` of a ``TimeStamp``.
    """
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _is_instant(value: Any) -> bool:  # noqa: ANN401 -- raw YAML value of unknown shape
    """Whether *value* is a shape ``models._as_timestamp`` can read back as an instant.

    Mirrors its accepted shapes exactly: a ``datetime``, a ``date``, or a
    ``str`` that ``datetime.fromisoformat`` parses after the same ``Z`` ->
    ``+00:00`` substitution. Anything else -- an int, a bool, a mapping, a
    list, a blank or otherwise unparseable string -- is a value the reader
    cannot coerce, so ``generated.at`` would come back ``None`` with a
    ``coercion_failures`` entry. A blank string is not special-cased: it fails
    the same ``fromisoformat`` parse as any other unparseable string.
    """
    if isinstance(value, datetime | date):
        return True
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True
    return False


def _migrate_timestamp(
    document: Document,
    *,
    fallbacks: frozenset[str],
    actor: str,
    changes: list[MigrationChange],
    unmigrated: list[Unmigrated],
) -> None:
    """§4: ``timestamp`` becomes ``generated: { by, at }``.

    The ``at`` value **passes through verbatim** -- whatever ``fm_raw`` holds is
    what ``generated.at`` receives. A quoted string keeps its quotes and a
    ruamel ``TimeStamp`` re-emits its own source spelling, so the instant
    crosses unchanged in representation as well as value. Nothing normalizes,
    reformats or re-serializes it.

    Two shapes, because the fallback fires on both. With no ``generated`` at
    all, a whole block is inserted at its ``PREFERRED_KEY_ORDER`` position.
    With a *partial* one -- an authored ``by`` and no ``at`` -- the block is
    filled in place through the documented ``fm_raw`` + ``mark_dirty()`` escape
    hatch, because replacing it would delete the author.
    """
    raw = document.fm_raw.get("timestamp")
    if raw is None:
        return
    if "generated.at" not in fallbacks:
        # The fallback fires whenever a `timestamp` sits beside no real
        # `generated.at`, so not firing means there is one. Two provenance
        # claims may name different instants, and deleting one of them is not
        # a mechanical decision -- `legacy.timestamp` keeps warning until a
        # human resolves it.
        unmigrated.append(
            Unmigrated(
                "conflicting-provenance",
                "`timestamp` sits beside a `generated.at`; a human resolves which is right",
            )
        )
        return
    if not _is_instant(raw):
        # Not a shape the reader can coerce to an instant -- a blank string,
        # an int, a bool, a list, a string `_as_timestamp` can't parse. There
        # is nothing to migrate: writing it into `generated.at` would not fix
        # it, only launder it into v0.2 shape while silencing the warning that
        # flags it.
        #
        # A blank or whitespace-only string is the one shape `_rules.legacy`
        # also treats as absent: nothing warns, so there is nothing to report,
        # and the decline stays silent. Every other shape here still fires
        # `legacy.timestamp`, so the decline is reported too -- otherwise the
        # caller is left with a warning no result explains.
        if isinstance(raw, str) and not raw.strip():
            return
        unmigrated.append(
            Unmigrated(
                "not-an-instant",
                f"`timestamp: {raw!r}` is not a shape that coerces to an instant; a human must supply a real instant",
            )
        )
        return

    existing = document.fm_raw.get("generated")
    if existing is not None and not isinstance(existing, MutableMapping):
        unmigrated.append(
            Unmigrated(
                "conflicting-provenance",
                "`generated` is present but is not a mapping; nothing mechanical to merge into",
            )
        )
        return

    if isinstance(existing, MutableMapping):
        existing["at"] = raw
        if existing.get("by") is None:
            existing["by"] = actor
        document.mark_dirty()
        by = str(existing["by"])
    else:
        block = CommentedMap()
        block["by"] = actor
        block["at"] = raw
        document.set("generated", block)
        by = actor

    document.delete("timestamp")
    changes.append(MigrationChange("generated", actor=by, at=_as_text(raw)))


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def _identifiers(
    planned: Sequence[tuple[str | None, str | None]],
    taken: set[str],
) -> list[str]:
    """A deterministic ``id`` per planned source, unique against *taken*.

    §5.5: a kebab slug of the title, falling back to the resource; a slug that
    comes out empty falls back to ``source-<n>`` at the entry's 1-based
    position. A collision appends ``-2``, ``-3``, ....

    *taken* arrives seeded with the footnote labels the body **already**
    carries, not just with the ids generated so far. §5.1's join runs on label
    equality, so an id colliding with an existing label would silently attach
    an unrelated footnote to this source -- the quiet misattribution the
    suffix exists to prevent.

    Deterministic by construction: no clock, no randomness, so migrating the
    same file twice yields the same ids.
    """
    out: list[str] = []
    for position, (title, resource) in enumerate(planned, start=1):
        base = _slug(title or resource or "") or f"source-{position}"
        identifier = base
        suffix = 2
        while identifier in taken:
            identifier = f"{base}-{suffix}"
            suffix += 1
        taken.add(identifier)
        out.append(identifier)
    return out


def _migrate_citations(
    document: Document,
    *,
    fallbacks: frozenset[str],
    changes: list[MigrationChange],
    unmigrated: list[Unmigrated],
) -> None:
    """§5: a body ``# Citations`` list becomes a ``sources[]`` family.

    Entry *values* come from ``doc.fm.sources`` -- the tuple the read fallback
    already produced -- and never from a second parse. The locator is consulted
    for two things the view cannot express: the line range to delete, and
    whether each entry was a markdown link, which is what decides between a
    ``resource`` and a refusal (§5.2).

    **All-or-nothing per document** (§5.4). An impure section or a single prose
    item leaves the section exactly as written. Partial surgery is the
    alternative and it is worse: it means rewriting a section rather than
    deleting it, and it leaves a document half in each format. Rewrite A is
    independent -- a document can migrate its timestamp and be refused here in
    the same pass.

    **A second ``# Citations`` heading refuses the whole document too.**
    `_md.citations_section` locates only the *first* one, and its own `stop`
    ends at the next heading -- so migrating would delete just that first
    range and leave a second, now-unreachable section stranded: `sources` is
    authored, the fallback stops firing, and nothing can ever see it again.
    That is exactly the half-migrated shape §5.4 exists to prevent, reached
    without a refusal, so it is checked before anything else here. A
    blockquoted `# Citations` is somebody else's content -- consistent with
    `citations_section`'s own filter -- and never counts.
    """
    if "sources" not in fallbacks:
        return

    body = document.body
    index = _md.parse_body(body)
    citations_headings = [
        heading for heading in index.headings if not heading.quoted and heading.text.casefold() == "citations"
    ]
    if len(citations_headings) > 1:
        unmigrated.append(
            Unmigrated(
                "multiple-citations-sections",
                "the document carries more than one `# Citations` section; migrating "
                "one would leave the other stranded and unreachable; a human must "
                "consolidate them",
            )
        )
        return

    section = _md.citations_section(body)
    sources = document.fm.sources
    if section is None or len(section.entries) != len(sources):
        # Defensive: the fallback in `fm.sources` came from this same locator,
        # so the two cannot disagree. Refusing beats deleting a range whose
        # entries were derived from a different reading of the section.
        return

    if not section.pure:
        unmigrated.append(
            Unmigrated(
                "impure-section",
                "`# Citations` holds more than citation entries; left exactly as written",
            )
        )
        return

    planned: list[tuple[str | None, str | None]] = []
    for citation, source in zip(section.entries, sources, strict=True):
        if citation.link_target is not None:
            planned.append((source.title, source.resource))
            continue
        # A non-link item: the reader modelled it `Source(title=text,
        # resource=text)`. A URI is a resource that happens to carry no label;
        # anything else is prose, and `resource: See the FY2026 policy binder`
        # is never written.
        if not is_external((source.resource or "").strip()):
            unmigrated.append(
                Unmigrated(
                    "not-a-resource",
                    f"citation {citation.text!r} is prose, not a resource; the section is left exactly as written",
                )
            )
            return
        planned.append((None, source.resource))

    identifiers = _identifiers(planned, set(_md.parse_body(body).footnote_labels))

    entries: list[CommentedMap] = []
    for (title, resource), identifier in zip(planned, identifiers, strict=True):
        entry = CommentedMap()
        if title:
            entry["title"] = title
        entry["resource"] = resource
        entry["id"] = identifier
        entries.append(entry)
    document.set("sources", entries)

    # §5.6. One definition per created source. `BodyIndex.footnote_labels` is
    # the union of references and definitions, so a definition alone satisfies
    # `provenance.footnote_join` in both directions: a migrated document
    # validates clean without the rewriter inventing a sentence. Inline `[^id]`
    # references are out of scope -- placing one means deciding which claim a
    # source supports, and a mechanical guess is silent misattribution.
    newline = _edit.newline_of(body)
    definitions = tuple(
        f"[^{identifier}]: [{title}]({resource}){newline}" if title else f"[^{identifier}]: {resource}{newline}"
        for (title, resource), identifier in zip(planned, identifiers, strict=True)
    )

    trimmed = _edit.apply(body, [_edit.Edit(section.start, section.stop)])
    last = _edit.line_count(trimmed)
    # The definitions sit apart from the prose above them -- but the deletion
    # usually leaves the blank line that separated the citations section, and
    # adding another would double it.
    separator = () if last == 0 or not _edit.line_at(trimmed, last).strip() else (newline,)
    document.set_body(_edit.apply(trimmed, [_edit.insert_after(trimmed, last, (*separator, *definitions))]))
    changes.append(MigrationChange("sources", ids=tuple(identifiers)))


def _migrate_one(concept_id: str, original: Document, *, actor: str) -> Migration | None:
    path = f"{concept_id}.md"
    before = original.raw_text

    if original.parse_error is not None:
        # `Document._require_mutable` raises on such a document and this must
        # not: nothing on the content path raises (§11).
        return Migration(
            path,
            before,
            before,
            (),
            (
                Unmigrated(
                    "unparseable",
                    f"{original.parse_error.kind}: {original.parse_error.message}",
                ),
            ),
        )

    # §6.3. An independent re-parse, never `rendered_with_body`: that clone is
    # a shallow `dataclasses.replace`, so `clone.fm_raw is document.fm_raw`,
    # and frontmatter is precisely what this rewriter mutates. A `dry_run=True`
    # call that left the in-memory bundle carrying a declined change would be
    # a trap.
    document = Document.parse(before, path=original.path)
    fallbacks = document.fm.fallbacks
    changes: list[MigrationChange] = []
    unmigrated: list[Unmigrated] = []

    _migrate_timestamp(document, fallbacks=fallbacks, actor=actor, changes=changes, unmigrated=unmigrated)
    _migrate_citations(document, fallbacks=fallbacks, changes=changes, unmigrated=unmigrated)

    after = document.serialize()
    if after == before and not unmigrated:
        return None
    return Migration(path, before, after, tuple(changes), tuple(unmigrated))


def migrate(
    bundle: Bundle,
    *,
    actor: str = DEFAULT_ACTOR,
    dry_run: bool = True,
) -> tuple[Migration, ...]:
    """Rewrite every v0.1 construct in *bundle* into its v0.2 form.

    A bundle is the unit that gets migrated; a half-migrated bundle is not a
    state anyone wants, so there is deliberately no document-level public
    entry point.

    **``dry_run`` defaults to ``True``**, matching both existing writers:
    the default call plans, and only an explicit ``dry_run=False`` touches the
    disk. Unchanged results are never rewritten.

    *actor* fills ``generated.by``, which v0.1's ``timestamp`` cannot supply.
    An *actor* that ``models.parse_actor`` classifies ``unknown`` raises
    ``ValueError``: that is a caller error rather than bundle content -- the
    §11 tolerance rule governs content -- and it matches ``index.update()``
    raising for a directory id the bundle does not contain. Writing an
    unrecognized actor would also manufacture a ``trust.actor-convention``
    warning on every document it touched.

    Results come back sorted by path and carry **only** concepts that changed
    or that the rewriter declined something on. A clean v0.2 bundle returns
    ``()`` rather than one no-op result per concept.

    ``index.md`` and ``log.md`` are not concepts and are never seen; neither
    are members excluded by ``load_bundle(ignore=)``.
    """
    by = parse_actor(actor)
    if by is None or by.kind == "unknown":
        raise ValueError(f"{actor!r} matches none of `human:`, `process:` or `<producer>/<version>` (§7)")

    results = [
        result
        for concept_id, document in sorted(bundle.concepts.items())
        if (result := _migrate_one(concept_id, document, actor=actor)) is not None
    ]
    results.sort(key=lambda result: result.path)

    if not dry_run:
        for result in results:
            if result.changed:
                (bundle.root / result.path).write_bytes(result.after.encode("utf-8"))
    return tuple(results)
