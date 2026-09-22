#!/usr/bin/env python3
"""Move whole bundle directories per a declarative rules file, repairing every
inbound OKF reference.

One reusable, dry-run-first entry point for bundle restructures, so the next
relocation is a reviewed YAML diff rather than a bespoke script. Its argument
is the bundle directory; it never resolves a workspace itself.

The name avoids `migrate` deliberately: `okf_io/migrate.py` is the unrelated
OKF v0.1 -> v0.2 format migration.

Design notes
------------
* **One `plan_move_many` over the union of every rule, never a
  `plan_move_dir` loop.** A reference *between* two members that both move
  has a new form depending on both the new base and the new target
  (`okf_ext/moves/plan.py:784-786`), so cross-linking lanes must expand into
  one mapping handed to one call. Destination collisions then come free from
  `_validate`'s `claimed` tracking as `dest-exists` -- nothing here
  re-implements them.
* **Two refusals the engine cannot see**, because both are facts about the
  *rules* rather than about the bundle: `rule-overlap` (two rules claim one
  source with different destinations, which a plain `dict` would let the
  later rule silently win) and `empty-rule` (a rule matched nothing *and* its
  destination holds nothing either -- the `references` versus `reference`
  slip). The destination half of `empty-rule` is what keeps a second run a
  clean no-op rather than a spurious refusal.
* **Reserved members are carried here, not by the engine.** `index.md` and
  `log.md` under a rule are refused as `reserved-source`
  (`plan.py:155-159`), and any refusal invalidates the whole plan
  (`MovePlan.ok`), so a naive directory expansion moves *nothing*. A lane
  index's entries are **relative**, so it moves correctly on its own: it is
  excluded from the mapping, hidden from the planning load via `ignore=`,
  renamed directly, and its inbound references repaired by a `plan_repair`
  computed against a **reloaded** bundle.
* **Directory rules only.** Glob-with-template and regex rule kinds are out
  of scope for v1; `expand` is the seam they would be added behind.
* **Indexes, `log.md` and `gw config sync` are not this script's concern.**
  It moves files and repairs links, then prints the follow-up commands.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from okf_ext import moves
from okf_ext.moves import MovePlan, MoveResult
from okf_ext.schemas import DEFAULT_IGNORE as _SCHEMA_IGNORE
from okf_ext.shape import DEFAULT_IGNORE as _SECTIONS_IGNORE
from okf_io import Bundle, load_bundle
from okf_io.bundle import INDEX_NAME, LOG_NAME, canonical_id
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

#: The `ignore=` recipe every load here uses, byte-identical to both
#: `doc_wiki_okf.archive.ARCHIVE_IGNORE` (`archive.py:89`) and
#: `work_tracker_okf.items.ARCHIVE_IGNORE` (`items.py:19`) -- two packages
#: independently arriving at the same rule: when handing a bundle to
#: `okf_ext.moves`, hide nothing that is content, because the planner never
#: reads `bundle.ignored` and would otherwise leave files behind
#: (`archive.py:84-88`).
#:
#: `*/.DS_Store` stays **visible**, deliberately (`archive.py:70-76`):
#: reading, it is not content; moving, it has to stay visible or
#: `okf_ext.moves` leaves it behind and the source directory never empties
#: for `apply` to prune.
IGNORE: tuple[str, ...] = (*_SCHEMA_IGNORE, *_SECTIONS_IGNORE)

#: The two filenames `okf_ext.moves` refuses to relocate, and which this
#: script therefore carries itself.
RESERVED: frozenset[str] = frozenset({INDEX_NAME, LOG_NAME})


@dataclass(frozen=True)
class Rule:
    """One directory move. Both ends bundle-relative posix, no trailing slash."""

    directory: str
    to: str


@dataclass(frozen=True)
class Refused:
    """One reason nothing will be written.

    `kind` is this script's own closed vocabulary -- `bad-rules`, `bad-rule`,
    `rule-overlap`, `empty-rule` -- plus any `moves.RefusalKind` forwarded
    from the engine, so a reader sees one refusal shape whichever layer
    produced it.
    """

    kind: str
    detail: str


class _BadRule(Exception):
    pass


def _directory(value: object, field: str) -> str:
    """Validate one end of a rule and return it canonically, or raise."""
    if not isinstance(value, str) or not value.strip():
        raise _BadRule(f"`{field}` must be a non-empty string")
    text = value.strip()
    if text.startswith("/"):
        raise _BadRule(f"`{field}: {text}` must be bundle-relative, not absolute")
    text = text.rstrip("/")
    if not text or ".." in text.split("/"):
        raise _BadRule(f"`{field}: {value}` must not contain a `..` segment")
    return text


def load_rules(path: Path) -> tuple[list[Rule], list[Refused]]:
    """Parse the rules file and validate its shape. No bundle is read.

    An unknown key is refused rather than ignored, for the reason
    `okf_ext.tags.vocabulary` gives about its own format: a silently-ignored
    key is the same silent-no-op failure class as a typo that still loads,
    just not the way the author meant.
    """
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except YAMLError as exc:
        return [], [Refused("bad-rules", f"{path}: not valid YAML: {exc}")]

    entries = data.get("moves") if isinstance(data, Mapping) else None
    if not isinstance(entries, Sequence) or isinstance(entries, str):
        return [], [Refused("bad-rules", f"{path}: needs a top-level `moves:` list")]

    rules: list[Rule] = []
    refusals: list[Refused] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            refusals.append(Refused("bad-rule", f"moves[{index}] is not a mapping"))
            continue
        unknown = sorted(set(entry) - {"dir", "to"})
        if unknown:
            refusals.append(Refused("bad-rule", f"moves[{index}] has unknown key(s): {', '.join(unknown)}"))
            continue
        try:
            rules.append(Rule(_directory(entry.get("dir"), "dir"), _directory(entry.get("to"), "to")))
        except _BadRule as exc:
            refusals.append(Refused("bad-rule", f"moves[{index}]: {exc}"))
    return rules, refusals


@dataclass(frozen=True)
class Expansion:
    """What the rules mean against one bundle.

    `ordinary` is the mapping handed to `plan_move_many`. `reserved` holds the
    `index.md` / `log.md` members under a rule, which this script carries
    itself -- see the module docstring.
    """

    ordinary: Mapping[str, str]
    reserved: Mapping[str, str]
    refusals: tuple[Refused, ...]


def _member_paths(bundle: Bundle) -> tuple[str, ...]:
    """Every member as a bundle-relative posix path, sorted.

    A deliberate four-line duplicate of the private
    `okf_ext.moves.plan._all_member_paths` (`plan.py:860-866`) rather than an
    import of it: a script does not reach into another package's private
    surface, and the shape is small enough that mirroring it costs less than
    the coupling would. If it drifts, `plan_move_many` refuses the mapping as
    `not-a-member` rather than moving the wrong thing.
    """
    found = {f"{cid}.md" for cid in bundle.concepts}
    found.update(bundle.assets)
    found.update(f"{directory}/{INDEX_NAME}" if directory else INDEX_NAME for directory in bundle.indexes)
    found.update(f"{directory}/{LOG_NAME}" if directory else LOG_NAME for directory in bundle.logs)
    return tuple(sorted(found))


def _segments(directory: str) -> list[str]:
    return [canonical_id(segment) for segment in directory.split("/") if segment]


def _rest_under(head: list[str], member: str) -> str | None:
    """The remainder of *member* beneath the canonical segments *head*, or None.

    Mirrors `plan_move_dir._under_prefix` (`plan.py:801-807`) segment by
    segment through `canonical_id`. That comparison is what anchors a rule at
    the bundle root -- a `references` rule cannot reach
    `work/<item>/references/` or `sources/references/` -- and what keeps a raw
    disk id in a different Unicode normalization form than the rule matching
    anyway (ADR 2026-08-21-member-identity).
    """
    parts = member.split("/")
    if len(parts) <= len(head):
        return None
    if [canonical_id(part) for part in parts[: len(head)]] != head:
        return None
    return "/".join(parts[len(head) :])


def expand(bundle: Bundle, rules: Sequence[Rule]) -> Expansion:
    """Turn directory rules into the `{old: new}` mapping the engine takes."""
    members = _member_paths(bundle)
    claims: dict[str, list[tuple[int, str]]] = {}
    refusals: list[Refused] = []

    for index, rule in enumerate(rules):
        head = _segments(rule.directory)
        matched = 0
        for member in members:
            rest = _rest_under(head, member)
            if rest is None:
                continue
            matched += 1
            claims.setdefault(member, []).append((index, f"{rule.to}/{rest}"))
        if matched:
            continue
        # The destination check is what keeps a second run over an
        # already-moved bundle a clean no-op rather than a spurious refusal.
        target = _segments(rule.to)
        if not any(_rest_under(target, member) is not None for member in members):
            refusals.append(
                Refused(
                    "empty-rule",
                    f"`{rule.directory}` matched no member and `{rule.to}` holds none either",
                )
            )

    mapping: dict[str, str] = {}
    for member, claimed in sorted(claims.items()):
        if len({dest for _index, dest in claimed}) > 1:
            named = ", ".join(f"`{rules[index].directory}` -> `{dest}`" for index, dest in claimed)
            refusals.append(Refused("rule-overlap", f"`{member}` is claimed by {len(claimed)} rules: {named}"))
            continue
        mapping[member] = claimed[0][1]

    reserved = {
        source: dest for source, dest in mapping.items() if PurePosixPath(source).name in RESERVED
    }
    ordinary = {source: dest for source, dest in mapping.items() if source not in reserved}
    return Expansion(ordinary=ordinary, reserved=reserved, refusals=tuple(refusals))
