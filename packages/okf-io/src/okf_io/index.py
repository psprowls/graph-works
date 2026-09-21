"""Reconciling ``index.md`` against the bundle (OKF v0.2 §8).

**okf-io owns which entries appear. The human owns what they say.**

An index file is not derivable from the bundle. Comparing the vendored
fixtures against the concepts they list, roughly half the text in a real index
exists nowhere else: subdirectory summaries ("BigQuery tables the bundle
grounds against") appear in no frontmatter anywhere, asset entries describe a
``.py`` file that carries no frontmatter at all, and ``acme_retail``'s curated
one-liners deliberately differ from their concepts' ``description``. A writer
that rendered the body from the bundle would not risk destroying that text --
it would destroy it on every run, by construction.

What *is* fully derivable is which entries belong there. So regeneration is
reconciliation: an entry whose target resolves to nothing is pruned, a member
with no entry is added, and every other byte is copied through. Nothing is
rewritten under the default ``descriptions="preserve"``; text that has drifted
from its concept's ``description`` is reported instead.
"""

from __future__ import annotations

import bisect
import difflib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import unquote

from okf_io import _edit
from okf_io._md import BodyIndex, Heading, ListItem, parse_body
from okf_io.bundle import INDEX_NAME, LOG_NAME, Bundle
from okf_io.document import Document, rendered_with_body
from okf_io.links import parse_destination, resolve_path

EntryKind = Literal["concept", "subdirectory", "asset"]
ChangeKind = Literal["add", "remove", "refresh"]
Descriptions = Literal["preserve", "refresh"]

#: The separator between an entry's link and its description, per §8's example.
_SEPARATOR = " - "

#: An entry's leading link markdown. A label containing ``]`` or a destination
#: containing ``)`` is legal markdown this cannot cut cleanly; such an entry is
#: reported unreadable and then left strictly alone -- never refreshed, never
#: reported as drift. The destination group is a fast-path guess only: a
#: destination containing ``)`` (``a(1).md``) makes ``[^)]*`` stop early, and
#: a link title (``[x](url "title")``) is swallowed whole. ``_read_entry``
#: verifies the guess against markdown-it's own parse before trusting it.
_LINK_PREFIX_RE = re.compile(r"[ \t]*!?\[[^\]]*\]\(([^)]*)\)")

#: What separates a link from its description, in any of the forms the corpus
#: uses. Anchored, so a colon inside the description itself is untouched.
_TEXT_SEPARATOR_RE = re.compile(r"^[ \t]*[-–—:][ \t]*")  # noqa: RUF001 -- en/em dash intentional

#: The lead of a rendered bullet -- marker and indent -- taken from the source
#: line so a refresh reproduces the file's own shape.
_BULLET_LEAD_RE = re.compile(r"^[ \t]*[*+\-][ \t]+")


@dataclass(frozen=True, slots=True)
class EntryTarget:
    """What an entry points at. The argument to a ``describe=`` hook.

    ``path`` is bundle-relative posix: the file for a concept or an asset, the
    directory id for a subdirectory. ``document`` is the concept itself for a
    concept, the subdirectory's own ``index.md`` when it has one, and ``None``
    for an asset -- which carries no frontmatter to read.

    For a ``subdirectory``, that ``index.md`` carries no frontmatter in this
    corpus, so a ``describe=`` hook will find no ``title``/``description``
    there to read; the curated one-liner for a subdirectory lives only in the
    parent's bullet text, which is never rewritten under the default
    ``descriptions="preserve"``.
    """

    path: str
    kind: EntryKind
    document: Document | None


#: Extension point #4. Returning ``None`` means "no description".
Describe = Callable[[EntryTarget], str | None]


@dataclass(frozen=True, slots=True)
class IndexChange:
    """One entry added, removed, or rewritten.

    The design spec named ``add`` and ``remove``; ``refresh`` is the third
    because ``descriptions="refresh"`` reports every rewrite it makes.
    """

    kind: ChangeKind
    target: str  # bundle-relative posix, or the directory id for a subdirectory
    heading: str | None  # the heading the entry sits under, verbatim
    text: str  # the rendered bullet, or the removed line for a removal
    line: int | None  # 1-based body line; None for an addition


