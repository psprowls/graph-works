"""The four planners, over one batch engine. Pure reads: nothing here writes.

**Why a batch, and not one member per plan.** Three reasons, in increasing
severity. A directory rename would otherwise be N plans and N applies -- N
full inbound traversals. Those N plans go stale against each other: plan 2 was
computed against a bundle plan 1 has already changed. And a relative reference
**between two members that both move** cannot be computed correctly at all
without both endpoints known in one pass, because its new form depends on both
the new base and the new target. That last one is not an optimisation; a
per-member plan gets it wrong.

**Repair by resolved identity, not by string equality.** `Link.raw` is
markdown-it's *normalized* destination -- `./café.md` arrives as
`./caf%C3%A9.md` -- so no reference can be repaired by string-matching a graph
destination against file text. Instead every candidate found in the text is
pushed through `parse_destination` + `resolve_path` (and `resolve_reference`
for frontmatter values, added in Task 5), and **rewritten only when the
resolved target is a member being moved**. Percent-encoding, `<...>`
destinations, `./` prefixes, root-absolute versus file-relative forms and
`#fragment` suffixes all stop being special cases, because resolution already
handles each of them and the comparison happens after it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from urllib.parse import quote

from okf_io import Bundle, Document, build_link_graph
from okf_io.bundle import INDEX_NAME, LOG_NAME, canonical_id
from okf_io.links import is_external, parse_destination, resolve_path, resolve_reference

from okf_ext.moves import locate
from okf_ext.moves.model import Move, MovePlan, RefEdit, Refusal, Unrebased
from okf_ext.writing import body_digest

#: The §6.2 path-valued frontmatter keys, as dotted paths. A fixed, documented
#: set -- resolved-identity matching is what lets it be generous without
#: needing configuration. `sources[].resource` may be a §5.1 scope descriptor
#: and top-level `resource` may be a table URI; neither resolves to a moved
#: member, so neither is touched.
#:
#: **A caller-supplied `keys=` was rejected.** The vault pointers that would
#: want it are exactly the ones two surveys say should *become* `sources[]`
#: entries with a `resource`; extensibility here would entrench the shape it
#: exists to replace.
REFERENCE_KEYS = ("resource", "computation", "executor.resource", "attester.resource")

#: The list-valued key whose every element carries a `resource`.
SOURCES_KEY = "sources"


def _normalize(path: str) -> str | None:
    """Collapse `.` and `..` in a bundle-relative posix path, or None if it escapes.

    `PurePosixPath` will not do this -- it preserves `..` rather than resolving
    it -- which is why `okf_io.links._normalize` exists and why this mirrors it.
    """
    parts: list[str] = []
    for segment in path.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(segment)
    return "/".join(parts)


def _parent(member: str) -> str:
    """The directory a member sits in, `""` at the bundle root."""
    parent = PurePosixPath(member).parent.as_posix()
    return "" if parent == "." else parent


def _relative(from_dir: str, target: str) -> str:
    """A file-relative posix path from *from_dir* to *target*, with `../` climbs."""
    from_parts = [segment for segment in from_dir.split("/") if segment]
    to_parts = target.split("/")
    shared = 0
    while shared < len(from_parts) and shared < len(to_parts) - 1 and from_parts[shared] == to_parts[shared]:
        shared += 1
    parts = [".."] * (len(from_parts) - shared) + to_parts[shared:]
    return "/".join(parts)


def _rewrite_path(old: str, new_target: str, new_base: str, *, encode: bool) -> str:
    """The replacement text for a repaired or rebased path value. **Form is preserved.**

    Shared by both call sites -- a body destination (`_body_edits`) and a §6.2
    frontmatter value (the frontmatter walk below) -- because the path algebra
    is identical either way: a root-absolute reference stays root-absolute; a
    file-relative one is recomputed against *new_base*, keeping a leading `./`
    if it had one so the diff stays as small as the author's own style allows;
    and the `#fragment` suffix rides through untouched, because it was never
    part of the path.

    Only the encoding differs, which is why *encode* is a caller-supplied flag
    rather than something inferred here. A body destination is
    percent-encoded (with `/` safe, matching what `parse_destination`'s
    `unquote` reverses and what markdown-it would emit anyway) unless it was
    written `<...>`, whose angle brackets are exactly the syntax that lets a
    destination hold spaces and parens as-is. A §6.2 frontmatter value arrives
    undecoded -- `resolve_reference` skips the decode -- and must leave the
    same way, or it stops resolving; callers of this function pass
    `encode=False` for it and are expected to have `.strip()`-ed the raw YAML
    scalar first, since (unlike a located body span) it may carry incidental
    surrounding whitespace.
    """
    path_raw, separator, fragment = old.partition("#")
    if path_raw.startswith("/"):
        rewritten = "/" + new_target
    else:
        rewritten = _relative(new_base, new_target)
        if path_raw.startswith("./") and not rewritten.startswith("../"):
            rewritten = "./" + rewritten
    encoded = quote(rewritten, safe="/") if encode else rewritten
    return encoded + separator + fragment


def _validate(bundle: Bundle, mapping: Mapping[str, str], *, relocate: bool) -> tuple[list[Move], list[Refusal]]:
    """Turn a raw mapping into moves plus every refusal the mapping itself earns."""
    moves: list[Move] = []
    refusals: list[Refusal] = []
    claimed: dict[str, str] = {}

    for source, dest in sorted(mapping.items()):
        clean_source = _normalize(source)
        clean_dest = _normalize(dest)
        if clean_source is None or clean_dest is None:
            refusals.append(Refusal(source, "escapes-root", f"`{source}` -> `{dest}` resolves outside the bundle root"))
            continue
        if clean_source == clean_dest:
            refusals.append(Refusal(clean_source, "same-path", "source and destination are the same member"))
            continue
        # Gated on `relocate`, like `not-a-member`, `dest-exists` and
        # `reserved-dest` below and for the same reason: in repair mode the
        # mapping describes a move that already happened outside this
        # capability, and a reserved file is not exempt from that -- something
        # else can rename `index.md` into an ordinary concept just as easily
        # as it can rename any other member. Refusing it here would leave the
        # resulting dangling inbound references with no repair path at all,
        # since `plan_repair` is the only call that could fix them.
        if relocate and PurePosixPath(clean_source).name in {INDEX_NAME, LOG_NAME}:
            refusals.append(
                Refusal(clean_source, "reserved-source", "`index.md` and `log.md` are reserved and never move")
            )
            continue
        if clean_source.endswith(".md") != clean_dest.endswith(".md"):
            refusals.append(
                Refusal(
                    clean_source,
                    "kind-change",
                    f"`{clean_source}` -> `{clean_dest}` changes a concept into an asset or the reverse",
                )
            )
            continue
        # The destination-side twin of the `reserved-source` check above --
        # `reserved-source` refuses `index.md`/`log.md` moving *away*; this
        # refuses anything moving *onto* one, which is the same rule read
        # backwards: nothing may fabricate a reserved file. Left unguarded,
        # an ordinary concept could land at `some/dir/index.md` (`kind-change`
        # does not catch it -- both ends are still `.md`), and `update_index()`
        # would afterward reconcile it as a genuine index.
        #
        # Gated on `relocate`, like `dest-exists` just below and for the same
        # reason: in repair mode the destination names a path that already
        # exists on disk because the move it is repairing after already
        # happened. `plan_repair(bundle, {"a.md": "notes/index.md"})`
        # describes a state of the world -- however unusual -- rather than
        # proposing to create one, so there is nothing here for this check to
        # prevent.
        if relocate and PurePosixPath(clean_dest).name in {INDEX_NAME, LOG_NAME}:
            refusals.append(
                Refusal(
                    clean_source,
                    "reserved-dest",
                    f"`{clean_dest}` names a reserved file; a move cannot turn a concept into `index.md` or `log.md`",
                )
            )
            continue
        raw_source = clean_source
        if relocate:
            if not bundle.has_member(clean_source):
                refusals.append(Refusal(clean_source, "not-a-member", "not a member of this bundle"))
                continue
            # `bundle.member_id` is what keeps the rest of the engine on raw
            # disk ids: every downstream comparison against `bundle.concepts`
            # / `_members(bundle)` is keyed by what the walk found, and a
            # `clean_source` typed in a different Unicode normalization form
            # than the disk id would otherwise never match it again.
            raw_source = bundle.member_id(clean_source) or clean_source
        # `bundle.has_member(clean_dest)` is gated on `relocate`, symmetrically
        # with the `not-a-member` check on the source above -- and for the
        # same reason. `plan_repair` exists for exactly the situations where
        # the destination already exists on disk: the recovery path after a
        # partial `apply` (destinations commit before referrers, so a partial
        # failure always leaves the destination already written) and the
        # back-repair for a move nothing planned (the file already sits at
        # its new path by the time anyone calls `plan_repair`). In `relocate`
        # mode an existing destination is a genuine conflict -- something
        # else already lives there and this move would clobber it -- but in
        # repair mode it is the expected, unremarkable state, not a conflict.
        # The intra-batch duplicate check just below stays unconditional in
        # either mode: two sources mapping to the same destination is
        # ambiguous regardless of whether anything has actually moved yet.
        if relocate and bundle.has_member(clean_dest):
            refusals.append(Refusal(clean_source, "dest-exists", f"`{clean_dest}` already names a member"))
            continue
        if clean_dest in claimed:
            refusals.append(
                Refusal(
                    clean_source,
                    "dest-exists",
                    f"`{clean_dest}` is already claimed by `{claimed[clean_dest]}` in this batch",
                )
            )
            continue
        claimed[clean_dest] = clean_source
        moves.append(Move(source=raw_source, dest=clean_dest, is_asset=not raw_source.endswith(".md")))

    return moves, refusals


def _members(bundle: Bundle) -> dict[str, Document]:
    """Every markdown member by bundle-relative path -- concepts *and* reserved files.

    `LinkGraph` excludes `index.md` and `log.md` because "what cites this
    concept" is a question about prose. "What text must change when a path
    changes" is not the same question, so they are scanned here (spec §7.2).
    Without this pass every move loses hand-authored index entry text:
    `update_index()` reconciles by member set, so it would prune the dead entry
    and add a fresh, generated one.
    """
    found: dict[str, Document] = {f"{cid}.md": doc for cid, doc in bundle.concepts.items()}
    for directory, document in bundle.indexes.items():
        found[f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME] = document
    for directory, document in bundle.logs.items():
        found[f"{directory}/{LOG_NAME}" if directory else LOG_NAME] = document
    return found


def _body_edits(
    member: str,
    document: Document,
    *,
    destination_of: Mapping[str, str],
    new_base: str,
    rebase: bool,
    bundle: Bundle,
) -> tuple[list[RefEdit], list[Unrebased], int]:
    """Every body destination in *member* that must change, and what it becomes.

    Two reasons a destination changes, and they are counted differently. A
    reference **into the moved set** repoints at the target's new path; that is
    what the count reconciliation checks. A reference *out of* a member that is
    itself moving is **rebased** against its new location -- `resolve_path`
    resolves file-relative destinations against the containing file's directory
    -- and is not an inbound reference at all, so it must never inflate the
    inbound count. `RefEdit.target` is what keeps the two apart.

    The third return value, *located_into_moved*, is the count of candidates
    whose resolved target is in the moved set -- **counted whether or not the
    rewrite actually changes the text.** A directory move can carry two
    siblings to the same new directory together, in which case a relative
    reference between them re-resolves to exactly the bytes already on disk
    (`./beta.md` staying `./beta.md`), and no `RefEdit` is emitted for it,
    because there is nothing to rewrite. `LinkGraph` still counts that
    reference as one into the moved set, so the count reconciliation in
    `_plan_moves` has to compare against *located*, not against *changed* --
    otherwise every no-op sibling reference reads as a locator shortfall and a
    legitimate directory move refuses itself.
    """
    edits: list[RefEdit] = []
    unrebased: list[Unrebased] = []
    located_into_moved = 0
    for candidate in locate.destinations(document.body):
        path_part, _fragment, external = parse_destination(candidate.text)
        if external or not path_part:
            continue
        target = resolve_path(path_part, source_id=member)
        if target is None:
            if rebase and not path_part.startswith("/"):
                unrebased.append(
                    Unrebased(member=member, raw=candidate.text, detail="resolves outside the bundle root")
                )
            continue
        # `target` is content-derived -- it may name a non-ASCII member in a
        # different Unicode normalization form than the raw disk id
        # `destination_of` is keyed by *in relocate mode*. In repair mode
        # `destination_of` is keyed by the caller's own (unresolved) mapping,
        # because the source it names has already left the bundle -- so the
        # plain `target` is tried too, falling back to it whenever the raw
        # id either doesn't resolve or isn't the key that was used.
        raw_target = bundle.member_id(target)
        moved_to = destination_of.get(raw_target) if raw_target is not None else None
        if moved_to is None:
            moved_to = destination_of.get(target)
        if moved_to is not None:
            located_into_moved += 1
            new_text = _rewrite_path(candidate.text, moved_to, new_base, encode=not candidate.bracketed)
        elif rebase:
            if raw_target is None:
                if not path_part.startswith("/"):
                    unrebased.append(
                        Unrebased(member=member, raw=candidate.text, detail="target is not a bundle member")
                    )
                continue
            new_text = _rewrite_path(candidate.text, raw_target, new_base, encode=not candidate.bracketed)
        else:
            continue
        if new_text == candidate.text:
            continue
        edits.append(
            RefEdit(
                member=member,
                where="body",
                target=raw_target if raw_target is not None else target,
                old=candidate.text,
                new=new_text,
                line=candidate.line,
                column=candidate.column,
            )
        )
    return edits, unrebased, located_into_moved


def _mentions_moved_set(document: Document, member: str, moved_set: set[str], *, bundle: Bundle) -> bool:
    """Whether *document*'s raw text mentions a path resolving into *moved_set*.

    Used only to decide whether a document that failed to parse must still
    refuse the plan. Unterminated frontmatter swallows every remaining line
    into `fm_text`, so `document.body` comes back empty and the normal,
    body-based scan in `_body_edits` sees nothing -- silently treating a
    parse-error document that genuinely cites the moved path the same as one
    that never mentions it at all. Scanning `raw_text` instead is safe for
    *this* yes/no question even though it is never used to place an edit:
    nothing here can safely rewrite a document that does not parse, so the
    only thing this scan is allowed to do is turn a hidden citation into a
    `parse-error` refusal instead of a silent miss.

    *moved_set* holds raw disk ids in relocate mode, and the caller's own
    (unresolved) source strings in repair mode -- because a repaired source
    has already left the bundle, `bundle.member_id` cannot resolve it, so a
    content-derived *target* is checked both through it and directly, for
    the same reason `_body_edits` does.
    """
    for candidate in locate.destinations(document.raw_text):
        path_part, _fragment, external = parse_destination(candidate.text)
        if external or not path_part:
            continue
        target = resolve_path(path_part, source_id=member)
        if target is None:
            continue
        raw_target = bundle.member_id(target)
        if (raw_target is not None and raw_target in moved_set) or target in moved_set:
            return True
    return False


def _read_key(raw: object, key: str) -> object:
    """Read a dotted key path out of a raw frontmatter mapping.

    Integer segments index a sequence, so `sources.0.resource` reads the first
    source's resource. Returns `None` for any path that is not present or whose
    shape does not match -- a malformed value is content, and content never
    raises on the read path.
    """
    current = raw
    for segment in key.split("."):
        if segment.isdigit():
            if not isinstance(current, Sequence) or isinstance(current, (str, bytes)):
                return None
            index = int(segment)
            if index >= len(current):
                return None
            current = current[index]
            continue
        if not isinstance(current, Mapping):
            return None
        if segment not in current:
            return None
        current = current[segment]
    return current


def _reference_key_paths(document: Document) -> tuple[str, ...]:
    """Every dotted key path in *document* that may hold a §6.2 path value.

    The fixed set, plus one `sources.<n>.resource` per element actually
    present. Enumerating from the document rather than guessing an upper bound
    is what keeps the walk exact on a bundle whose `sources` lists differ in
    length.
    """
    paths = list(REFERENCE_KEYS)
    sources = document.fm_raw.get(SOURCES_KEY)
    if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes)):
        paths.extend(f"{SOURCES_KEY}.{index}.resource" for index in range(len(sources)))
    return tuple(paths)


def _frontmatter_edits(
    member: str,
    document: Document,
    *,
    destination_of: Mapping[str, str],
    new_base: str,
    rebase: bool,
    bundle: Bundle,
) -> tuple[list[RefEdit], list[Unrebased]]:
    """Every §6.2 path-valued frontmatter key in *member* that must change.

    Resolved-identity matching is what makes this need no configuration.
    `sources[].resource` may be a §5.1 scope descriptor and top-level
    `resource` may be a table URI; neither resolves to a moved member, so
    neither is touched. The key set can be generous because the match is
    strict.

    Mirrors `_body_edits`' two `Unrebased` outcomes exactly, so the two
    sources are indistinguishable to a consumer of `plan.unrebased`: a moved
    member's own relative reference that escapes the bundle root, or that
    resolves to an in-bundle path nothing lives at, is reported rather than
    rewritten -- and only when *this* member is itself moving (`rebase`),
    since an unmoved member's dangling reference is not this capability's
    business. `destination` mirrors `_body_edits`' `path_part`: the value
    with its fragment split off, used only to tell a root-absolute reference
    apart from a relative one -- a root-absolute reference is never rebased,
    so it is never "unrebased" in the meaningful sense either.
    """
    edits: list[RefEdit] = []
    unrebased: list[Unrebased] = []
    for key in _reference_key_paths(document):
        value = _read_key(document.fm_raw, key)
        if not isinstance(value, str) or not value.strip():
            continue
        stripped = value.strip()
        if is_external(stripped):
            continue
        destination = stripped.partition("#")[0]
        # Mirrors `_body_edits`' `if external or not path_part: continue`: a
        # fragment-only value (`#section`) has no path component to resolve,
        # so it is not a candidate at all -- not "outside the bundle root",
        # which is what an unguarded fall-through into `resolve_reference`
        # (itself returning `None` for an empty destination) would otherwise
        # report it as.
        if not destination:
            continue
        target = resolve_reference(value, source_id=member)
        if target is None:
            if rebase and not destination.startswith("/"):
                unrebased.append(Unrebased(member=member, raw=value, detail="resolves outside the bundle root"))
            continue
        raw_target = bundle.member_id(target)
        moved_to = destination_of.get(raw_target) if raw_target is not None else None
        if moved_to is None:
            moved_to = destination_of.get(target)
        if moved_to is not None:
            new_value = _rewrite_path(stripped, moved_to, new_base, encode=False)
        elif rebase:
            if raw_target is None:
                if not destination.startswith("/"):
                    unrebased.append(Unrebased(member=member, raw=value, detail="target is not a bundle member"))
                continue
            new_value = _rewrite_path(stripped, raw_target, new_base, encode=False)
        else:
            continue
        if new_value == stripped:
            continue
        edits.append(
            RefEdit(
                member=member,
                where="frontmatter",
                target=raw_target if raw_target is not None else target,
                old=value,
                new=new_value,
                key=key,
            )
        )
    return edits, unrebased


def _plan_moves(bundle: Bundle, mapping: Mapping[str, str], *, relocate: bool) -> MovePlan:
    """The one engine behind all four planners.

    *mapping* is old -> new, bundle-relative posix. Every planner is sugar
    that builds one, exactly as `_plan_mapping` backs all four tag planners.
    """
    moves, refusals = _validate(bundle, mapping, relocate=relocate)
    destination_of = {move.source: move.dest for move in moves}
    moved_markdown = {move.source for move in moves if not move.is_asset}

    edits: list[RefEdit] = []
    unrebased: list[Unrebased] = []
    digests: dict[str, str] = {}

    graph = build_link_graph(bundle)
    moved_set = set(destination_of)

    for member, document in sorted(_members(bundle).items()):
        rebase = relocate and member in moved_markdown
        new_base = _parent(destination_of[member]) if rebase else _parent(member)

        member_edits, member_unrebased, located_into_moved = _body_edits(
            member,
            document,
            destination_of=destination_of,
            new_base=new_base,
            rebase=rebase,
            bundle=bundle,
        )
        fm_edits, fm_unrebased = _frontmatter_edits(
            member, document, destination_of=destination_of, new_base=new_base, rebase=rebase, bundle=bundle
        )
        member_edits.extend(fm_edits)
        member_unrebased.extend(fm_unrebased)

        # `is_link_source` mirrors `okf_io.links.build`'s own exclusion:
        # `index.md`/`log.md` are never a `Link.source`, so `graph.out` never
        # carries an entry for them and `expected` stays 0. `expected` is
        # computed **before** the `touched` gate, and factored into it, for
        # exactly one reason: a reference-style link produces zero located
        # edits by construction (its destination lives in a definition, not
        # in the body text `locate.destinations` scans), so `member_edits`
        # alone would never mark this member as needing a look -- and the
        # refusal that exists to catch precisely that case would never fire.
        is_link_source = member.endswith(".md") and PurePosixPath(member).name not in {INDEX_NAME, LOG_NAME}
        expected = 0
        if is_link_source:
            concept_id = member[: -len(".md")]
            expected = sum(
                1
                for link in graph.out.get(concept_id, ())
                if not link.external
                and link.target is not None
                and (bundle.member_id(link.target) or link.target) in moved_set
            )

        touched = bool(member_edits) or rebase or expected > 0
        if not touched and document.parse_error is not None:
            # A parse-error document's `body` can be empty (unterminated
            # frontmatter swallows everything), which also empties `graph.out`
            # for it -- so `expected` cannot see a hidden citation either.
            # This is the one case that needs its own, separate check.
            touched = _mentions_moved_set(document, member, moved_set, bundle=bundle)
        if not touched:
            continue

        if document.parse_error is not None:
            refusals.append(
                Refusal(
                    member,
                    "parse-error",
                    f"cannot rewrite a document that failed to parse "
                    f"({document.parse_error.kind}): {document.parse_error.message}",
                )
            )
            continue

        for definition in locate.reference_definitions(document.body):
            if is_external(definition.href):
                continue
            href_path = parse_destination(definition.href)[0]
            resolved = resolve_path(href_path, source_id=member) if href_path else None
            resolved_id = (bundle.member_id(resolved) if resolved is not None else None) or resolved
            if resolved_id is not None and resolved_id in moved_set:
                refusals.append(
                    Refusal(
                        member,
                        "reference-definition",
                        f"line {definition.line}: `[{definition.label}]: {definition.href}` resolves into the "
                        f"moved set, and a reference-style link's own text carries no destination to rewrite",
                    )
                )

        if is_link_source:
            placed = located_into_moved
            if placed != expected:
                shortfall = expected > placed
                refusals.append(
                    Refusal(
                        member,
                        "unlocatable-reference",
                        f"the link graph reports {expected} reference(s) into the moved set and the locator "
                        f"placed {placed}; "
                        + (
                            "the shortfall is most likely a reference-style link, whose destination lives in a "
                            "definition the block parser consumes"
                            if shortfall
                            else "the excess means the locator matched text the graph never saw"
                        ),
                    )
                )

        edits.extend(member_edits)
        unrebased.extend(member_unrebased)
        digests[member] = body_digest(document.body)

    edits.sort(key=lambda edit: (edit.member, edit.where, edit.line or 0, edit.column or 0, edit.key or ""))
    unrebased.sort(key=lambda entry: (entry.member, entry.raw))
    refusals.sort(key=lambda refusal: (refusal.path, refusal.kind))

    return MovePlan(
        root=bundle.root,
        moves=tuple(moves),
        edits=tuple(edits),
        refusals=tuple(refusals),
        unrebased=tuple(unrebased),
        digests=digests,
        relocate=relocate,
    )


def plan_move(bundle: Bundle, source: str, dest: str) -> MovePlan:
    """Move one member, repairing every inbound reference to it.

    Sugar over `plan_move_many({source: dest})`: a single-member call and the
    equivalent one-entry mapping produce the same plan, because they are the
    same call.
    """
    return plan_move_many(bundle, {source: dest})


def plan_move_dir(bundle: Bundle, source: str, dest: str) -> MovePlan:
    """Move every member under *source* to the matching path under *dest*.

    A directory is not itself a member -- `Bundle` models files -- so this
    enumerates what is under it and hands one mapping to the engine. That is
    what makes a directory rename one traversal rather than N, and what lets a
    reference *between* two members that both move be computed correctly:
    its new form depends on both the new base and the new target.
    """
    prefix = source.rstrip("/")
    # A caller's `source` is written text, like any other reference, and a
    # raw disk id may name the same directory in a different Unicode
    # normalization form (§ADR-0027). Comparing canonically, segment by
    # segment, is what keeps a directory rename from silently matching
    # nothing when the two disagree -- and computing the remainder in
    # *segments* rather than by string length is what keeps the destination
    # correct when a matched segment's raw byte length differs from the
    # caller's.
    head_segments = [segment for segment in prefix.split("/") if segment]
    canonical_head = [canonical_id(segment) for segment in head_segments]
    target = dest.rstrip("/")

    def _under_prefix(member: str) -> str | None:
        parts = member.split("/")
        if len(parts) <= len(canonical_head):
            return None
        if [canonical_id(part) for part in parts[: len(canonical_head)]] != canonical_head:
            return None
        return "/".join(parts[len(canonical_head) :])

    mapping: dict[str, str] = {}
    for member in _all_member_paths(bundle):
        rest = _under_prefix(member)
        if rest is not None:
            mapping[member] = f"{target}/{rest}"
    return plan_move_many(bundle, mapping)


def plan_move_many(bundle: Bundle, mapping: Mapping[str, str]) -> MovePlan:
    """Move every member in *mapping*, old -> new, in one pass."""
    return _plan_moves(bundle, mapping, relocate=True)


def plan_repair(bundle: Bundle, mapping: Mapping[str, str]) -> MovePlan:
    """Repair inbound references for moves that already happened.

    The same engine with the relocation step omitted: no requirement that the
    source still exist, and no `dest-exists` refusal for a destination that
    already does -- which is what makes it the recovery path after a partial
    `apply` (destinations commit before referrers, so a partial failure always
    leaves the destination already written) and the back-repair for
    references broken by a move nothing planned (the file already sits at its
    new path). Both are the ordinary, expected state in this mode, not a
    conflict to refuse.

    **Inbound only.** A moved member's own outbound references are not rebased
    here: after a partial `apply` the destination content was already written
    with its rebasing (destinations commit first), and for a move nothing
    planned, the file already sits at its new path. Recorded as a limitation
    in the package README rather than guessed at.
    """
    return _plan_moves(bundle, mapping, relocate=False)


def _all_member_paths(bundle: Bundle) -> tuple[str, ...]:
    """Every member of *bundle* as a bundle-relative posix path, sorted."""
    found = {f"{cid}.md" for cid in bundle.concepts}
    found.update(bundle.assets)
    found.update(f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME for directory in bundle.indexes)
    found.update(f"{directory}/{LOG_NAME}" if directory else LOG_NAME for directory in bundle.logs)
    return tuple(sorted(found))


#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this.
__all__ = [
    "REFERENCE_KEYS",
    "SOURCES_KEY",
    "plan_move",
    "plan_move_dir",
    "plan_move_many",
    "plan_repair",
]
