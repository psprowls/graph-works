"""The three check regimes, keyed by who owns the file.

| Files | Absent | Present |
|---|---|---|
| **Scaffold** -- `index.md`, `log.md`, `_tags.yaml` | create | skip if valid; refuse if not |
| **Owned template** -- passed to `plan_install` | create | skip if byte-identical; refuse naming it if not |
| **Human config** -- the `seed_only` subset of those | create | skip, never compared |

The third regime exists because a configuration file is the human's from
birth: byte-comparing one and refusing on their own edits would invert the
ownership the refusal is there to protect. An install only ever *seeds* it.

**Validity, not identity, for the scaffold three.** `index.md` and `log.md`
change by design -- a log gains an entry per sync, an index is reconciled as
pages come and go -- so byte-comparing them against a template would report a
difference on essentially every real bundle. They are checked for validity
instead: `index.md` parses and carries `okf_version`, `log.md` parses,
`_tags.yaml` parses as YAML and is a mapping (or empty).

That last predicate is `ruamel.yaml` and deliberately **not**
`okf_ext.tags.load_vocabulary`: capabilities never import each other. It is
also the weaker question this planner should be asking -- "can I scaffold into
this" should not turn on whether every tag definition is well-formed, which is
`vocabulary_rule`'s job and the caller's to run.

**Nothing here raises for a content reason.** A refusal is a `WriteFailure` in
the plan and an already-present file is a `Skipped`; both ride into
`ApplyResult` unchanged. `okf_io`'s never-raise rule, inherited.

This module imports `okf_io`, `ruamel.yaml`, its own capability's model, and
the shared write layer. It imports no sibling capability.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date
from pathlib import Path, PurePosixPath

from okf_io import append_log_entry, parse
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from okf_ext.bundle.model import (
    DECLARATION_MEMBERS,
    DECLARATION_PREFIXES,
    EMPTY_TAGS_YAML,
    SCAFFOLD_MEMBERS,
    InstallPlan,
    PlannedFile,
    ScaffoldPlan,
)
from okf_ext.writing import Skipped, WriteFailure

#: The scaffold's first log entry. Neutral on purpose: tier 2 must not name a
#: tier-3 package, and in a bundle three of them share, scaffolding is the one
#: act none of them owns. Each package logs its own arrival afterwards.
SCAFFOLD_LOG_ENTRY = "bundle scaffold created"

#: §8's single exception: the one key a bundle-root index may carry, and the
#: one this planner reads to decide a root is an OKF bundle root at all.
_VERSION_KEY = "okf_version"


def _target(root: Path, declarations_dir: Path, member: str) -> Path:
    """Where *member* lands. Declaration members go to `declarations_dir`;
    every other member goes to the bundle root."""
    if member in DECLARATION_MEMBERS or member.startswith(DECLARATION_PREFIXES):
        return declarations_dir / member
    return root / member


def _skip(member: str, detail: str) -> Skipped:
    """`concept_id` is empty on purpose: nothing this capability writes is a
    concept, so there is no id to carry, and `path` is the identity a reader
    of the result actually needs."""
    return Skipped(concept_id="", path=member, reason="already-present", detail=detail)


def _index_text(name: str) -> str:
    return f"---\n{_VERSION_KEY}: 0.2\n---\n\n# {name}\n"


def _log_text(today: date) -> str:
    return append_log_entry(parse(""), SCAFFOLD_LOG_ENTRY, today=today, dry_run=True).after


def _index_problem(text: str) -> str | None:
    document = parse(text)
    if document.parse_error is not None:
        return f"does not parse ({document.parse_error.kind}): {document.parse_error.message}"
    if _VERSION_KEY not in document.fm_raw:
        return f"carries no `{_VERSION_KEY}`, so this is not an OKF bundle root"
    return None


def _log_problem(text: str) -> str | None:
    document = parse(text)
    if document.parse_error is not None:
        return f"does not parse ({document.parse_error.kind}): {document.parse_error.message}"
    return None


def _tags_problem(text: str) -> str | None:
    try:
        loaded = YAML(typ="safe").load(text)
    except (YAMLError, RecursionError) as exc:
        # Deeply nested YAML blows the parser's stack. That is a property of
        # the content, so it must not escape.
        return f"is not valid YAML: {exc}"
    if loaded is not None and not isinstance(loaded, dict):
        return f"is a {type(loaded).__name__}, not a mapping"
    return None


def plan_scaffold(
    root: str | Path,
    *,
    today: date,
    declarations_dir: str | Path | None = None,
) -> ScaffoldPlan:
    """Plan the three files every bundle has, whoever installs into it.

    `today` is injected -- this module never reads the clock. Calling this
    against a bundle that already carries all three yields an empty plan, so
    a second package's scaffold pass is a no-op rather than a refusal, which
    is the whole reason this sits at tier 2 rather than inside one installer.

    `declarations_dir` defaults to *root*, which is today's layout unchanged.
    """
    root = Path(root)
    declarations = Path(declarations_dir) if declarations_dir is not None else root

    writes: list[PlannedFile] = []
    skipped: list[Skipped] = []
    refusals: list[WriteFailure] = []

    for member, content, problem_of in (
        ("index.md", _index_text(root.name), _index_problem),
        ("log.md", _log_text(today), _log_problem),
        ("_tags.yaml", EMPTY_TAGS_YAML, _tags_problem),
    ):
        path = _target(root, declarations, member)
        if not path.exists():
            writes.append(PlannedFile(member=member, path=path, content=content))
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            refusals.append(WriteFailure(path=member, kind="unwritable", error=f"{path}: {exc}"))
            continue
        problem = problem_of(text)
        if problem is None:
            skipped.append(_skip(member, f"{path}: present and valid; left as it is"))
        else:
            refusals.append(WriteFailure(path=member, kind="foreign-content", error=f"{path}: {problem}"))

    return ScaffoldPlan(
        root=root,
        declarations_dir=declarations,
        writes=tuple(writes),
        skipped=tuple(skipped),
        refusals=tuple(refusals),
    )


def _normalize_member(raw: str) -> str:
    """*raw* as a clean bundle-relative posix path, or `""` when it is not one.

    Modeled on `proposals.plan._normalize_target` -- capabilities never import
    each other, so the shape is duplicated rather than shared. A leading `/`
    is stripped (there is no root-relative addressing mode to preserve here,
    unlike a proposal's `target`); `PurePosixPath.parts` already collapses `.`
    segments on its own, so only `..` needs handling here, and a `..` that
    would climb above the root returns `""` rather than clamping.

    A NUL is rejected outright: the OS refuses it in a path, so leaving it to
    the write would turn a screenable member name into an uncaught `ValueError`
    from `open()` -- the one thing this check exists to prevent.
    """
    if "\x00" in raw:
        return ""
    cleaned = raw.strip().replace("\\", "/").lstrip("/")
    if not cleaned:
        return ""
    parts: list[str] = []
    for part in PurePosixPath(cleaned).parts:
        if part == "..":
            if not parts:
                return ""
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def plan_install(
    root: str | Path,
    files: Mapping[str, str],
    *,
    seed_only: Collection[str] = (),
    declarations_dir: str | Path | None = None,
) -> InstallPlan:
    """Plan one package's own files into *root*, additively.

    `files` maps bundle-relative posix path to content, so a tier-3 package
    supplies its assets and inherits the skip/refuse semantics rather than
    reimplementing them. Three packages each writing their own
    byte-compare-and-refuse is exactly the duplication the shared write layer
    was hoisted out to prevent, one layer up -- and the failure mode is the
    same one: three implementations meaning three slightly different things by
    the word "refused".

    A member named in *seed_only* is **human configuration**: created when
    absent, skipped when present, never compared. Byte-comparing a human's own
    configuration file and refusing on their edits would invert the ownership
    the refusal exists to protect. Everything else is an **owned template**:
    created when absent, skipped when byte-identical, refused -- naming the
    file, `kind="foreign-content"` -- when it differs.

    A member in `SCAFFOLD_MEMBERS` (`index.md`, `log.md`, `_tags.yaml`) is
    refused the same way, `kind="not-a-member"`: those three are the
    scaffold's to write, and a tier-3 package shipping one of its own would
    collide with `write_all`'s all-or-nothing create-probe regime and take
    every sibling write down with it. Call `plan_scaffold` for them instead.

    A `member` that is not already a clean, in-root, bundle-relative posix
    path is refused as `kind="not-a-member"` -- naming exactly what the caller
    passed -- before it is ever resolved against `root` or touches the
    filesystem. This covers an absolute member (`Path.__truediv__` discards
    `root` when the right-hand side is absolute), a `..`-climbing one, and one
    that is merely *not written clean* (`./a/../x`): the last is refused
    rather than planned at its normalized form, because silently relocating it
    would leave `InstallPlan.writes[].member` disagreeing with the key the
    caller passed. Note this widens `not-a-member` slightly: elsewhere it means
    a `concept_id` naming no member of a loaded bundle, and here it means a
    member string that is not shaped like a bundle path at all. Content that
    cannot be encoded as UTF-8 is refused as `kind="serialize-error"`, whether
    the target is absent or present, rather than raising -- content is never an
    exception here.

    `files` stays `Mapping[str, str]` and does not follow `PendingWrite.rendered`'s
    widening to `str | bytes` (ADR-0031). What this plans is a package's *own
    declaration assets* -- seed schemas, section declarations, scaffold templates
    -- which are text by construction, authored in the package's own `resources/`.
    The compare path encodes each value to UTF-8 precisely so it can byte-compare
    against what is on disk and refuse `foreign-content` on a human's edits; a
    bytes value would need a second branch there and would make the
    `serialize-error` refusal above unreachable. There is no caller asking for it.
    Widen it the day one appears, not before.

    Files are planned in the order *files* iterates, and `write_all` commits
    in the order it is given, so a caller who cares about write order controls
    it by the mapping it passes.
    """
    root = Path(root)
    declarations = Path(declarations_dir) if declarations_dir is not None else root
    seeds = frozenset(seed_only)

    writes: list[PlannedFile] = []
    skipped: list[Skipped] = []
    refusals: list[WriteFailure] = []

    for member, content in files.items():
        normalized = _normalize_member(member)
        if not normalized or normalized != member:
            refusals.append(
                WriteFailure(
                    path=member,
                    kind="not-a-member",
                    error=f"{member!r} is not a clean bundle-relative path; refusing rather than guessing where "
                    "it belongs",
                )
            )
            continue
        if normalized in SCAFFOLD_MEMBERS:
            refusals.append(
                WriteFailure(
                    path=member,
                    kind="not-a-member",
                    error=f"{member} belongs to the scaffold; call plan_scaffold instead",
                )
            )
            continue
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            # Caught here, once, ahead of both the create and compare paths --
            # an absent target would otherwise carry this same content into
            # `write_all`'s own encode with no plan-time refusal to show for it.
            refusals.append(WriteFailure(path=member, kind="serialize-error", error=f"{member}: {exc}"))
            continue
        path = _target(root, declarations, member)
        if not path.exists():
            writes.append(PlannedFile(member=member, path=path, content=content))
            continue
        if member in seeds:
            skipped.append(_skip(member, f"{path}: human-owned configuration, already present; never compared"))
            continue
        try:
            on_disk = path.read_bytes()
        except OSError as exc:
            refusals.append(WriteFailure(path=member, kind="unwritable", error=f"{path}: {exc}"))
            continue
        if on_disk == encoded:
            skipped.append(_skip(member, f"{path}: already present, byte-identical to the seed"))
        else:
            refusals.append(
                WriteFailure(
                    path=member,
                    kind="foreign-content",
                    error=f"{path}: exists with content this package did not write; not overwriting it",
                )
            )

    return InstallPlan(
        root=root,
        declarations_dir=declarations,
        writes=tuple(writes),
        skipped=tuple(skipped),
        refusals=tuple(refusals),
    )
