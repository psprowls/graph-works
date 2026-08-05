"""The typed, immutable view of a concept's frontmatter.

Lenient normalization runs *here*, when the view is built — never in a
constructor and never as a parse gate. Building a view of a malformed
concept is required to succeed (spec §11); shape and spec validation belong
to the ``validate`` module.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Literal

from okf_io import _md

ActorKind = Literal["human", "process", "agent", "unknown"]

#: Governs newly created documents and newly inserted keys **only**. Existing
#: documents are never reordered: reordering is a diff bomb even when it is
#: comment-safe.
PREFERRED_KEY_ORDER: tuple[str, ...] = (
    "type",
    "resource",
    "title",
    "description",
    "tags",
    "status",
    "generated",
    "verified",
    "stale_after",
    "sources",
    "usage_window",
)

KNOWN_KEYS: frozenset[str] = frozenset(
    {
        *PREFERRED_KEY_ORDER,
        "runtime",
        "parameters",
        "executor",
        "attester",
        "computation",
        "timestamp",
    }
)

_AGENT_RE = re.compile(r"^[^\s:/]+/\S+$")

#: Default for every ``extra`` field. A plain ``dict`` default would make a
#: directly constructed Frontmatter writable while a built one is not, so the
#: class would only honour its own read-only contract half the time.
_EMPTY_EXTRA: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Actor:
    raw: str
    kind: ActorKind
    id: str


@dataclass(frozen=True, slots=True)
class Generated:
    by: Actor | None = None
    at: str | None = None
    at_dt: datetime | None = None
    extra: Mapping[str, Any] = _EMPTY_EXTRA


@dataclass(frozen=True, slots=True)
class Verified:
    by: Actor | None = None
    at: str | None = None
    at_dt: datetime | None = None
    extra: Mapping[str, Any] = _EMPTY_EXTRA


@dataclass(frozen=True, slots=True)
class UsageWindow:
    from_: date | None = None
    to: date | None = None
    extra: Mapping[str, Any] = _EMPTY_EXTRA


@dataclass(frozen=True, slots=True)
class Source:
    id: str | None = None
    resource: str | None = None
    title: str | None = None
    author: Actor | None = None
    usage_count: int | None = None
    last_modified: date | None = None
    usage_window: UsageWindow | None = None
    extra: Mapping[str, Any] = _EMPTY_EXTRA


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str | None = None
    type: str | None = None
    required: bool | None = None
    default: Any = None
    extra: Mapping[str, Any] = _EMPTY_EXTRA


@dataclass(frozen=True, slots=True)
class Executor:
    resource: str | None = None
    receipt: tuple[str, ...] = ()
    extra: Mapping[str, Any] = _EMPTY_EXTRA


@dataclass(frozen=True, slots=True)
class Attester:
    resource: str | None = None
    extra: Mapping[str, Any] = _EMPTY_EXTRA


@dataclass(frozen=True, slots=True)
class Frontmatter:
    """A frozen projection of ``fm_raw``. Absent fields are None or empty.

    Three fields carry the interface to the rule catalog:

    ``extra``
        Unknown keys, *mirroring* rather than replacing their presence in the
        ``CommentedMap``. This is what keeps ``not:``, ``from:``, and ``class:``
        readable — attribute mapping would break on all three.
    ``coercion_failures``
        Dotted field paths whose raw value was the wrong shape. Without it the
        view is silently lossy and the rules would have to re-read
        ``fm_raw``; with it, rules fire off the view. The raw value always stays
        reachable through ``fm_raw``.
    ``fallbacks``
        The v0.1 read-time fallbacks that fired, so a consumer can tell a
        fallback-derived value from a real one.
    """

    type: str | None = None
    title: str | None = None
    description: str | None = None
    resource: str | None = None
    tags: tuple[str, ...] = ()
    status: str | None = None
    stale_after: date | None = None
    generated: Generated | None = None
    verified: tuple[Verified, ...] = ()
    sources: tuple[Source, ...] = ()
    usage_window: UsageWindow | None = None
    runtime: str | None = None
    parameters: tuple[Parameter, ...] = ()
    executor: Executor | None = None
    attester: Attester | None = None
    computation: str | None = None
    extra: Mapping[str, Any] = _EMPTY_EXTRA
    coercion_failures: frozenset[str] = frozenset()
    fallbacks: frozenset[str] = frozenset()


def parse_actor(
    value: Any,  # noqa: ANN401 -- accepts arbitrary raw YAML scalar
    path: str | None = None,
    failures: set[str] | None = None,
) -> Actor | None:
    """Classify an actor string per §7. Never raises, never rejects.

    *path* and *failures* are optional so the classifier stays callable on a
    bare value. Supply them from a builder: a structurally wrong actor (a
    mapping or a list where a scalar belongs) would otherwise be stringified
    into a plausible-looking ``kind="unknown"`` actor with no trace, making it
    indistinguishable from a legitimately unrecognized *format* such as
    ``team:data-platform``. The wrong-type case still yields an Actor -- this
    is a view, not a gate -- but it is recorded.
    """
    if value is None:
        return None
    wrong_shape = isinstance(value, Mapping) or _is_listish(value)
    if wrong_shape and failures is not None and path is not None:
        failures.add(path)
    raw = str(value)
    if raw.startswith("human:"):
        return Actor(raw=raw, kind="human", id=raw[len("human:") :])
    if raw.startswith("process:"):
        return Actor(raw=raw, kind="process", id=raw[len("process:") :])
    if _AGENT_RE.match(raw):
        return Actor(raw=raw, kind="agent", id=raw)
    return Actor(raw=raw, kind="unknown", id=raw)


def _as_str(value: Any, path: str, failures: set[str]) -> str | None:  # noqa: ANN401 -- raw YAML value of unknown shape
    if value is None:
        return None
    if isinstance(value, str):
        return str(value)  # flattens ruamel's ScalarString
    if isinstance(value, bool | int | float):
        return str(value)
    failures.add(path)
    return None


def _as_int(value: Any, path: str, failures: set[str]) -> int | None:  # noqa: ANN401 -- raw YAML value of unknown shape
    if value is None:
        return None
    if isinstance(value, bool):
        failures.add(path)
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            failures.add(path)
            return None
    failures.add(path)
    return None


def _as_bool(value: Any, path: str, failures: set[str]) -> bool | None:  # noqa: ANN401 -- raw YAML value of unknown shape
    if value is None:
        return None
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return bool(value)  # ruamel's ScalarBoolean subclasses int, not bool
    if isinstance(value, str) and value.lower() in {"true", "false", "yes", "no"}:
        return value.lower() in {"true", "yes"}
    failures.add(path)
    return None


def _as_date(value: Any, path: str, failures: set[str]) -> date | None:  # noqa: ANN401 -- raw YAML value of unknown shape
    """Coerce ``str | date | datetime``.

    A YAML loader may have pre-parsed a date-shaped field or left it a string,
    depending only on whether the producer quoted it.
    """
    if value is None:
        return None
    if isinstance(value, datetime):  # must precede date: datetime subclasses it
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            failures.add(path)
            return None
    failures.add(path)
    return None


def _as_timestamp(
    value: Any,  # noqa: ANN401 -- raw YAML value of unknown shape
    path: str,
    failures: set[str],
) -> tuple[str | None, datetime | None]:
    """Return the ``(rendered, parsed)`` pair for a timestamp-shaped field."""
    if value is None:
        return None, None
    if isinstance(value, datetime):
        return value.isoformat(), value
    if isinstance(value, date):
        return value.isoformat(), datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return value, datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            failures.add(path)
            return value, None
    failures.add(path)
    return None, None


def _mapping(value: Any) -> Mapping[str, Any] | None:  # noqa: ANN401 -- raw YAML value of unknown shape
    return value if isinstance(value, Mapping) else None


def _is_listish(value: Any) -> bool:  # noqa: ANN401 -- raw YAML value of unknown shape
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def _str_tuple(values: Any, path: str, failures: set[str]) -> tuple[str, ...]:  # noqa: ANN401 -- raw YAML sequence of unknown shape
    """Coerce a sequence of scalars to strings, recording what would not coerce.

    A blanket ``tuple(str(v) for v in values)`` turns a nested mapping into the
    plausible-looking string ``"{'a': 1}"`` with no trace that it was ever a
    mapping -- exactly the silent loss ``coercion_failures`` exists to prevent.
    Elements that will not coerce are dropped and their index recorded.
    """
    out: list[str] = []
    for i, value in enumerate(values):
        coerced = _as_str(value, f"{path}.{i}", failures)
        if coerced is not None:
            out.append(coerced)
    return tuple(out)


def _extra_of(raw: Mapping[str, Any], known: frozenset[str] | tuple[str, ...]) -> Mapping[str, Any]:
    """Mirror the unknown keys of *raw*.

    Returned read-only: the values are the live objects from ``fm_raw``, not
    copies, so an accidental ``fm.extra[k] = v`` would otherwise mutate the
    storage layer behind a memoized view's back. The proxy stops that at the
    top level. Nested containers reached through it are still live -- treat
    everything under ``extra`` as read-only and go through ``Document.set`` or
    ``fm_raw`` plus ``mark_dirty()`` to change anything.
    """
    return MappingProxyType({str(k): v for k, v in raw.items() if str(k) not in known})


_TRUST_KEYS = ("by", "at")
_WINDOW_KEYS = ("from", "to")
_SOURCE_KEYS = (
    "id",
    "resource",
    "title",
    "author",
    "usage_count",
    "last_modified",
    "usage_window",
)
_PARAM_KEYS = ("name", "type", "required", "default")
_EXECUTOR_KEYS = ("resource", "receipt")


def _build_generated(
    value: Any,  # noqa: ANN401 -- raw YAML value of unknown shape
    path: str,
    failures: set[str],
) -> Generated | None:
    raw = _mapping(value)
    if raw is None:
        if value is not None:
            failures.add(path)
        return None
    at, at_dt = _as_timestamp(raw.get("at"), f"{path}.at", failures)
    return Generated(
        by=parse_actor(raw.get("by"), f"{path}.by", failures),
        at=at,
        at_dt=at_dt,
        extra=_extra_of(raw, _TRUST_KEYS),
    )


def _build_verified(
    value: Any,  # noqa: ANN401 -- raw YAML value of unknown shape
    path: str,
    failures: set[str],
) -> tuple[Verified, ...]:
    """Normalize ``verified`` per §5.2 — a bare mapping is a one-element list."""
    if value is None:
        return ()
    if isinstance(value, Mapping):
        entries: list[Any] = [value]
    elif _is_listish(value):
        entries = list(value)
    else:
        failures.add(path)
        return ()

    out: list[Verified] = []
    for i, entry in enumerate(entries):
        raw = _mapping(entry)
        if raw is None:
            failures.add(f"{path}.{i}")
            continue
        at, at_dt = _as_timestamp(raw.get("at"), f"{path}.{i}.at", failures)
        out.append(
            Verified(
                by=parse_actor(raw.get("by"), f"{path}.{i}.by", failures),
                at=at,
                at_dt=at_dt,
                extra=_extra_of(raw, _TRUST_KEYS),
            )
        )
    return tuple(out)


def _build_usage_window(
    value: Any,  # noqa: ANN401 -- raw YAML value of unknown shape
    path: str,
    failures: set[str],
) -> UsageWindow | None:
    raw = _mapping(value)
    if raw is None:
        if value is not None:
            failures.add(path)
        return None
    return UsageWindow(
        from_=_as_date(raw.get("from"), f"{path}.from", failures),
        to=_as_date(raw.get("to"), f"{path}.to", failures),
        extra=_extra_of(raw, _WINDOW_KEYS),
    )


def _build_sources(
    value: Any,  # noqa: ANN401 -- raw YAML value of unknown shape
    path: str,
    failures: set[str],
) -> tuple[Source, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        entries: list[Any] = [value]
    elif _is_listish(value):
        entries = list(value)
    else:
        failures.add(path)
        return ()

    out: list[Source] = []
    for i, entry in enumerate(entries):
        raw = _mapping(entry)
        if raw is None:
            failures.add(f"{path}.{i}")
            continue
        out.append(
            Source(
                id=_as_str(raw.get("id"), f"{path}.{i}.id", failures),
                resource=_as_str(raw.get("resource"), f"{path}.{i}.resource", failures),
                title=_as_str(raw.get("title"), f"{path}.{i}.title", failures),
                author=parse_actor(raw.get("author"), f"{path}.{i}.author", failures),
                usage_count=_as_int(raw.get("usage_count"), f"{path}.{i}.usage_count", failures),
                last_modified=_as_date(raw.get("last_modified"), f"{path}.{i}.last_modified", failures),
                usage_window=_build_usage_window(raw.get("usage_window"), f"{path}.{i}.usage_window", failures),
                extra=_extra_of(raw, _SOURCE_KEYS),
            )
        )
    return tuple(out)


def _build_parameters(
    value: Any,  # noqa: ANN401 -- raw YAML value of unknown shape
    path: str,
    failures: set[str],
) -> tuple[Parameter, ...]:
    if value is None:
        return ()
    if not _is_listish(value):
        failures.add(path)
        return ()

    out: list[Parameter] = []
    for i, entry in enumerate(value):
        raw = _mapping(entry)
        if raw is None:
            failures.add(f"{path}.{i}")
            continue
        out.append(
            Parameter(
                name=_as_str(raw.get("name"), f"{path}.{i}.name", failures),
                type=_as_str(raw.get("type"), f"{path}.{i}.type", failures),
                required=_as_bool(raw.get("required"), f"{path}.{i}.required", failures),
                default=raw.get("default"),
                extra=_extra_of(raw, _PARAM_KEYS),
            )
        )
    return tuple(out)


def _build_executor(
    value: Any,  # noqa: ANN401 -- raw YAML value of unknown shape
    path: str,
    failures: set[str],
) -> Executor | None:
    raw = _mapping(value)
    if raw is None:
        if value is not None:
            failures.add(path)
        return None
    receipt: tuple[str, ...] = ()
    receipt_raw = raw.get("receipt")
    if receipt_raw is not None:
        if _is_listish(receipt_raw):
            receipt = _str_tuple(receipt_raw, f"{path}.receipt", failures)
        else:
            failures.add(f"{path}.receipt")
    return Executor(
        resource=_as_str(raw.get("resource"), f"{path}.resource", failures),
        receipt=receipt,
        extra=_extra_of(raw, _EXECUTOR_KEYS),
    )


def _build_attester(
    value: Any,  # noqa: ANN401 -- raw YAML value of unknown shape
    path: str,
    failures: set[str],
) -> Attester | None:
    raw = _mapping(value)
    if raw is None:
        if value is not None:
            failures.add(path)
        return None
    return Attester(
        resource=_as_str(raw.get("resource"), f"{path}.resource", failures),
        extra=_extra_of(raw, ("resource",)),
    )


def _scan_citations(body: str) -> tuple[Source, ...]:
    """Collect a v0.1 body ``# Citations`` list.

    Runs on ``_md``'s token stream, so a ``#`` inside a fenced block can no
    longer end the scan early and an item inside one is no longer collected --
    the two limits documented when this was a regex scanner. The item text is
    now the item's full inline source, including lazily-continued lines, where
    the regex scanner truncated at the first line; this is a deliberate
    consequence of moving to the token stream and is the same class of
    improvement as the fence fix.

    The consumer-visible contract is otherwise unchanged: the result is still
    flagged in ``Frontmatter.fallbacks``, and an item that is not a markdown
    link still becomes ``Source(title=text, resource=text)``. Changing that
    second shape is a migration question, not a parser one.
    """
    out: list[Source] = []
    for item in _md.list_items_under(_md.parse_body(body), "citations"):
        if item.link_target is not None:
            out.append(Source(title=item.link_label or None, resource=item.link_target))
        else:
            out.append(Source(title=item.text, resource=item.text))
    return tuple(out)


def build_frontmatter(fm_raw: Mapping[str, Any], *, body: str = "") -> Frontmatter:
    """Build the typed view. **Never raises.**

    *body* feeds the ``# Citations`` read fallback (see
    ``_scan_citations``): when a document has no ``sources`` in its
    frontmatter, *body* is scanned for a v0.1-era citations list.
    """
    failures: set[str] = set()
    fallbacks: set[str] = set()

    tags: tuple[str, ...] = ()
    tags_raw = fm_raw.get("tags")
    if tags_raw is not None:
        if _is_listish(tags_raw):
            tags = _str_tuple(tags_raw, "tags", failures)
        else:
            failures.add("tags")

    generated = _build_generated(fm_raw.get("generated"), "generated", failures)
    # Read fallback: v0.1 wrote a top-level `timestamp` and no `generated`.
    if (generated is None or generated.at is None) and fm_raw.get("timestamp") is not None:
        at, at_dt = _as_timestamp(fm_raw.get("timestamp"), "timestamp", failures)
        generated = replace(generated or Generated(), at=at, at_dt=at_dt)
        fallbacks.add("generated.at")

    sources = _build_sources(fm_raw.get("sources"), "sources", failures)
    # Read fallback: v0.1 put citations in the body, not in `sources`.
    if not sources and body:
        cited = _scan_citations(body)
        if cited:
            sources = cited
            fallbacks.add("sources")

    return Frontmatter(
        type=_as_str(fm_raw.get("type"), "type", failures),
        title=_as_str(fm_raw.get("title"), "title", failures),
        description=_as_str(fm_raw.get("description"), "description", failures),
        resource=_as_str(fm_raw.get("resource"), "resource", failures),
        tags=tags,
        status=_as_str(fm_raw.get("status"), "status", failures),
        stale_after=_as_date(fm_raw.get("stale_after"), "stale_after", failures),
        generated=generated,
        verified=_build_verified(fm_raw.get("verified"), "verified", failures),
        sources=sources,
        usage_window=_build_usage_window(fm_raw.get("usage_window"), "usage_window", failures),
        runtime=_as_str(fm_raw.get("runtime"), "runtime", failures),
        parameters=_build_parameters(fm_raw.get("parameters"), "parameters", failures),
        executor=_build_executor(fm_raw.get("executor"), "executor", failures),
        attester=_build_attester(fm_raw.get("attester"), "attester", failures),
        computation=_as_str(fm_raw.get("computation"), "computation", failures),
        extra=_extra_of(fm_raw, KNOWN_KEYS),
        coercion_failures=frozenset(failures),
        fallbacks=frozenset(fallbacks),
    )
