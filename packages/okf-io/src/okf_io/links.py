"""The markdown link graph over a loaded bundle (OKF v0.2 §6).

Resolution is four steps, and each one is a rule an existing implementation
gets wrong. The graph derived from them keeps backlinks *computed*, never
written into a neighbour's frontmatter -- writing them is what dirties every
neighbour on each edit, and not doing that is the whole point of the
two-layer model.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from urllib.parse import unquote

from okf_io import _md
from okf_io._md import BodyIndex, MdLink
from okf_io.bundle import Bundle

#: A URI scheme, per RFC 3986 §3.1.
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


@dataclass(frozen=True, slots=True)
class Link:
    """One resolved destination.

    ``target`` is ``None`` for an external destination and for a relative path
    that escapes the bundle root; ``external`` tells the two apart, and only
    the second is broken.
    """

    source: str  # id of the containing concept
    raw: str  # the destination exactly as markdown-it reports it
    target: str | None  # resolved bundle-relative posix path
    fragment: str | None  # stripped before resolution, kept for reporting
    external: bool  # has a scheme, or is protocol-relative
    image: bool  # from an `![](...)` token
    line: int | None  # 1-based, in the file; every producer today is body-derived and
    # always supplies one, but the type stays Optional to admit a future
    # non-body-line producer without a signature change.


@dataclass(frozen=True, slots=True)
class LinkGraph:
    links: tuple[Link, ...]
    out: Mapping[str, tuple[Link, ...]]
    backlinks: Mapping[str, tuple[str, ...]]
    broken: tuple[Link, ...]
    bodies: Mapping[str, BodyIndex]


def is_external(destination: str) -> bool:
    """Whether *destination* has a URI scheme or is protocol-relative.

    A relative path whose *first* segment contains a colon --
    ``notes:draft.md``, ``report:v2.sql`` -- reads as ``scheme:opaque`` per RFC
    3986 §4.2 and is therefore external too, even though it names no file
    outside the bundle. Such a value is never resolved or reported broken.
    Writing it ``./notes:draft.md`` forces the leading-dot path interpretation.
    """
    return destination.startswith("//") or _SCHEME_RE.match(destination) is not None


def parse_destination(raw: str) -> tuple[str, str, bool]:
    """Split *raw* per RFC 3986 order: delimiters first, decode after.

    Returns ``(path, fragment, external)`` with path and fragment decoded.
    ``%23`` is an escaped literal ``#``, not a delimiter, so the fragment
    split runs on the raw text; likewise the scheme check (§3.1: schemes are
    never percent-encoded) runs on the raw path part.
    """
    path_raw, _, fragment_raw = raw.partition("#")
    return unquote(path_raw), unquote(fragment_raw), is_external(path_raw)


def _normalize(base: str, relative: str) -> str | None:
    """Join *relative* onto *base*, collapsing ``.`` and ``..``.

    Returns ``None`` when the path climbs above the bundle root. ``PurePosixPath``
    will not do this: it preserves ``..`` rather than resolving it, so a
    ``.parent``-based join silently produces ``a/../b`` and never matches a
    member.
    """
    parts = [segment for segment in base.split("/") if segment] if base else []
    for segment in relative.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(segment)
    return "/".join(parts)


def resolve_path(destination: str, *, source_id: str) -> str | None:
    """Resolve a bundle path. Both §6.1 forms, or ``None`` if it escapes the root.

    A leading ``/`` is bundle-root-relative; anything else is relative to the
    containing file's directory. ``acme_retail`` uses file-relative links in
    prose and root-absolute links in its ``# Cited by`` sections, and the
    reference viewer silently discards the latter -- both resolving here is a
    named acceptance criterion, not an implementation detail.
    """
    if destination.startswith("/"):
        return _normalize("", destination[1:])
    parent = PurePosixPath(source_id).parent.as_posix()
    return _normalize("" if parent == "." else parent, destination)


def resolve_reference(value: str, *, source_id: str) -> str | None:
    """Resolve a §6.2 path-valued frontmatter field.

    Unlike a markdown destination this arrives undecoded and unfragmented, so
    it skips the percent-decode. Callers must screen for :func:`is_external`
    first: this returns ``None`` for an external value and for one that escapes
    the root, and the caller is the only one who can tell those apart.

    A §6.2 value like ``report:v2.sql`` is a plausible relative filename, but
    its first segment contains a colon, so :func:`is_external` reads it as a
    scheme and this returns ``None`` for it -- it is never validated. Writing
    it ``./report:v2.sql`` is what forces path interpretation; see
    :func:`is_external` for the RFC 3986 §4.2 rule.
    """
    destination = value.strip().partition("#")[0]
    if not destination or is_external(destination):
        return None
    return resolve_path(destination, source_id=source_id)


def absolute_form(link: Link) -> str | None:
    """The root-absolute path a *relative* link would resolve to, or ``None``.

    Feeds the okfcli-style repair hint: when a broken relative link would
    resolve from the bundle root, the message suggests the absolute form.
    """
    if link.external or link.raw.startswith("/"):
        return None
    destination = parse_destination(link.raw)[0]
    if not destination:
        return None
    return _normalize("", destination)


def _link_from(md_link: MdLink, *, source: str, line: int) -> Link | None:
    """Resolve one destination, or ``None`` when it is not an edge."""
    # Split then decode (RFC 3986 order); markdown-it normalizes destinations
    # on the way out (`./café.md` arrives as `./caf%C3%A9.md`), so the decode
    # itself is non-optional. An external destination is never resolved, never
    # reported broken; a destination that is *only* a fragment is an
    # intra-document anchor, not an edge.
    path_part, fragment, external = parse_destination(md_link.raw)
    if external:
        return Link(source, md_link.raw, None, None, True, md_link.image, line)
    if not path_part:
        return None
    target = resolve_path(path_part, source_id=source)
    return Link(source, md_link.raw, target, fragment or None, False, md_link.image, line)


def build(bundle: Bundle) -> LinkGraph:
    """Build the graph. One markdown parse per concept, shared through ``bodies``.

    Reserved files are not link sources: ``Link.source`` is a concept id, and
    "what cites this concept" is a question about prose, not about an index
    enumerating its own directory. The reserved rules still read those bodies,
    through the memoized :func:`okf_io._md.parse_body`.
    """
    bodies: dict[str, BodyIndex] = {}
    collected: list[Link] = []

    for concept_id in sorted(bundle.concepts):
        document = bundle.concepts[concept_id]
        index = _md.parse_body(document.body)
        bodies[concept_id] = index
        offset = document.body_line_offset
        for md_link in index.links:
            link = _link_from(md_link, source=concept_id, line=md_link.line + offset)
            if link is not None:
                collected.append(link)

    collected.sort(key=lambda link: (link.source, link.line or 0, link.raw, link.image))
    links = tuple(collected)

    out: defaultdict[str, list[Link]] = defaultdict(list)
    back: defaultdict[str, set[str]] = defaultdict(set)
    broken: list[Link] = []
    for link in links:
        out[link.source].append(link)
        if link.external:
            continue
        raw_target = bundle.member_id(link.target) if link.target is not None else None
        if raw_target is None:
            broken.append(link)
            continue
        # An embedded asset does not cite anything, so images are excluded.
        if not link.image and raw_target.endswith(".md"):
            target_id = raw_target[: -len(".md")]
            if target_id in bundle.concepts:
                back[target_id].add(link.source)

    return LinkGraph(
        links=links,
        out=MappingProxyType({k: tuple(v) for k, v in sorted(out.items())}),
        backlinks=MappingProxyType({k: tuple(sorted(v)) for k, v in sorted(back.items())}),
        broken=tuple(broken),
        bodies=MappingProxyType(dict(sorted(bodies.items()))),
    )