@dataclass(frozen=True, slots=True)
class Drift:
    """An entry whose text no longer matches its concept's ``description``."""

    target: str
    text: str  # what the index says today
    description: str  # what the concept's frontmatter says


@dataclass(frozen=True, slots=True)
class IndexUpdate:
    """What one directory's index would become.

    ``changed`` compares and renders nothing; ``diff()`` renders on demand and
    writes nothing. Asking whether anything changed never produces a diff, and
    producing a diff never implies a write -- okf-schema conflates the two, and
    this is the un-conflation.
    """

    path: str
    before: str
    after: str
    changes: tuple[IndexChange, ...]
    drift: tuple[Drift, ...]
    created: bool

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


@dataclass(frozen=True, slots=True)
class _Entry:
    """One parsed index entry.

    ``prefix`` is the item's leading link markdown, verbatim, so a refresh
    reproduces the destination exactly as written rather than re-emitting
    markdown-it's percent-encoded normalization of it. ``None`` means the
    entry could not be cut cleanly and must be left alone.
    """

    item: ListItem
    target: str
    prefix: str | None
    text: str | None


@dataclass(frozen=True, slots=True)
class _Plan:
    """What one directory's index needs, before anything is rendered.

    A list item carrying no link is invisible here -- neither an entry, nor
    dead, nor missing. Deliberate: most such bullets are plain notes, not
    entries. The cost is that a bullet that lost its link to a bad edit is
    silently dropped from consideration rather than flagged.
    """

    path: str
    directory: str
    document: Document | None
    entries: tuple[_Entry, ...]
    dead: tuple[_Entry, ...]
    missing: tuple[EntryTarget, ...]


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One index list item that begins with a link.

    ``href`` is the destination exactly as the source writes it -- not
    markdown-it's percent-encoded normalization -- so a reader resolves what
    the author wrote. ``label`` is the link text; ``text`` is what follows
    the link with its separator stripped, or ``None``.
    """

    href: str
    label: str | None
    text: str | None


@dataclass(frozen=True, slots=True)
class IndexHeading:
    """One heading and the entries between it and the next heading of any level."""

    level: int
    text: str
    entries: tuple[IndexEntry, ...]


@dataclass(frozen=True, slots=True)
class IndexOutline:
    """An index body's headings in file order. Read-only: nothing here writes."""

    headings: tuple[IndexHeading, ...]


def _linked_items(parsed: BodyIndex) -> tuple[ListItem, ...]:
    """Every list item carrying a link: the candidates `_plan` and `outline` both cut.

    Takes an already-parsed *parsed* rather than a body string, so a caller
    holding one parse (`parse_body` is cached, but a caller may also be
    reusing headings from the same `BodyIndex`) never triggers a second one.
    """
    return tuple(item for item in parsed.list_items if item.link_target is not None)


def outline(body: str) -> IndexOutline:
    """The headings of an index *body* (frontmatter already stripped) and
    the entries under each.

    Pure: no bundle, no file I/O. Parses *body* exactly once. An entry is a
    list item whose first inline content is a link that `_read_entry` can cut
    cleanly -- the same test `update` applies, over the same markdown-it
    parse, so one text yields the same entries to both. A heading inside a
    blockquote is not a heading; one inside a code fence never parses as one.
    Items above the first heading belong to no heading and are dropped. An
    entry belongs to the nearest heading above it, whatever that heading's
    level.
    """
    if not body:
        return IndexOutline(headings=())
    parsed = parse_body(body)
    headings = [heading for heading in parsed.headings if not heading.quoted]
    lines = [heading.line for heading in headings]
    grouped: list[list[IndexEntry]] = [[] for _ in headings]
    for item in _linked_items(parsed):
        owner = bisect.bisect_left(lines, item.line) - 1
        if owner < 0:
            continue
        entry = _read_entry(item, "")
        if entry.prefix is None:
            continue
        match = _LINK_PREFIX_RE.match(entry.prefix)
        assert match is not None  # `_read_entry` produced the prefix from this same pattern
        grouped[owner].append(IndexEntry(href=match.group(1), label=item.link_label, text=entry.text))
    return IndexOutline(
        headings=tuple(
            IndexHeading(level=heading.level, text=heading.text, entries=tuple(entries))
            for heading, entries in zip(headings, grouped, strict=True)
        )
    )


