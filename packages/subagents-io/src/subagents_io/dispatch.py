"""The dispatch value types: what a planner hands a runner to start a worker.

Band 1's half of the worker-dispatch seam. Both types are inert data — frozen,
method-free, default-free. Their field names spell work-item concepts (`slug`,
`phase`, `kind`, `effort`) because that is what a dispatch record carries; this
package stores those strings and never decides what any of them means.

The two frozensets are the other half of the split. How a worktree is obtained
and how a worker relates to a human are *how to run a worker*, so they are
exported vocabularies rather than the trailing comments they were in the
source. The phase-to-mode mapping that picks one of them is a lifecycle fact
and stays a band up.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The legal values of `WorktreeAction.action`.
WORKTREE_ACTIONS: frozenset[str] = frozenset({"reuse", "fork-child", "create-top-level", "main", "pin-detached"})

#: The legal values of `PlannedDispatch.mode`.
DISPATCH_MODES: frozenset[str] = frozenset({"autonomous", "attend", "relay"})


@dataclass(frozen=True)
class WorktreeAction:
    """Where a dispatched worker runs, and on which branch.

    `"main"` is the repository's working checkout rather than a feature
    worktree. For an ordinary repository that is its declared path; for an
    in-bundle clone (`okf/repositories/<name>/references/git`) it is the
    declared `checkout` (by default `.gw/worktrees/<name>/<track>`), never the
    clone, which is scanned but not worked in. It carries a concrete `path`
    like `"reuse"` does. A worker in an ordinary repository's main checkout
    cannot infer worktree provenance from git: `--git-dir` and
    `--git-common-dir` agree there, so worktree detectors answer "not a
    worktree". An in-bundle checkout is itself a linked worktree of its clone.
    A planner that emits `"main"` owes the worker its path and branch
    explicitly in either case.

    `parent_path` names the existing worktree a *created* worktree
    (`fork-child`) is to be linked beneath, as data. It is `None` for every
    non-reader action that creates nothing, for a fresh top-level worktree,
    and for a fork whose source is the repository's own checkout. A backend must never
    fill it from wherever its caller happens to be running.

    `pin-detached` is prepared, verified and detached by the launcher before
    the worker starts; never a branch. The backend must refuse it if it cannot
    prepare one. Exactly this action carries a non-None `start_sha`, with
    `path=None`, `branch=None`, `exists=None`, `base_branch` naming the source
    branch and `parent_path` its source anchor worktree or None. Every other
    action carries `start_sha=None` and a non-None `branch`. Planners and
    backends enforce this invariant; this value type remains inert.
    """

    action: str  # one of WORKTREE_ACTIONS
    path: str | None  # concrete for "reuse" and "main"; None until the backend creates it
    branch: str | None
    base_branch: str | None  # source for fork-child / create-top-level / pin-detached
    exists: bool | None  # best-effort stat; None when the path is unknown or the stat failed
    parent_path: str | None  # the worktree a created one is linked beneath; None = no lineage
    start_sha: str | None  # full commit object ID a pin-detached reader must check out; None for every other action


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
    worktree: WorktreeAction
    merge_target: str
    prompt: str
    # The coordinator may answer this finish-relay's merge question itself: the
    # planner sets it only for a non-root item merging into its owner's
    # integration branch with merges unsupervised. Opaque to backends.
    auto_merge: bool
