#!/usr/bin/env python3
"""Convert Obsidian wikilinks to OKF §6.1 markdown links, in any graph-wiki vault.

``[[entities/pkg_okf-io]]`` becomes ``[okf-io](/entities/pkg_okf-io.md)``. More
than one vault needs this, so it is a tool over a vault path rather than a
one-off over any single one.

Design notes
------------
* **The bundle root is the vault (`<workspace>/wiki`), so a root-absolute link
  carries no `/wiki/` prefix.** `/wiki/entities/x.md` would resolve to a member
  `wiki/entities/x.md` that does not exist under this root -- breaking every
  link rather than none. `raw/` is deliberately *not* linkable: it is a sibling
  of the root, `load_bundle` does not follow a symlink into it, and both
  `../raw/x.md` and `/raw/x.md` resolve to a memberless `raw/x.md`. The vault's
  ten references to `raw/` stay in `source_path:` frontmatter, which no rule
  validates. See the design spec, D-1.

* **Code regions come from the parser, not from a guess.** Some `[[...]]` in
  these vaults are TOML samples (`[[tool.importlinter.contracts]]`) or prose
  *about* wikilinks (`` `Callable[[EntryTarget], str | None]` ``). Converting
  either corrupts the file. `okf_io._md` already establishes the idiom for this
  -- its footnote labels "are found by a line scan restricted to the ranges the
  parser says are *not* code" -- so `BodyIndex.code_blocks` supplies the
  excluded lines and inline-code spans are masked in place, with the wikilink
  regex running only on what survives.

* **An unresolvable target is left exactly as it is.** `.templates/` carries
  `[[entities/<prefix>_<name>]]` placeholders and the specs discuss `[[wikilink]]`
  in prose; neither names a page, and neither should be rewritten into a
  dangling link. This is also the safety net for anything the code-masking
  misses.

* **Link text prefers the target's H1 over its filename stem** because the stem
  collides: `unit_tests_okf-io` strips to `okf-io`, which is already
  `pkg_okf-io`'s name. Across this vault's entity pages the two agree
  everywhere *except* the `unit_tests_` pages -- exactly the collisions.

* **Dry-run by default**, matching okf-io's writer conventions. Edits go
  through `Document`, never a re-emit, because these are hand-edited files.
  Re-running is a no-op, so a later `git mv` relayout plus a re-run converges
  rather than compounding.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from okf_io import Document, _md, load_bundle
from okf_io.bundle import Bundle

INDEX_NAME = "index.md"
LOG_NAME = "log.md"

#: Copied from `packages/okf-ext/src/okf_ext/render/rule.py` (`_WIKILINK_RE`),
#: with named groups added for the anchor and alias this script has to render.
#: The character classes are unchanged and deliberately so: no group crosses a
#: newline, because a wikilink is a single-line construct and joining an
#: unclosed `[[` at one line's end to a stray `]]` on the next would rewrite two
#: unrelated lines into one bogus link. The target group requires at least one
#: character, so `[[]]` never matches. Inside a table cell the alias separator
#: is escaped as `\|`, which the lookahead stops the target at.
_WIKILINK_RE = re.compile(
    r"\[\["
    r"(?P<target>(?:(?!\\\|)[^\]|#\n])+)"
    r"(?:#(?P<anchor>[^\]|\n]*))?"
    r"(?:\\?\|(?P<alias>[^\]\n]*))?"
    r"\]\]"
)

_BACKTICK_RUN_RE = re.compile(r"`+")
_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
#: Entity filenames encode their type as a prefix; the graph node name is what
#: follows it. Longest first, so `unit_tests_` wins over any shorter match.
_ENTITY_PREFIXES = ("unit_tests_", "agent_plugin_", "domain_", "repo_", "pkg_", "dep_", "app_")
#: Filler for masked-out code. Same length as what it replaces, so every offset
#: on the line still points where it did.
_MASK = "\x00"
#: The dated-slug form every pre-migration work item carried
#: (`2026-08-11-epic-graph-works-cli`). The date is what makes a bare slug safe
#: to match against basenames: an undated bare name like `01-design-spec`
#: matches 170+ files in this bundle, which is why `okf_ext.body.resolve_wikilink`
#: declines shortest-path resolution outright.
_DATED_SLUG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(?P<slug>.+)$")
#: Work-item kinds, for the historic `epic-<kind>-<rest>` -> `<kind>-epic-<kind>-<rest>`
#: rename. Spelled out rather than matched as `[a-z]+` because `tech-debt` and
#: `test-gap` are hyphenated and both appear in the renamed form on disk.
_WORK_KINDS = ("bug", "feature", "spike", "tech-debt", "test-gap")


@dataclass(frozen=True, slots=True)
class Conversion:
    """One wikilink rewritten, for the report."""

    member: str
    line: int
    before: str
    after: str
    step: str


@dataclass(frozen=True, slots=True)
class Resolution:
    """Where a wikilink target landed, and which ladder step put it there."""

    member: str
    step: str


@dataclass(frozen=True, slots=True)
class Skip:
    """One wikilink left alone, with the reason."""

    member: str
    line: int
    raw: str
    reason: str


@dataclass
class Result:
    conversions: list[Conversion] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)
    titled: list[tuple[str, str]] = field(default_factory=list)
    changed: set[str] = field(default_factory=set)
    #: raw wikilink target -> (destination member, ladder step). One entry per
    #: distinct target across the whole bundle -- 164 of them here, against 1257
    #: occurrences. This is what a human reads before `--write`.
    mapping: dict[str, tuple[str, str]] = field(default_factory=dict)


def is_reserved(member: str) -> bool:
    """Whether *member* is a §12 reserved file (`index.md` / `log.md`)."""
    return posixpath.basename(member) in {INDEX_NAME, LOG_NAME}


def members(bundle: Bundle) -> dict[str, Document]:
    """Every markdown member, keyed by bundle-relative path.

    Indexes and logs are included alongside concepts: a wikilink in `index.md`
    is as real as one in a concept, and `index.md` is where this vault keeps
    its catalog.
    """
    out: dict[str, Document] = {f"{cid}.md": doc for cid, doc in bundle.concepts.items()}
    for directory, doc in bundle.indexes.items():
        out[f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME] = doc
    for directory, doc in bundle.logs.items():
        out[f"{directory}/{LOG_NAME}" if directory else LOG_NAME] = doc
    return out


def mask_inline_code(line: str) -> str:
    """Blank out inline-code spans, preserving every other offset.

    CommonMark's rule: a run of N backticks is closed by the next run of
    *exactly* N. `okf_io._md` uses a simpler `` `[^`]*` `` for footnotes, which
    mis-pairs a ``​``double-backtick``​`` span -- and these vaults contain
    wikilinks inside exactly that construct.
    """
    runs = [(m.start(), m.end()) for m in _BACKTICK_RUN_RE.finditer(line)]
    if not runs:
        return line
    out = list(line)
    i = 0
    while i < len(runs):
        start, end = runs[i]
        width = end - start
        for j in range(i + 1, len(runs)):
            if runs[j][1] - runs[j][0] == width:
                for k in range(start, runs[j][1]):
                    out[k] = _MASK
                i = j + 1
                break
        else:
            i += 1
    return "".join(out)


def unmasked(line: str, match: re.Match[str], group: str) -> str | None:
    """A match group's text as it appears in *line*, not in the masked copy.

    The regex runs over `mask_inline_code`'s output, so every group holds
    filler wherever the source had inline code -- and an alias legitimately
    contains it (`[[sources/x|Design spec — okf-ext ``search`` capability]]`
    is real in this vault). Masking is length-preserving precisely so the
    original can be recovered by span. Using `match.group()` here instead
    writes NUL bytes into the rendered link label.
    """
    start, end = match.span(group)
    return None if start < 0 else line[start:end]


def code_lines(index: _md.BodyIndex) -> frozenset[int]:
    """1-based body line numbers the parser reports as code. `end` is exclusive."""
    return frozenset(n for block in index.code_blocks for n in range(block.start, block.end))


def strip_entity_prefix(stem: str) -> str:
    for prefix in _ENTITY_PREFIXES:
        if stem.startswith(prefix) and len(stem) > len(prefix):
            return stem[len(prefix) :]
    return stem


def heading_of(doc: Document) -> str | None:
    """The document's H1, ignoring any inside a code block."""
    index = _md.parse_body(doc.body)
    skip = code_lines(index)
    for number, line in enumerate(doc.body.split("\n"), start=1):
        if number in skip:
            continue
        match = _H1_RE.match(line.rstrip("\r"))
        if match:
            return match.group(1).strip()
    return None


def display_name(member: str, doc: Document | None) -> str:
    """What a page should be called: its H1, else its de-prefixed stem.

    The stem is the fallback rather than the primary because it collides --
    `unit_tests_okf-io` and `pkg_okf-io` both strip to `okf-io`.
    """
    if doc is not None:
        heading = heading_of(doc)
        if heading:
            return heading
    return strip_entity_prefix(posixpath.basename(member).removesuffix(".md"))


def link_text(member: str, doc: Document | None) -> str:
    """Text for a bare wikilink: the target's `title:`, else its display name."""
    if doc is not None and doc.fm.title:
        return doc.fm.title
    return display_name(member, doc)


def resolve(target: str, bundle: Bundle, by_stem: dict[str, list[str]]) -> str | None:
    """A wikilink target to a bundle member path, or None if it names nothing.

    Vault wikilinks are root-relative (the vault CLAUDE.md: "every `[[wikilink]]`
    resolves relative to it"). A bare name with no `/` also gets Obsidian's
    shortest-path treatment, but only when it is unambiguous -- two pages
    sharing a basename resolve to neither.
    """
    cleaned = target.strip().lstrip("/")
    if not cleaned:
        return None
    candidate = cleaned if cleaned.endswith(".md") else f"{cleaned}.md"
    candidate = posixpath.normpath(candidate)
    if candidate.startswith(".."):
        return None
    if bundle.has_member(candidate):
        return candidate
    if "/" not in cleaned:
        matches = by_stem.get(cleaned, [])
        if len(matches) == 1:
            return matches[0]
    return None


def split_item_path(target: str) -> tuple[str, str] | None:
    """A legacy work-item target as `(item_slug, trailing_artifact_path)`.

    Two accepted shapes, and nothing else:

    * `work/[_archive/]<slug>[/<trail>]` -- the lane prefix is the licence to
      treat the next segment as an item slug, dated or not.
    * `<YYYY-MM-DD>-<slug>[/<trail>]` -- no lane prefix, so the date is the
      licence instead.

    Returns None for anything else, which sends the target down to step 4.
    That includes every undated bare name: `01-design-spec` names 170+ files
    here, so matching it against basenames would be a coin flip.
    """
    parts = target.strip().lstrip("/").split("/")
    if not parts or not parts[0]:
        return None
    if parts[0] == "work":
        parts.pop(0)
        if parts and parts[0] == "_archive":
            parts.pop(0)
        if not parts or not parts[0]:
            return None
        dated = _DATED_SLUG_RE.match(parts[0])
        slug = dated.group("slug") if dated else parts[0]
    else:
        dated = _DATED_SLUG_RE.match(parts[0])
        if dated is None:
            return None
        slug = dated.group("slug")
    # The trail is written both with and without `.md` in this bundle; strip it
    # so `<item-dir>/<trail>.md` cannot become `<item-dir>/00-decisions.md.md`.
    return slug, "/".join(parts[1:]).removesuffix(".md")


def resolve_item(slug: str, by_stem: dict[str, list[str]]) -> str | None:
    """The one member whose basename is *slug*, or None on zero or many hits.

    Ambiguity is unresolved rather than first-wins: a slug that names both a
    live item and an archived one is exactly the case a human should look at.
    """
    hits = by_stem.get(slug, [])
    if not hits:
        for kind in _WORK_KINDS:
            if slug == f"epic-{kind}" or slug.startswith(f"epic-{kind}-"):
                hits = by_stem.get(f"{kind}-{slug}", [])
                break
    return hits[0] if len(hits) == 1 else None


def resolve_artifact(item_member: str, trail: str, bundle: Bundle) -> str | None:
    """An artifact path beneath a located item's owned directory, or None.

    The search is confined to that one item's directory on purpose. Bare-basename
    matching is what makes this safe to attempt at all: `01-design-spec` is a
    universal filename, meaningless outside the item that owns it.
    """
    directory = item_member.removesuffix(".md")
    base = posixpath.basename(trail)
    for candidate in (
        f"{directory}/{trail}.md",
        f"{directory}/references/{base}.md",
        f"{directory}/references/_archive/{base}.md",
    ):
        if bundle.has_member(candidate):
            return candidate
    return None


def resolve_ladder(target: str, bundle: Bundle, by_stem: dict[str, list[str]]) -> Resolution | None:
    """Steps 1-3 of the resolution ladder. None means step 4 (de-link).

    Step order is significance order: an exact member always wins, and a target
    only gets treated as a legacy work path once it has failed to be a real one.
    """
    exact = resolve(target, bundle, by_stem)
    if exact is not None:
        return Resolution(exact, "exact")
    split = split_item_path(target)
    if split is None:
        return None
    slug, trail = split
    item = resolve_item(slug, by_stem)
    if item is None:
        return None
    if not trail:
        return Resolution(item, "item")
    artifact = resolve_artifact(item, trail, bundle)
    if artifact is not None:
        return Resolution(artifact, "artifact")
    return Resolution(item, "demoted")


def escape_text(text: str) -> str:
    """Make *text* safe as a markdown link label.

    `|` is escaped because a converted link inside a table cell would otherwise
    grow a column; `[` and `]` because they would close the label early.
    """
    return re.sub(r"([\[\]\\])", r"\\\1", text).replace("|", r"\|")


def delink(raw: str) -> str:
    """`[[x]]` as inline code, so it renders as the dead name it is.

    Idempotent by construction: once an occurrence is inline code, the scanner's
    `mask_inline_code` blanks it and the next run does not see it at all.
    """
    longest = max((run_.end() - run_.start() for run_ in _BACKTICK_RUN_RE.finditer(raw)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if raw.startswith("`") or raw.endswith("`") else ""
    return f"{fence}{pad}{raw}{pad}{fence}"


def encode_url(path: str) -> str:
    """Percent-encode only what would break a markdown destination."""
    return path.replace("%", "%25").replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def destination(target_member: str, source_member: str, *, relative: bool) -> str:
    if relative:
        source_dir = posixpath.dirname(source_member)
        return encode_url(posixpath.relpath(target_member, start=source_dir or "."))
    return "/" + encode_url(target_member)


def convert_body(
    member: str,
    doc: Document,
    bundle: Bundle,
    all_members: dict[str, Document],
    by_stem: dict[str, list[str]],
    result: Result,
    *,
    relative: bool,
    delink_unresolved: bool,
) -> tuple[str, set[str]]:
    """Rewrite one body. Returns the new text and the targets it resolved to."""
    index = _md.parse_body(doc.body)
    skip = code_lines(index)
    resolved: set[str] = set()
    lines = doc.body.split("\n")

    for position, line in enumerate(lines):
        number = position + 1
        if number in skip or "[[" not in line:
            continue
        raw = line.rstrip("\r")
        scannable = mask_inline_code(raw)
        pieces: list[str] = []
        cursor = 0
        for match in _WIKILINK_RE.finditer(scannable):
            if match.start() > 0 and scannable[match.start() - 1] == "!":
                result.skips.append(Skip(member, number, line[match.start() : match.end()], "embed"))
                continue
            raw_target = unmasked(line, match, "target") or ""
            resolution = resolve_ladder(raw_target, bundle, by_stem)
            if resolution is None:
                original = line[match.start() : match.end()]
                # Test the RAW (unmasked) line, not `scannable`: a paired code
                # span's backticks have already been replaced by `mask_inline_code`,
                # so testing `scannable` only ever catches a *stray* unpaired
                # backtick and misses a *closed* span flush against the match --
                # which merges just as badly (and, for a double-backtick span,
                # destructively). A masked backtick in `scannable` means an
                # adjacent code span too, so testing `raw` strictly subsumes the
                # old guard.
                adjacent_backtick = (match.start() > 0 and raw[match.start() - 1] == "`") or (
                    match.end() < len(raw) and raw[match.end()] == "`"
                )
                if not delink_unresolved or adjacent_backtick:
                    result.skips.append(Skip(member, number, original, "unresolved"))
                    continue
                rendered = delink(original)
                pieces.append(line[cursor : match.start()])
                pieces.append(rendered)
                result.skips.append(Skip(member, number, original, "delinked"))
                cursor = match.end()
                continue
            target = resolution.member
            result.mapping.setdefault(raw_target, (target, resolution.step))
            resolved.add(target)
            alias = unmasked(line, match, "alias")
            # `all_members`, not `bundle.concept()`: `index.md` and `log.md` are
            # reserved files rather than concepts, and `[[index]]` is a real
            # link whose text should still come from the page it points at.
            text = alias if alias is not None else link_text(target, all_members.get(target))
            anchor = unmasked(line, match, "anchor")
            if anchor and resolution.step == "demoted":
                # Demotion swaps the destination for the item page; an anchor
                # written against the original artifact almost never exists
                # there. Demotion is already lossy -- drop the anchor too
                # rather than emit a link to a heading that doesn't exist.
                anchor = None
            url = destination(target, member, relative=relative)
            rendered = f"[{escape_text(text)}]({url}{'#' + anchor if anchor else ''})"
            pieces.append(line[cursor : match.start()])
            pieces.append(rendered)
            result.conversions.append(
                Conversion(member, number, line[match.start() : match.end()], rendered, resolution.step)
            )
            cursor = match.end()
        if pieces:
            pieces.append(line[cursor:])
            lines[position] = "".join(pieces)

    return "\n".join(lines), resolved


