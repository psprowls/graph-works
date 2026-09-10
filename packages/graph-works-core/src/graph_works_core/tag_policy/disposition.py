"""The reviewable disposition file: one YAML, three lists, every tag once.

Deliberately its own hand-edited format rather than a projection of
`tags.yaml`. The two answer different questions -- `tags.yaml` says what is
allowed *now*, this file says what to *do* about what exists -- and the
`reason` field is the audit trail an allow-list has nowhere to put.

**Strict, top level and per entry**, for exactly the reason
`okf_ext.tags.vocabulary` gives about the same class of file: this is a
small hand-edited artifact with no schema and no editor support, so an
unrecognized key is far more likely `in:` for `into:` than a
forward-compatible extension. A silently-ignored key here would mean a merge
that quietly becomes a keep.

**A caller-configuration error, always.** Every refusal raises; nothing
degrades. Bundle *content* is never at issue -- this file is the operator's
input, and okf-io's tolerance rule does not reach it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast, get_args

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from graph_works_core.tag_policy.model import Disposition, Reason, TagVerdict, Verdict

_SECTIONS: tuple[Verdict, ...] = ("keep", "merge", "strip")
_TOP_LEVEL_KEYS = frozenset({"generated", "tagged_pages", *_SECTIONS})
_ENTRY_KEYS = frozenset({"tag", "uses", "reason", "into"})
_REASONS = frozenset(get_args(Reason))


class DispositionError(ValueError):
    """A disposition file the operator got wrong.

    Subclasses `ValueError` so the CLI's existing `except (OSError,
    ValueError)` guard catches it with no new clause.
    """


def render(disposition: Disposition) -> str:
    """The file, as a human reviews it.

    Hand-formatted rather than `YAML().dump`-ed: the flow-style one-entry-
    per-line shape is the whole point of the review, and a round-tripping
    dumper reflows it. `loads` parses either form, so nothing depends on this
    formatting -- it is presentation only. `\\n` throughout; the caller writes
    with `newline=""`.
    """
    lines = [
        f"# generated {disposition.generated.isoformat()} from "
        f"{disposition.total_tags} distinct tags across {disposition.tagged_pages} tagged pages",
        "#",
        "# Every tag appears exactly once. Edit a `keep` into a `merge` (add `into:`",
        "# and set `reason: semantic`) where retention test 5 says two tags are one.",
        f"generated: {disposition.generated.isoformat()}",
        f"tagged_pages: {disposition.tagged_pages}",
    ]
    for section in _SECTIONS:
        entries = [v for v in disposition.verdicts if v.verdict == section]
        lines.append(f"{section}:")
        if not entries:
            lines[-1] = f"{section}: []"
            continue
        for entry in entries:
            into = f", into: {entry.into}" if entry.into is not None else ""
            lines.append(f"  - {{tag: {entry.tag}, uses: {entry.uses}, reason: {entry.reason}{into}}}")
    return "\n".join(lines) + "\n"


def _reject_unknown(mapping: Mapping[Any, Any], allowed: frozenset[str], where: str, noun: str) -> None:
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        raise DispositionError(f"{where}: unknown {noun} key(s) {unknown!r}; only {sorted(allowed)!r} are recognized")


def loads(text: str, source: str) -> Disposition:
    """Parse *text*, reporting every problem against *source*."""
    try:
        data = YAML(typ="safe").load(text)
    except YAMLError as exc:
        raise DispositionError(f"{source}: not valid YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise DispositionError(f"{source}: must be a YAML mapping, got {type(data).__name__}")
    _reject_unknown(data, _TOP_LEVEL_KEYS, source, "top-level")

    generated = data.get("generated")
    if not isinstance(generated, date):
        raise DispositionError(f"{source}: `generated` must be a YYYY-MM-DD date, got {generated!r}")
    tagged_pages = data.get("tagged_pages")
    # `bool` is an `int` in Python; type first, compare second -- the same
    # reasoning `load_vocabulary` applies to `version`.
    if not isinstance(tagged_pages, int) or isinstance(tagged_pages, bool) or tagged_pages < 0:
        raise DispositionError(f"{source}: `tagged_pages` must be a non-negative integer, got {tagged_pages!r}")

    seen: dict[str, str] = {}
    verdicts: list[TagVerdict] = []
    for section in _SECTIONS:
        raw = data.get(section) or []
        if isinstance(raw, str) or not isinstance(raw, Sequence):
            raise DispositionError(f"{source}: `{section}` must be a sequence, got {type(raw).__name__}")
        for position, entry in enumerate(raw):
            where = f"{source}: {section}[{position}]"
            if not isinstance(entry, Mapping):
                raise DispositionError(f"{where}: must be a mapping, got {type(entry).__name__}")
            _reject_unknown(entry, _ENTRY_KEYS, where, "entry")
            tag = entry.get("tag")
            if not isinstance(tag, str) or not tag.strip():
                raise DispositionError(f"{where}: missing a non-empty `tag`")
            tag = tag.strip()
            if tag in seen:
                raise DispositionError(f"{where}: `{tag}` is declared twice (already in `{seen[tag]}`)")
            seen[tag] = section
            uses = entry.get("uses")
            if not isinstance(uses, int) or isinstance(uses, bool) or uses < 0:
                raise DispositionError(f"{where}: `uses` must be a non-negative integer, got {uses!r}")
            reason = entry.get("reason")
            if reason not in _REASONS:
                raise DispositionError(f"{where}: `reason` must be one of {sorted(_REASONS)!r}, got {reason!r}")
            reason = cast(Reason, reason)
            into = entry.get("into")
            if into is not None and (not isinstance(into, str) or not into.strip()):
                raise DispositionError(f"{where}: `into` must be a non-empty string, got {into!r}")
            into = into.strip() if isinstance(into, str) else None
            if section == "merge" and into is None:
                raise DispositionError(f"{where}: a merge needs an `into` target")
            if section != "merge" and into is not None:
                raise DispositionError(f"{where}: `into` is only meaningful on a `merge` entry; `{tag}` is a {section}")
            verdicts.append(TagVerdict(tag=tag, uses=uses, verdict=section, reason=reason, into=into))

    kept = {v.tag for v in verdicts if v.verdict == "keep"}
    for entry in verdicts:
        if entry.verdict == "merge" and entry.into not in kept:
            raise DispositionError(
                f"{source}: `{entry.tag}` merges into `{entry.into}`, which is not kept; "
                f"a merge target must survive, or the merge only defers the strip"
            )

    verdicts.sort(key=lambda v: v.tag)
    return Disposition(
        generated=generated,
        total_tags=len(verdicts),
        tagged_pages=tagged_pages,
        verdicts=tuple(verdicts),
    )


def load(path: Path) -> Disposition:
    """Read the disposition at *path*.

    Propagates `OSError` for a path that is not there -- a different caller
    error, and not one to dress up as a format problem. Everything else is a
    `DispositionError`.
    """
    return loads(path.read_text(encoding="utf-8"), path.name)


__all__ = ["DispositionError", "load", "loads", "render"]
