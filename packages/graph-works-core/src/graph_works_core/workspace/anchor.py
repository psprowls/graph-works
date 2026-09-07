"""Where a range starts: the anchor commit, by four arms of decreasing directness.

The policy over `provenance.py`'s primitives. It lives in `workspace/` — the
bottom layer — rather than in either vertical because both need it: `work`'s
reconcile pass and `orchestrate`'s stage advance, and the layers contract
forbids one vertical importing the other. Two modules rather than one:
`provenance.py` stays the primitives-and-git-runner module its docstring
claims, and this is the composition over it.

Nothing here raises, and every arm is verified before it is believed — a stale
or foreign sha degrades to "no range" rather than producing a bogus one.
"""

from __future__ import annotations

from pathlib import Path

from work_tracker_okf import anchors
from work_tracker_okf.items import WorkItem
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref
from work_tracker_okf.vocabulary import SPEC_SOURCE_ID

from graph_works_core.workspace import provenance


def spec_ref(item: WorkItem) -> str:
    """*item*'s design spec, bundle-relative.

    `sources[]` first: it is where an adopted or relocated spec is recorded,
    and the conventional path computed unconditionally would miss it -- an
    item whose spec was adopted from elsewhere or relocated after filing has
    no `sources[]`-independent way to find it otherwise. Falling back to the
    conventional artifact path covers every item that never needed an
    override.

    Shared by `work`'s reconcile pass and `orchestrate`'s stage advance --
    both need "where is this item's spec" and neither may import the other,
    so it lives here beside `resolve_anchor` rather than in either vertical.
    """
    for source in item.sources:
        if source.id == SPEC_SOURCE_ID and source.resource:
            return source.resource.lstrip("/")
    return artifact_ref(item.path, MANAGED_ARTIFACTS[SPEC_SOURCE_ID]).rel


def resolve_anchor(repo: Path | None, spec_path: Path, spec_text: str) -> tuple[str | None, str]:
    """`(anchor_sha, anchor_source)`. Three arms, decreasing directness.

    The spec's own git history is the anchor whenever the spec is tracked in
    the repository being diffed. In a split topology the workspace is a
    separate directory and the spec has no history there at all, so the anchor
    falls back to the code-repo shas a previous pass stamped into the spec:
    the head of its most recent `## Reconciled` heading, then its
    `**Baseline commit:**` line.

    **Every fallback sha is verified with `commit_exists` before use**, so a
    stale or foreign sha degrades to "no range" rather than producing a bogus
    one.
    """
    if repo is None:
        return None, "none"
    tracked = provenance.spec_anchor_commit(repo, spec_path)
    if tracked:
        return tracked, "spec-git-history"
    for candidate, source in (
        (anchors.last_reconciled_head(spec_text), "last-reconciled-heading"),
        (anchors.baseline_commit(spec_text), "baseline-commit"),
    ):
        if candidate and provenance.commit_exists(repo, candidate):
            return candidate, source
    return None, "none"


def phase_start_sha(repo: Path, spec_path: Path, spec_text: str) -> str | None:
    """Where a code phase started in *repo*: merge-base first, then the spec arms.

    The merge-base arm is the true phase start for the fork-per-item worktree
    model auto-drive creates, and it needs nothing stamped anywhere — which is
    what makes it the only arm that fires for a freshly brainstormed spec in a
    split topology, where arm 1 has no history to read and arms 2-3 have no
    text to read. `repo` is the worktree the stage's commits actually landed
    in, so `HEAD` is already the item's branch.

    It degrades honestly in main-mode: on the base branch itself the merge base
    IS `HEAD`, giving an empty range, and `work_tracker_okf.results.render()`
    already emits an explicit "Range is empty" warning for exactly that.
    """
    candidate = provenance.merge_base(repo, provenance.default_base(repo), "HEAD")
    if candidate:
        return candidate
    return resolve_anchor(repo, spec_path, spec_text)[0]


__all__ = ["phase_start_sha", "resolve_anchor", "spec_ref"]