def run(vault: Path, *, write: bool, relative: bool, backfill: bool, delink_unresolved: bool = False) -> Result:
    bundle = load_bundle(vault)
    all_members = members(bundle)

    by_stem: dict[str, list[str]] = defaultdict(list)
    for path in all_members:
        by_stem[posixpath.basename(path).removesuffix(".md")].append(path)
    for path in bundle.assets:
        by_stem[posixpath.basename(path)].append(path)

    result = Result()
    needs_title: set[str] = set()

    for member in sorted(all_members):
        doc = all_members[member]
        if doc.parse_error is not None:
            result.skips.append(Skip(member, 0, "", f"unparseable: {doc.parse_error.kind}"))
            continue
        body, resolved = convert_body(
            member,
            doc,
            bundle,
            all_members,
            by_stem,
            result,
            relative=relative,
            delink_unresolved=delink_unresolved,
        )
        # Reserved files are excluded: §12 permits `okf_version` on `index.md`
        # and `log.md` and nothing else, and this vault's `index.md` carries no
        # frontmatter at all -- so a `title:` here would not merely add a key,
        # it would staple a whole block onto a file the spec says must not have
        # one, and trip `reserved.index-frontmatter`.
        needs_title |= {t for t in resolved if t in all_members and not is_reserved(t) and not all_members[t].fm.title}
        if body != doc.body:
            doc.set_body(body)
            result.changed.add(member)

    if backfill:
        for member in sorted(needs_title):
            doc = all_members[member]
            if doc.parse_error is not None:
                continue
            name = display_name(member, doc)
            doc.set("title", name)
            result.titled.append((member, name))
            result.changed.add(member)

    if write:
        for member in sorted(result.changed):
            all_members[member].save()

    return result


