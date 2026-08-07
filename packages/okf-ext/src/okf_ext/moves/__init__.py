"""Member moves: plan a rename, repair every inbound reference, apply.

    from okf_ext import moves

    plan = moves.plan_move(bundle, "concepts/okf.md", "pages/okf.md")
    plan = moves.plan_move_dir(bundle, "concepts", "pages")
    plan = moves.plan_move_many(bundle, {"a.md": "b.md", "img/x.png": "assets/x.png"})
    plan = moves.plan_repair(bundle, {"work/old.md": "work/_archive/old.md"})

    result = moves.apply(bundle, plan)

A member's id in OKF v0.2 *is* its bundle-relative path, so moving a file
changes its id and silently invalidates every reference pointing at it. This
capability is what repairs them.

**Plan and apply stay two calls, not a `dry_run` flag** -- the same reason
`tags` gives: a move touching 40 documents produces a preview you want to
inspect and filter, and "apply 915 of these 917" is a thing a boolean cannot
express.

**Not `[[wikilink]]` forms.** A wikilink is not a link form OKF v0.2 defines
and `okf_io` does not see one, so nothing here can repair one. Converting
wikilinks to markdown links is a separate, upstream decision; once converted,
this capability sees them like any other destination.

**This module imports the shared `okf_ext.context`, `okf_ext.body` and
`okf_ext.writing` layers and nothing else from its own package.** It never
imports `okf_ext` itself -- that would invert the re-export direction and make
every capability load every other.
"""

from __future__ import annotations

from okf_ext.moves.apply import apply
from okf_ext.moves.model import (
    Move,
    MovePlan,
    MoveResult,
    RefEdit,
    Refusal,
    RefusalKind,
    RefWhere,
    Unrebased,
)
from okf_ext.moves.plan import (
    REFERENCE_KEYS,
    SOURCES_KEY,
    plan_move,
    plan_move_dir,
    plan_move_many,
    plan_repair,
)
from okf_ext.writing import FailureKind, WriteFailure

#: `FailureKind` and `WriteFailure` are re-exported from the shared
#: `okf_ext.writing` layer, not defined here -- matching `tags` and `tables`,
#: which re-export the same shared write vocabulary for the same reason: a
#: caller inspecting `MoveResult.failed[0].kind` should have one type to
#: import, not `okf_ext.writing` reached into directly. Only these two,
#: because they are the only shared write types `moves`' own surface
#: (`MoveResult.failed: tuple[WriteFailure, ...]`) actually exposes --
#: `moves` has no `Skipped`/`SkipReason`/`ApplyResult` in its return types, so
#: those stay unexported here.
#:
#: `okf_ext.moves.locate`'s `Candidate`, `RefDef`, `destinations` and
#: `reference_definitions` are deliberately **not** re-exported here. They are
#: internal machinery `plan.py` uses to build `RefEdit`s -- nothing in
#: `MovePlan` or `MoveResult` ever hands one back to a caller, unlike
#: `tables`' `read`/`read_all`, which ship because three named external
#: consumers use them directly. Shipping an unused surface pre-1.0 would only
#: bind a future removal to a breaking minor bump (ADR-0007) for a surface
#: nothing ever consumed. `locate` stays reachable as
#: `okf_ext.moves.locate.destinations` etc. for anything that genuinely needs
#: it, `test_moves_locate.py` included.
#:
#: Ordered UPPER_SNAKE_CASE constants, then CapWords, then lowercase
#: functions, each group alphabetical -- `RUF022` enforces exactly this, and
#: `test_ext_boundaries.py` checks the same invariant so a stale ordering
#: fails the suite too, not only an opt-in lint pass.
__all__ = [
    "REFERENCE_KEYS",
    "SOURCES_KEY",
    "FailureKind",
    "Move",
    "MovePlan",
    "MoveResult",
    "RefEdit",
    "RefWhere",
    "Refusal",
    "RefusalKind",
    "Unrebased",
    "WriteFailure",
    "apply",
    "plan_move",
    "plan_move_dir",
    "plan_move_many",
    "plan_repair",
]
