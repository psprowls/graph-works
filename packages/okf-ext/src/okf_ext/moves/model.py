"""Frozen values the moves capability returns.

All frozen and slotted, matching the core. `WriteFailure` and `FailureKind`
are **not** here: they come from `okf_ext.writing`, the shared layer, so a
caller discriminating a write failure has one type to match rather than one
per capability (spec §10).

`MoveResult` stays moves-specific -- it carries the moved pairs and the pruned
directories, which `ApplyResult` has no reason to model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from okf_ext.writing import WriteFailure

#: Why one move, or one referring document, was refused at plan time. A closed
#: vocabulary: every refusal a planner can produce is one of these, and any
#: refusal makes the whole plan not `ok` (spec §8).
#:
#: `reserved-dest` is a deliberate extension beyond the original nine: it is
#: `reserved-source`'s rule -- "`index.md` and `log.md` are reserved and never
#: move" -- read from the other direction. Guarding only the source leaves a
#: move free to *fabricate* a reserved file (an ordinary concept landing at
#: `some/dir/index.md`), which `update_index()` would then reconcile as a
#: genuine index -- a bundle-invariant break caused by the one capability
#: whose job is not to cause them. `kind-change` does not catch this: it only
#: compares `.md`-ness, not reserved names.
RefusalKind = Literal[
    "not-a-member",
    "dest-exists",
    "reserved-source",
    "reserved-dest",
    "escapes-root",
    "same-path",
    "kind-change",
    "parse-error",
    "unlocatable-reference",
    "reference-definition",
]

#: Where a reference lives. A body edit is a `(line, column)` span; a
#: frontmatter edit names a dotted key path and is position-free.
RefWhere = Literal["body", "frontmatter"]


@dataclass(frozen=True, slots=True)
class Move:
    """One member relocating. `source` and `dest` are bundle-relative posix."""

    source: str
    dest: str
    is_asset: bool  # a non-`.md` member: content is never edited, so it renames directly


@dataclass(frozen=True, slots=True)
class RefEdit:
    """One reference occurrence to rewrite.

    `target` is the **resolved** old target -- the bundle-relative posix path
    the reference pointed at before the move. It is what the count
    reconciliation in `plan` compares against `LinkGraph`, and the reason a
    rebase edit for an *unmoved* target never inflates that count.

    Body edits carry `line` (1-based, body-relative) and `column` (0-based,
    an index into that line as `okf_ext.body.split_lines` returns it); `old` and
    `new` are the destination text alone, never the whole line. A destination
    never spans lines -- an angle-bracket destination may not contain a line
    ending -- so the span is always well-defined.

    Frontmatter edits carry `key`, a dotted path with integer segments for
    list indices (`resource`, `executor.resource`, `sources.0.resource`), and
    leave `line`/`column` `None`.
    """

    member: str  # the file holding the reference, bundle-relative posix
    where: RefWhere
    target: str  # resolved bundle-relative posix path, before the move
    old: str
    new: str
    line: int | None = None
    column: int | None = None
    key: str | None = None


@dataclass(frozen=True, slots=True)
class Refusal:
    """One reason this plan will not be applied. Any refusal invalidates all of it."""

    path: str  # bundle-relative posix: the member the refusal is about
    kind: RefusalKind
    detail: str


@dataclass(frozen=True, slots=True)
class Stranded:
    """One inbound `[[wikilink]]` into the moved set that no repair can reach.

    Not a `Refusal`: a refusal is a judgment about markdown the planner *can*
    see, and any refusal makes the whole plan not-`ok`. A stranded wikilink is
    a fact about what the planner could not see, and never gates anything.
    """

    member: str  # the referrer, bundle-relative posix
    target: str  # the bundle-relative path it names, before the move
    line: int  # 1-based, document-relative


@dataclass(frozen=True, slots=True)
class Unrebased:
    """A moved member's own relative reference that was left exactly as written.

    A move cannot make a broken link less broken, but it *can* silently make
    it mean something different -- so a relative reference whose target is not
    a bundle member is reported rather than rewritten (spec §7.1).
    """

    member: str  # the moved member, by its **source** path
    raw: str  # the destination exactly as written
    detail: str


@dataclass(frozen=True, slots=True)
class MovePlan:
    """A preview you can inspect and filter before anything is written.

    A value rather than a `dry_run=True` flag, deliberately: a move touching
    40 documents and 917 references produces a preview you want to inspect,
    and "apply 915 of these 917" is a thing a boolean cannot express.

    `digests` maps a member path to the SHA-256 of the body this plan was
    computed against. `apply` refuses a document whose live body no longer
    matches: a change anywhere in a body can move the line an edit lands on,
    so a `(line, column, old)` span check alone would not catch every drift.
    Frontmatter edits are position-free and are checked by re-reading the key.

    `relocate` is `False` for a `plan_repair` plan: the references are
    repaired, no file is relocated, and no source is required to still exist.

    `stranded` counts the inbound `[[wikilink]]` references into the moved set
    that `moves` is deliberately blind to (it repairs OKF markdown links only).
    Reported, never rewritten, and never a reason a plan is not `ok`.
    """

    root: Path  # the bundle this was planned against
    moves: tuple[Move, ...]
    edits: tuple[RefEdit, ...]
    refusals: tuple[Refusal, ...]
    unrebased: tuple[Unrebased, ...]
    digests: Mapping[str, str]
    relocate: bool = True
    stranded: tuple[Stranded, ...] = ()

    @property
    def ok(self) -> bool:
        """False when there is any refusal. `apply` raises `ValueError` on a non-ok plan.

        In a batch, every edit is computed against the *whole* mapping.
        Silently dropping one bad move and applying the rest would apply edits
        computed against a mapping that is no longer the one being applied.
        The caller fixes the mapping and re-plans.
        """
        return not self.refusals

    @property
    def is_empty(self) -> bool:
        return not self.moves and not self.edits

    @property
    def members(self) -> tuple[str, ...]:
        """Every member whose *content* this plan writes, sorted, named by its
        **source** path.

        A moved markdown member counts: its own outbound references get
        rebased, and that content is read from -- and keyed by -- its source
        path, exactly as `digests` is (`digests[member] = body_digest(...)` at
        plan time, `digests.get(member)` at apply time). Naming it by its dest
        would not match either lookup. A referrer's member is unaffected by
        the move and simply keeps its one path.

        A moved *asset* is excluded: its content is never edited -- it only
        renames -- so nothing about it is written here.
        """
        touched = {edit.member for edit in self.edits}
        touched.update(move.source for move in self.moves if not move.is_asset)
        return tuple(sorted(touched))


@dataclass(frozen=True, slots=True)
class MoveResult:
    """What happened. **The authority on the outcome -- the caller reloads.**

    `tags.apply` reassigns `document.fm_raw` on success so `by_tag` stays
    coherent with disk. Moves cannot do the equivalent: a move changes the
    bundle's **key set**, and `Bundle.concepts` is a `MappingProxyType` built
    once by the walk. Rebinding `Document.path` while the mapping still keys
    the document by its old id would leave the bundle half-coherent, which is
    worse than not coherent. So nothing here updates the in-memory `Bundle`,
    and a caller that needs one reloads it.

    A `WriteFailure` with `kind="unlink-error"` or `kind="source-kept"` means
    an **orphan copy** survives at the old path -- never a silent success.
    Recovery is `plan_repair` on the residual mapping, then deleting the
    orphan.
    """

    moved: tuple[tuple[str, str], ...]  # (source, dest) pairs that landed
    written: tuple[str, ...]  # referrer members whose edits landed
    failed: tuple[WriteFailure, ...]
    pruned: tuple[str, ...]  # source directories removed because they emptied

    @property
    def ok(self) -> bool:
        return not self.failed


#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this.
__all__ = [
    "Move",
    "MovePlan",
    "MoveResult",
    "RefEdit",
    "RefWhere",
    "Refusal",
    "RefusalKind",
    "Stranded",
    "Unrebased",
]
