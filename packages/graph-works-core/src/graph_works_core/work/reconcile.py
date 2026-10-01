"""`reconcile-context` — everything the `reconciling-spec` skill reads, in one call.

Pre-assembles the four evidence groups the skill needs before it does any
judgment work: the spec and its anchor commit, sibling drift, code drift, and
the ledger status of every decision the spec cites. The skill spends its
judgment on the diff rather than on re-deriving facts, and the facts stay
unit-testable without a model.

**Read-only.** Nothing here writes a page, an index, a log, a pointer or a
ledger entry.

It lives in the existing `work` vertical rather than in a `reconcile/` vertical
of its own: a new vertical would mean editing both import-linter contracts in
the root `pyproject.toml` to earn one command. It is a sibling of
`commands.py` rather than an addition to it because `commands.py`'s docstring
claims a different concern, and appending would push it past 550 lines.

**Synchronous.** The donor declared this `async def` and awaited nothing;
every entry point in `work/commands.py` is sync, and an async function with no
await is a promise a caller has to unwrap for no gain.

One hard error, `ValueError` on an unknown path — the same door
`commands.py:run_next` and `commands.py:_decision_context` open for a caller
mistake. Everything else degrades to partial evidence plus a warning: a
`ReconcileContext` carrying warnings and empty groups is a success, not a
failure.

"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from okf_io import load_bundle
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.affects import code_affects
from work_tracker_okf.decisions import ledger_ref
from work_tracker_okf.hierarchy import decision_owner
from work_tracker_okf.items import IGNORE, load_items
from work_tracker_okf.paths import item_page

from graph_works_core.workspace import provenance
from graph_works_core.workspace.anchor import resolve_anchor, spec_ref
from graph_works_core.workspace.dispatch_config import load_dispatch_config
from graph_works_core.workspace.landed import LandedSibling, landed_siblings
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_item_repo


@dataclass(frozen=True, slots=True)
class CommitRef:
    """One commit in the range since the spec's anchor -- enough to name it in
    a report without re-invoking git for the subject line."""

    sha: str
    subject: str


@dataclass(frozen=True, slots=True)
class CitedDecision:
    """A `D-nnn` the spec cites, resolved against the nearest owner's ledger.

    `status` is `"missing"` when nothing in the ledger carries that number —
    a real outcome, not an error: a spec can cite an id that never reached the
    ledger, through a typo or as an illustrative one. The skill decides what
    that means.
    """

    id: str
    status: str
    question: str


@dataclass(frozen=True, slots=True)
class ReconcileContext:
    """What has landed since a spec was written that might have invalidated it.

    `anchor_source` is a closed vocabulary: `"spec-baseline"`, `"spec-git-history"`,
    `"last-reconciled-heading"`, `"baseline-commit"`, `"none"`.

    The nested types are frozen dataclasses in tuples rather than `list[dict]`:
    the CLI's explicit per-command projection — not `dataclasses.asdict()` — is
    the public JSON contract, so the internal shape is free to be typed.
    """

    owner_path: str
    path: str
    spec_path: str
    spec_anchor_commit: str | None
    anchor_source: str
    commit_range: str | None
    landed_siblings: tuple[LandedSibling, ...] = ()
    touched_paths: tuple[str, ...] = ()
    commits_since: tuple[CommitRef, ...] = ()
    cited_decisions: tuple[CitedDecision, ...] = ()
    contradictions: tuple[CitedDecision, ...] = ()
    has_open_decision: bool = False
    diff_command: str | None = None
    warnings: tuple[str, ...] = ()


def run_reconcile_context(
    layout: WorkspaceLayout,
    path: str,
    *,
    repo_name: str | None = None,
    repo: Path | None = None,
) -> ReconcileContext:
    """Assemble the reconcile context for one work item. Read-only.

    *repo* is an explicit override for the code repository and, when given,
    wins outright. When it is `None`, `resolve_item_repo` decides, per item:
    the item's own `repo:` (inherited from its nearest physical ancestor)
    first, then *repo_name*, then strict resolution (one declared repo, or
    none). Its resolution note is folded into `warnings`. `resolve_item_repo`'s
    own refusals — a `repo:` naming nothing declared, a conflicting
    *repo_name*, or ambiguity with no `repo:` set anywhere in the chain —
    propagate as `WorkspaceError`; they are that function's closed contract,
    not a degrade this module invents around.

    Raises:
        WorkspaceError: dispatch configuration is missing or malformed, or repository selection is invalid.
        ValueError: for a path naming no work item. §3.2 of the consuming spec
            calls it a hard caller error, and it is the same door
            `commands.py:run_next` and `commands.py:_decision_context` already
            open.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    definition = load_dispatch_config(layout).definition
    items = load_items(bundle)
    item = next((candidate for candidate in items if candidate.path == path), None)
    if item is None:
        raise ValueError(f"unknown path {path!r}: no work item at {item_page(path).path(bundle.root)}")

    warnings: list[str] = []
    if repo is None:
        by_path = {candidate.path: candidate for candidate in items}
        resolved = resolve_item_repo(layout, item, by_path, repo_name=repo_name)
        repo = resolved.path
        if resolved.note:
            warnings.append(resolved.note)
    if repo is None:
        warnings.append("no repo resolved; code drift unavailable")

    owner_path = decision_owner(items, path)
    if owner_path is None:
        warnings.append(f"no decision owner for {path!r}; ledger drift unavailable")

    spec_path = bundle.root / spec_ref(item, definition=definition)
    spec_text = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
    if not spec_text:
        warnings.append(f"no design spec at {spec_path}; nothing to reconcile against")

    anchor, anchor_source = resolve_anchor(
        repo, spec_path, spec_text, stamped=item.spec_baseline.code if item.spec_baseline else None
    )
    if anchor is None and repo is not None:
        warnings.append(
            "could not resolve an anchor commit (spec untracked here, no `## Reconciled` heading, "
            "no `**Baseline commit:**` line); code drift is unscoped this pass"
        )
    commit_range = f"{anchor}..HEAD" if anchor else None

    siblings = landed_siblings(items, item)
    touched_paths = tuple(
        sorted(
            {
                *code_affects(item.affects),
                *(path for sibling in siblings for path in code_affects(sibling.affects)),
            }
        )
    )
    commits_since: tuple[CommitRef, ...] = ()
    diff_command: str | None = None
    if repo is not None and commit_range and touched_paths:
        commits_since = tuple(
            CommitRef(sha=sha, subject=subject)
            for sha, subject in provenance.commits_touching(repo, commit_range, touched_paths)
        )
        diff_command = f"git diff {commit_range} -- {' '.join(touched_paths)}"

    # ONE read. `workspace.decision_owner.hold_for` reloads the ledger from
    # disk, and a second read is a second non-atomic snapshot -- the precise
    # inconsistency `OrchestrateResult`'s docstring records for the orchestrate
    # path. Having the parse in hand makes reuse free and a re-read pure
    # downside.
    if owner_path is None:
        parsed = _decisions.LedgerParse()
    else:
        parsed = _decisions.load(ledger_ref(owner_path).path(bundle.root))
    entries = parsed.entries
    warnings.extend(parsed.warnings)

    by_number = {entry.number: entry for entry in entries}
    cited: list[CitedDecision] = []
    for raw in _decisions.extract_cited_decisions(spec_text):
        entry = by_number.get(_decisions.id_number(raw))
        cited.append(
            CitedDecision(
                id=entry.id if entry else raw,
                status=entry.status if entry else "missing",
                question=entry.question if entry else "",
            )
        )
    cited_decisions = tuple(cited)

    return ReconcileContext(
        owner_path=owner_path or "",
        path=path,
        spec_path=str(spec_path),
        spec_anchor_commit=anchor,
        anchor_source=anchor_source,
        commit_range=commit_range,
        landed_siblings=siblings,
        touched_paths=touched_paths,
        commits_since=commits_since,
        cited_decisions=cited_decisions,
        contradictions=tuple(entry for entry in cited_decisions if entry.status == "superseded"),
        has_open_decision=bool(_decisions.holds_for(entries, path)),
        diff_command=diff_command,
        warnings=tuple(warnings),
    )


__all__ = [
    "CitedDecision",
    "CommitRef",
    "LandedSibling",
    "ReconcileContext",
    "run_reconcile_context",
]