def _parent_of(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent


def _index_path(directory: str) -> str:
    return f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME


def _member_paths(bundle: Bundle) -> tuple[str, ...]:
    paths = [f"{concept_id}.md" for concept_id in bundle.concepts]
    paths += [_index_path(directory) for directory in bundle.indexes]
    paths += [f"{d}/{LOG_NAME}" if d else LOG_NAME for d in bundle.logs]
    paths += list(bundle.assets)
    paths += list(bundle.ignored)
    return tuple(sorted(paths))


def _directories(bundle: Bundle) -> frozenset[str]:
    """Every directory the bundle contains, including ``""`` and intermediates.

    Derived from member paths rather than from the filesystem: the bundle was
    walked once and is the model, and a second walk could disagree with it.
    """
    found = {""}
    for path in _member_paths(bundle):
        parts = path.split("/")[:-1]
        for depth in range(len(parts)):
            found.add("/".join(parts[: depth + 1]))
    return frozenset(found)


def _concepts_in(bundle: Bundle, directory: str) -> tuple[str, ...]:
    """Concept ids whose file sits directly in *directory*."""
    return tuple(sorted(cid for cid in bundle.concepts if _parent_of(f"{cid}.md") == directory))


def _subdirectories_of(directories: frozenset[str], directory: str) -> tuple[str, ...]:
    """Direct child directories of *directory* eligible to be proposed as new index entries.

    A dot-directory is excluded here even though root-scoped exclusion (§ADR 2026-08-21-bundle-dot-exclusion) makes it
    a real bundle member: it is machine-managed content nested in the tree, not something a
    human curates a subdirectory entry for. An existing hand-written entry that names a page
    inside one is still honored -- this only stops the directory itself from being
    auto-proposed as a *new* entry.
    """
    prefix = f"{directory}/" if directory else ""
    return tuple(
        sorted(
            found
            for found in directories
            if found
            and found.startswith(prefix)
            and "/" not in found[len(prefix) :]
            and not PurePosixPath(found).name.startswith(".")
        )
    )


def _in_dot_subtree(directory: str) -> bool:
    """Whether *directory* is, or sits under, a dot-directory.

    The **any-segment** test, where :func:`_subdirectories_of` uses a
    leaf-only one. The two are not inconsistent: that function is already
    scoped to one parent, so a caller who named ``.agents`` outright is
    entitled to see its children proposed. This one runs over the whole
    bundle at once, where ``.agents/skills`` is reachable only through the
    ``.agents`` that was already declined -- so admitting it would create an
    index nothing can ever link to.
    """
    return any(segment.startswith(".") for segment in directory.split("/") if segment)


def _has_content(bundle: Bundle, subdirectory: str) -> bool:
    """Whether *subdirectory* carries anything okf-io can stand behind, at or
    below it: a concept, an ``index.md``, or a ``log.md``.

    Assets and ``ignore=``d members don't count -- the same rule
    :func:`_members_of` already applies to a single asset applies to a whole
    directory of them: a directory holding nothing but excluded or untitled
    content is not something okf-io can name, so it must not be proposed.
    "At or below" rather than "directly in": a subdirectory that is itself
    empty but shelters a concept two levels down still has something to name.
    """
    prefix = f"{subdirectory}/"
    if any(cid.startswith(prefix) for cid in bundle.concepts):
        return True
    if any(d == subdirectory or d.startswith(prefix) for d in bundle.indexes):
        return True
    return any(d == subdirectory or d.startswith(prefix) for d in bundle.logs)


def _members_of(bundle: Bundle, directories: frozenset[str], directory: str) -> tuple[EntryTarget, ...]:
    """The members of *directory* that may be added. Assets are never among them.

    okf-io can name a concept (its ``title``) and a directory (its own name).
    For ``sql_equality.py`` it has neither a title nor a description, and
    emitting ``* [sql_equality.py](sql_equality.py)`` would be noise a human
    must then rewrite. Existing asset entries are honoured and pruned when
    dead; they are simply never created.
    """
    out = [EntryTarget(f"{cid}.md", "concept", bundle.concepts[cid]) for cid in _concepts_in(bundle, directory)]
    out += [
        EntryTarget(sub, "subdirectory", bundle.indexes.get(sub))
        for sub in _subdirectories_of(directories, directory)
        if _has_content(bundle, sub)
    ]
    return tuple(out)


def _raw_target(bundle: Bundle, directories: frozenset[str], target: str) -> str | None:
    """The canonical, disk-raw form of *target*, or ``None`` when it names
    nothing in the bundle.

    A directory counts, whether or not it carries an ``index.md``. An entry
    reading ``[concepts](concepts/index.md)`` for a directory with no index is
    a broken *link*, which ``links.broken`` already reports -- but it is not a
    dead *entry*: pruning it would only make the next reconcile add the same
    directory straight back under a different destination.

    *target* arrives from an entry's written destination (§8), which -- like
    any other reference -- may name a non-ASCII member in a different
    Unicode normalization form than the id the walk derived from disk.
    Routing through ``bundle.member_id`` is what keeps a canonically-matching
    entry from reading as "missing" and getting duplicated.
    """
    if target in directories:
        return target
    if target.endswith(f"/{INDEX_NAME}") and _parent_of(target) in directories:
        return target
    return bundle.member_id(target)


def _alive(bundle: Bundle, directories: frozenset[str], target: str) -> bool:
    """Whether *target* still names something in the bundle."""
    return _raw_target(bundle, directories, target) is not None


def _covers(covered: frozenset[str], target: EntryTarget) -> bool:
    if target.kind == "subdirectory":
        return target.path in covered or f"{target.path}/{INDEX_NAME}" in covered
    return target.path in covered


def _resolve(destination: str, *, source: str) -> str | None:
    """The bundle path an entry destination names, or ``None`` when it is not
    an entry at all.

    Reuses the graph's own rules, so the two can never disagree about what a
    destination means: an external destination (§6.1) and a fragment-only
    anchor are screened out, and both §6.1 path forms resolve.
    """
    path_part, _fragment, external = parse_destination(destination)
    if external or not path_part:
        return None
    return resolve_path(path_part, source_id=source)


def _read_entry(item: ListItem, target: str) -> _Entry:
    """Cut an item into its link and the text that follows it.

    The regex is a fast path only. Its captured destination is cross-checked
    against ``item.link_target`` -- markdown-it's own parse, which handles a
    parenthesis inside the destination and a link title correctly -- and the
    entry is declared unreadable rather than trusted on any mismatch. Both
    sides are percent-decoded before comparison: markdown-it percent-encodes
    destinations on the way out (``./café.md`` becomes ``./caf%C3%A9.md``),
    while the regex's capture is the undecoded source text, so comparing the
    raw forms would flag every non-ASCII destination as unreadable.
    """
    match = _LINK_PREFIX_RE.match(item.text)
    if match is None or "\n" in item.text[: match.end()]:
        return _Entry(item=item, target=target, prefix=None, text=None)
    raw_target = item.link_target
    if raw_target is None or unquote(match.group(1)) != unquote(raw_target):
        return _Entry(item=item, target=target, prefix=None, text=None)
    rest = _TEXT_SEPARATOR_RE.sub("", item.text[match.end() :])
    return _Entry(
        item=item,
        target=target,
        prefix=item.text[: match.end()].strip(),
        text=rest.strip() or None,
    )


def _plan(bundle: Bundle, directory: str, directories: frozenset[str]) -> _Plan:
    """Everything reconciling *directory* needs to know, and nothing rendered."""
    document = bundle.indexes.get(directory)
    body = document.body if document is not None else ""
    items = _linked_items(parse_body(body)) if body else ()

    entries: list[_Entry] = []
    dead: list[_Entry] = []
    covered: set[str] = set()
    for item in items:
        assert item.link_target is not None  # `_linked_items` admits only linked items
        target = _resolve(item.link_target, source=_index_path(directory))
        if target is None:
            continue
        entry = _read_entry(item, target)
        entries.append(entry)
        raw = _raw_target(bundle, directories, target)
        if raw is not None:
            covered.add(raw)
        else:
            dead.append(entry)

    frozen = frozenset(covered)
    missing = tuple(target for target in _members_of(bundle, directories, directory) if not _covers(frozen, target))
    return _Plan(
        path=_index_path(directory),
        directory=directory,
        document=document,
        entries=tuple(entries),
        dead=tuple(dead),
        missing=missing,
    )


def _label_of(target: EntryTarget) -> str:
    """The link label: a concept's ``title``, else the member's own name."""
    if target.kind == "concept" and target.document is not None:
        title = (target.document.fm.title or "").strip()
        if title:
            return title
    return PurePosixPath(target.path).name


def _destination_of(bundle: Bundle, target: EntryTarget) -> str:
    """The destination to write, file-relative as every fixture index writes it.

    §8's example links a subdirectory to its ``index.md``; a directory that has
    none is linked as a directory instead, which is the only honest thing to
    point at.
    """
    name = PurePosixPath(target.path).name
    if target.kind != "subdirectory":
        return name
    return f"{name}/{INDEX_NAME}" if target.path in bundle.indexes else f"{name}/"


def _description_of(target: EntryTarget, describe: Describe | None) -> str | None:
    """What a new or refreshed entry says.

    ``describe`` is authoritative when supplied, its ``None`` included:
    extension point #4 exists so a caller can drive index text from somewhere
    okf-io knows nothing about, and second-guessing its answer would defeat
    that.
    """
    if describe is not None:
        return describe(target)
    if target.kind == "concept" and target.document is not None:
        return (target.document.fm.description or "").strip() or None
    return None


def _render(marker: str, label: str, destination: str, description: str | None) -> str:
    if description:
        return f"{marker} [{label}]({destination}){_SEPARATOR}{description}"
    return f"{marker} [{label}]({destination})"


def _type_of(bundle: Bundle, target: str) -> str | None:
    """The ``type`` of the concept an entry points at, if it is one."""
    if not target.endswith(".md"):
        return None
    raw = bundle.member_id(target)
    if raw is None:
        return None
    document = bundle.concepts.get(raw[: -len(".md")])
    if document is None:
        return None
    return (document.fm.type or "").strip() or None


def _is_subdirectory_entry(directories: frozenset[str], target: str) -> bool:
    return target in directories or (target.endswith(f"/{INDEX_NAME}") and _parent_of(target) in directories)


def _new_section_title(bundle: Bundle, target: EntryTarget) -> str:
    if target.kind == "subdirectory":
        return "Subdirectories"
    concept_type = _type_of(bundle, target.path)
    return concept_type if concept_type else "Concepts"


def _sibling_heading(
    bundle: Bundle,
    directories: frozenset[str],
    entries: Sequence[_Entry],
    target: EntryTarget,
) -> tuple[bool, str | None]:
    """(found, key): the heading sibling entries already sit under, per §5.2.

    ``key`` is a casefolded heading text, matching ``ListItem.heading``, and is
    ``None`` when the siblings sit under no heading at all -- which is why the
    "found" flag is separate from it. The *first* such entry decides, so a
    file whose entries are split across headings keeps its first convention.
    """
    if target.kind == "subdirectory":
        for entry in entries:
            if _is_subdirectory_entry(directories, entry.target):
                return True, entry.item.heading
        return False, None
    wanted = _type_of(bundle, target.path)
    for entry in entries:
        if _type_of(bundle, entry.target) == wanted:
            return True, entry.item.heading
    return False, None


def _anchor_for(body: str, items: Sequence[ListItem], headings: Sequence[Heading], key: str | None) -> int | None:
    """The 1-based line to insert after, for the section keyed *key*.

    The last surviving item of the section, else the line just past the
    heading's own blank-line convention, else ``None`` -- there is no such
    section and one must be created.

    A heading with no *surviving* item under it -- its list was pruned to
    nothing, or it never had one -- still owns the blank line that separates
    it from whatever follows. Anchoring on the heading's own line would glue
    a new bullet straight onto it and strand that blank line at the end of
    the section instead; anchoring past the blank run keeps the bullet where
    the (pruned) list used to sit.
    """
    under = [item for item in items if item.heading == key]
    if under:
        return max(item.end for item in under)
    if key is None:
        return None
    heading = next((h for h in headings if h.text.casefold() == key), None)
    if heading is None:
        return None
    anchor = heading.line
    total = _edit.line_count(body)
    while anchor < total and not _edit.line_at(body, anchor + 1).strip():
        anchor += 1
    return anchor


def _last_surviving_line(body: str, dead: Sequence[_Entry]) -> int:
    """The last physical line the queued removals in *dead* will leave standing.

    ``_edit.line_count(body)`` answers "how long is the file", not "what will
    its last line be once *dead*'s edits land" -- and anchoring a brand-new
    section on the former, while a removal also covers the file's actual
    tail, either collides with it (no trailing newline) or leaves a silently
    doubled blank line behind (there is one). ``0`` means every line will be
    gone.
    """
    removed: set[int] = set()
    for entry in dead:
        removed.update(range(entry.item.line, entry.item.end + 1))
    line = _edit.line_count(body)
    while line > 0 and line in removed:
        line -= 1
    return line


def _insert_after(body: str, line: int, lines: Sequence[str], refreshed_lines: frozenset[int]) -> _edit.Edit:
    """``_edit.insert_after``, aware that a refresh may already terminate *line*.

    ``insert_after``'s own no-trailing-newline correction reads the
    *original* body: when *line* is both the file's literal last line
    (lacking a terminator) and the anchor for an insertion, it rewrites that
    same ``[line, line]`` range to add one. But when a refresh edit is
    rewriting that very line too -- its replacement always ends with
    ``newline`` -- the correction's rewrite lands on the identical range the
    refresh edit already occupies, and ``_edit.apply`` rejects the overlap.
    A plain insertion is correct instead: the refresh already guarantees the
    terminator ``insert_after`` would otherwise have added itself. Elsewhere
    -- *line* not refreshed, or not the body's last line -- this is exactly
    what ``insert_after`` already returns, so the override never changes
    behaviour outside the one colliding case.
    """
    if line and line in refreshed_lines:
        return _edit.Edit(line + 1, line, tuple(lines))
    return _edit.insert_after(body, line, lines)


def _update_one(
    bundle: Bundle,
    directories: frozenset[str],
    directory: str,
    *,
    descriptions: Descriptions,
    describe: Describe | None,
) -> IndexUpdate:
    plan = _plan(bundle, directory, directories)
    document = plan.document
    body = document.body if document is not None else ""
    parsed = parse_body(body) if body else None
    items = parsed.list_items if parsed is not None else ()
    headings = parsed.headings if parsed is not None else ()
    marker = _edit.bullet_marker(body, items, "*")
    newline = _edit.newline_of(body)
    heading_level = headings[0].level if headings else 1

    removed = {entry.item.line for entry in plan.dead}
    surviving = [item for item in items if item.line not in removed]

    refreshed, drift = _refresh_and_drift(
        bundle,
        directories,
        plan,
        body,
        newline,
        headings,
        descriptions=descriptions,
        describe=describe,
    )
    # A refresh edit's replacement line always ends with `newline` (its
    # `lines` tuple is `(f"{bullet}{newline}",)`), so any line it rewrites is
    # guaranteed a trailing terminator once applied -- computed up front so
    # the insertions below can see it.
    refreshed_lines = frozenset(edit.start for _, edit in refreshed)

    changes: list[IndexChange] = []
    edits: list[_edit.Edit] = []

    for entry in plan.dead:
        changes.append(
            IndexChange(
                kind="remove",
                target=entry.target,
                heading=_heading_text(headings, entry.item.heading),
                text=_edit.line_at(body, entry.item.line).rstrip("\r\n"),
                line=entry.item.line,
            )
        )
        edits.append(_edit.Edit(entry.item.line, entry.item.end))

    # Additions, grouped so that every entry joining one section becomes a
    # single insertion at that section's anchor. Order follows `missing`:
    # concepts sorted, then subdirectories sorted.
    by_anchor: dict[int, list[str]] = {}
    new_sections: list[tuple[str, list[str]]] = []
    for target in plan.missing:
        found, key = _sibling_heading(bundle, directories, plan.entries, target)
        title = None if found else _new_section_title(bundle, target)
        if title is not None:
            key = title.casefold()
        anchor = _anchor_for(body, surviving, headings, key)
        bullet = _render(
            marker,
            _label_of(target),
            _destination_of(bundle, target),
            _description_of(target, describe),
        )
        if anchor is None:
            # The siblings that named this section were all pruned, or there
            # were none: the section has to be created after all.
            title = title or _new_section_title(bundle, target)
            section = next((s for s in new_sections if s[0] == title), None)
            if section is None:
                new_sections.append((title, [bullet]))
            else:
                section[1].append(bullet)
            heading_text: str | None = title
        else:
            by_anchor.setdefault(anchor, []).append(bullet)
            heading_text = _heading_text(headings, key)
        changes.append(IndexChange(kind="add", target=target.path, heading=heading_text, text=bullet, line=None))

    for anchor, bullets in by_anchor.items():
        edits.append(_insert_after(body, anchor, [f"{bullet}{newline}" for bullet in bullets], refreshed_lines))

    if new_sections:
        tail = _last_surviving_line(body, plan.dead)
        edits.append(
            _insert_after(
                body,
                tail,
                _new_section_lines(body, new_sections, heading_level, newline, tail),
                refreshed_lines,
            )
        )

    for change, edit in refreshed:
        changes.append(change)
        edits.append(edit)

    new_body = _edit.apply(body, edits)
    if document is None:
        before, after = "", new_body
    else:
        before, after = document.raw_text, rendered_with_body(document, new_body)
    return IndexUpdate(
        path=plan.path,
        before=before,
        after=after,
        changes=tuple(changes),
        drift=drift,
        created=document is None,
    )


def _heading_text(headings: Sequence[Heading], key: str | None) -> str | None:
    """The heading's text as written, from the casefolded key items carry."""
    if key is None:
        return None
    heading = next((h for h in headings if h.text.casefold() == key), None)
    return heading.text if heading is not None else None


def _new_section_lines(
    body: str, sections: Sequence[tuple[str, list[str]]], level: int, newline: str, tail: int
) -> list[str]:
    """The rendered blocks for sections the file does not have yet.

    *tail* is the last line that will actually survive the queued removals
    (:func:`_last_surviving_line`), not the body's raw last line -- once a
    removal has emptied the real tail, only the survivor's blankness (or its
    absence, at ``tail == 0``, meaning nothing will survive) should decide
    whether a separator is needed.
    """
    lines: list[str] = []
    tail_is_blank = tail == 0 or not _edit.line_at(body, tail).strip()
    for position, (title, bullets) in enumerate(sections):
        if tail and (position > 0 or not tail_is_blank):
            lines.append(newline)
        lines.append(f"{'#' * level} {title}{newline}")
        lines.append(newline)
        lines.extend(f"{bullet}{newline}" for bullet in bullets)
    return lines


def update(
    bundle: Bundle,
    *,
    directories: Sequence[str] | None = None,
    descriptions: Descriptions = "preserve",
    describe: Describe | None = None,
    create_missing: bool = False,
    dry_run: bool = True,
) -> tuple[IndexUpdate, ...]:
    """Reconcile each directory's ``index.md`` against *bundle*.

    *directories* selects directory ids (``""`` for the bundle root); ``None``
    means every directory the bundle contains. Results come back sorted by
    path, one per directory considered -- a directory with no index is
    considered only when *create_missing* is set.

    **``dry_run`` defaults to ``True``.** Writing is something a caller asks
    for. That is what makes dry-run first-class structurally rather than by
    courtesy: the default call plans, and only an explicit ``dry_run=False``
    touches the disk. Unchanged files are never rewritten.

    **``create_missing`` defaults to ``False``.** §8 says an index MAY appear
    in any directory; the writer never conjures one into a directory that
    chose not to have one.

    ``descriptions="preserve"`` never touches existing entry text and reports
    what has drifted; ``"refresh"`` opts into the machine-generated regime and
    rewrites it, reporting every rewrite as a change. ``describe`` is
    extension point #4.

    Raises ``ValueError`` for a directory id the bundle does not contain --
    a caller error, not bundle content, and the tolerance rule (§11) is about
    content.
    """
    known = _directories(bundle)
    wanted = sorted(known) if directories is None else sorted(set(directories))
    results: list[IndexUpdate] = []
    for directory in wanted:
        if directory not in known:
            raise ValueError(f"{directory!r} is not a directory of this bundle")
        if directory not in bundle.indexes:
            if not create_missing:
                continue
            # An index that already exists inside a dot-subtree is reconciled
            # like any other, above -- what is declined here is *conjuring* one
            # into a directory nobody asked for by name. `_subdirectories_of`
            # will not propose the dot-directory as an entry in its parent, so
            # an index created here could never be linked from anywhere: an
            # orphan by construction. Naming the directory explicitly is still
            # honored, symmetrically with `create_missing` itself.
            if directories is None and _in_dot_subtree(directory):
                continue
        results.append(
            _update_one(
                bundle,
                known,
                directory,
                descriptions=descriptions,
                describe=describe,
            )
        )
    results.sort(key=lambda result: result.path)

    if not dry_run:
        for result in results:
            if not result.changed:
                continue
            target = bundle.root / result.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(result.after.encode("utf-8"))
    return tuple(results)


def _entry_target(bundle: Bundle, directories: frozenset[str], target: str) -> EntryTarget | None:
    """The :class:`EntryTarget` an existing entry points at, if it is a member.

    *target* is the entry's written destination, decoded but not otherwise
    normalized; every lookup below routes through ``bundle.member_id`` so a
    non-ASCII member matches regardless of which Unicode form the entry text
    happens to use, and the ``EntryTarget`` it returns always carries the
    *raw* disk id -- the id a caller can actually open or re-key a mapping
    with.
    """
    if _is_subdirectory_entry(directories, target):
        path = _parent_of(target) if target.endswith(f"/{INDEX_NAME}") else target
        return EntryTarget(path, "subdirectory", bundle.indexes.get(path))
    if target.endswith(".md"):
        raw = bundle.member_id(target)
        if raw is not None:
            document = bundle.concepts.get(raw[: -len(".md")])
            if document is not None:
                return EntryTarget(raw, "concept", document)
        return None
    raw_asset = bundle.member_id(target)
    if raw_asset is not None and raw_asset in bundle.assets:
        return EntryTarget(raw_asset, "asset", None)
    return None


def _refresh_and_drift(
    bundle: Bundle,
    directories: frozenset[str],
    plan: _Plan,
    body: str,
    newline: str,
    headings: Sequence[Heading],
    *,
    descriptions: Descriptions,
    describe: Describe | None,
) -> tuple[tuple[tuple[IndexChange, _edit.Edit], ...], tuple[Drift, ...]]:
    """Entry rewrites under ``refresh``, and drift under ``preserve``.

    The two are the same comparison read two ways. Under ``preserve`` a
    difference is reported and nothing is written; under ``refresh`` the same
    difference becomes a change and ``drift`` is empty. An entry whose text
    could not be read cleanly, or whose text wraps onto a second physical
    line, takes part in neither: it is left strictly alone. A wrapped entry
    could in principle be rewritten while preserving its continuation, but
    deciding how is a rendering problem this module does not have a rule
    for, and guessing risks losing the continuation outright -- the same
    conservative call as an entry the regex cannot cut cleanly.

    ``describe`` is consulted only under ``refresh``. §6.1 calls it "for every
    entry about to be added, and for every entry under ``refresh``" -- so under
    ``preserve``, drift is measured against the concept's own ``description``,
    which is the thing drift is *about*. Measuring it against a hook's output
    would report every entry in a bundle whose hook says something else.
    """
    dead = {entry.item.line for entry in plan.dead}
    hook = describe if descriptions == "refresh" else None
    rewrites: list[tuple[IndexChange, _edit.Edit]] = []
    drift: list[Drift] = []

    for entry in plan.entries:
        if entry.prefix is None or entry.item.line in dead:
            continue
        if entry.item.end != entry.item.line:
            continue
        target = _entry_target(bundle, directories, entry.target)
        if target is None:
            continue
        description = _description_of(target, hook)
        if description is None or description == (entry.text or ""):
            continue
        if descriptions == "preserve":
            drift.append(Drift(entry.target, entry.text or "", description))
            continue
        lead = _BULLET_LEAD_RE.match(_edit.line_at(body, entry.item.line))
        lead_text = lead.group(0) if lead else ""
        # Falsy handling matches `_render`'s: a hook that answers "" means
        # "no description", not "an empty sentence with a dangling ` - `".
        bullet = f"{lead_text}{entry.prefix}{_SEPARATOR}{description}" if description else f"{lead_text}{entry.prefix}"
        rewrites.append(
            (
                IndexChange(
                    kind="refresh",
                    # A refresh does not move an entry, so its heading is the
                    # one it already sits under -- verbatim, matching what
                    # `add` and `remove` report.
                    target=entry.target,
                    heading=_heading_text(headings, entry.item.heading),
                    text=bullet,
                    line=entry.item.line,
                ),
                _edit.Edit(entry.item.line, entry.item.end, (f"{bullet}{newline}",)),
            )
        )
    return tuple(rewrites), tuple(drift)
