"""The dispatch value types: what a planner hands a runner to start a worker.

Band 1's half of the worker-dispatch seam. Both types are inert data — frozen,
method-free, default-free. Their field names spell work-item concepts (`slug`,
`phase`, `kind`, `effort`) because that is what a dispatch record carries; this
package stores those strings and never decides what any of them means.

The frozenset is the other half of the split. How a worker relates to a human
is *how to run a worker*, so it is an exported vocabulary rather than a
trailing comment. The phase-to-mode mapping that picks one of them is a
lifecycle fact and stays a band up.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The legal values of `PlannedDispatch.mode`.
DISPATCH_MODES: frozenset[str] = frozenset({"autonomous", "attend", "relay"})


@dataclass(frozen=True)
class WorktreePlacement:
    """Where a dispatched worker runs: an existing path, decided before launch.

    A planner resolves placement; a backend only launches into `path` and never
    creates, forks or links a worktree. `branch` is the branch checked out
    there, or `None` when the path is detached or the branch is unknown.
    `parent_path` names the worktree this one lives beneath, as data, or `None`
    for no lineage. A backend must never fill it from wherever its caller
    happens to be running. This value type is inert.
    """

    path: str  # an existing directory the worker launches in
    branch: str | None  # the branch checked out at `path`; None = detached or unknown
    parent_path: str | None  # the worktree this one lives beneath; None = no lineage


@dataclass(frozen=True)
class PlannedDispatch:
    """One worker to launch, fully resolved."""

    key: str  # the worker's name, e.g. "gw-execute-feature-x-2f1a9c3d"; opaque to backends
    slug: str
    phase: str
    kind: str
    effort: str | None
    skill: str
    mode: str  # one of DISPATCH_MODES
    agent: str  # inert agent identifier; validated by the planner
    model: str | None  # None = use the selected agent's default
    reasoning_effort: str | None
    worktree: WorktreePlacement
    merge_target: str
    prompt: str
    # The coordinator may answer this finish-relay's merge question itself: the
    # planner sets it only for a non-root item merging into its owner's
    # integration branch with merges unsupervised. Opaque to backends.
    auto_merge: bool