def report(result: Result, *, write: bool) -> None:
    by_member: dict[str, int] = defaultdict(int)
    for conversion in result.conversions:
        by_member[conversion.member] += 1

    for member in sorted(by_member):
        print(f"  {member}: {by_member[member]} link(s)")

    reasons: dict[str, int] = defaultdict(int)
    for skip in result.skips:
        reasons[skip.reason] += 1

    print()
    print(f"converted        {len(result.conversions)}")
    print(f"files changed    {len(result.changed)}")
    print(f"titles added     {len(result.titled)}")
    for reason in sorted(reasons):
        print(f"left alone       {reasons[reason]} ({reason})")

    if result.titled:
        print("\ntitles backfilled:")
        for member, name in result.titled:
            print(f"  {member}: {name!r}")

    if result.mapping:
        by_step: dict[str, list[str]] = defaultdict(list)
        for raw in sorted(result.mapping):
            by_step[result.mapping[raw][1]].append(raw)
        print("\ntarget map (distinct targets, by ladder step):")
        for step in ("exact", "item", "artifact", "demoted"):
            for raw in by_step.get(step, []):
                print(f"  [{step}] {raw} -> {result.mapping[raw][0]}")

    delinked_targets: dict[str, list[str]] = defaultdict(list)
    for skip in result.skips:
        if skip.reason == "delinked":
            delinked_targets[skip.raw].append(f"{skip.member}:{skip.line}")
    if delinked_targets:
        print("\nde-linked (rewritten as inline code):")
        for raw in sorted(delinked_targets):
            where = delinked_targets[raw]
            shown = ", ".join(where[:3]) + (f", +{len(where) - 3} more" if len(where) > 3 else "")
            print(f"  {raw}  -- {shown}")

    unresolved: dict[str, list[str]] = defaultdict(list)
    for skip in result.skips:
        if skip.reason == "unresolved":
            unresolved[skip.raw].append(f"{skip.member}:{skip.line}")
    if unresolved:
        print("\nunresolved targets (left byte-identical):")
        for raw in sorted(unresolved):
            where = unresolved[raw]
            shown = ", ".join(where[:3]) + (f", +{len(where) - 3} more" if len(where) > 3 else "")
            print(f"  {raw}  -- {shown}")

    print()
    print("WROTE changes to disk." if write else "Dry run. Nothing written; pass --write to apply.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("vault", type=Path, help="bundle root, e.g. <workspace>/wiki")
    parser.add_argument("--write", action="store_true", help="apply changes (default: dry run)")
    parser.add_argument(
        "--relative",
        action="store_true",
        help="emit file-relative destinations instead of root-absolute ones",
    )
    parser.add_argument(
        "--delink-unresolved",
        action="store_true",
        help="rewrite a target that names nothing as inline code instead of leaving it",
    )
    parser.add_argument(
        "--no-title-backfill",
        dest="backfill",
        action="store_false",
        help="do not add a title: to link targets that lack one",
    )
    args = parser.parse_args(argv)

    if not args.vault.is_dir():
        print(f"not a directory: {args.vault}", file=sys.stderr)
        return 2

    report(
        run(
            args.vault,
            write=args.write,
            relative=args.relative,
            backfill=args.backfill,
            delink_unresolved=args.delink_unresolved,
        ),
        write=args.write,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
