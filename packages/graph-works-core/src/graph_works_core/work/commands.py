"""Composed commands for the work-tracking vertical: `file`, `next`, `status`,
`lint`, `regen-index`, and decision-ledger CRUD -- what `work-tracker-okf`
ships as primitives plus `compose.py`, composed through a `WorkspaceLayout`
the way `archive/commands.py` already does for `plan_archive`/`apply_archive`.
This is the whole vertical this plan adds — `advance`, `archive` and
`sync-children` remain out of scope, as explained below.

Every function here takes a `WorkspaceLayout` and, where a schema/section
declaration is needed, a `code_wiki_okf.config.Config` for
`config.declarations_dir` -- the same pair `ingest/commands.py`,
`scan/commands.py` and `query/commands.py` already take, rather than
re-deriving a declarations path from `layout.bundle_dir` the way
`work-tracker-okf/cli.py`'s standalone `_sections`/`_rules` helpers do.

`advance`, `archive` and `sync-children` are deliberately absent.
`orchestrate/commands.py:run_stage_advance` already composes
`work_tracker_okf.compose.advance_and_stamp` with worktree provenance and a
results stub; `archive/commands.py:run_archive` already composes `archive`.
`sync-children` has no `WorkspaceLayout`-aware caller anywhere yet and is
left as a gap for a future item. Neither does `work-tracker-okf/cli.py`'s own
`init`, which this item does not touch.

Front-door note: none of this module's symbols are re-exported through
`graph_works_core/__init__.py`. `run_lint` would collide with the
already-hoisted `lint_drift.lint.run_lint`, and no consumer forces a naming
resolution yet -- `graph-works-cli`, the only planned caller, is a sibling
epic, and CLI wiring is explicitly out of scope here. Reachable at
`graph_works_core.work.commands.*` until that consumer decides how to
disambiguate.

Nothing here reads the clock: `on`/`today` are always caller-supplied, as
everywhere else in this package and in `work-tracker-okf`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from code_wiki_okf.config import Config
from okf_ext.shape import load_sections
from okf_io import IndexUpdate, load_bundle, update_index
from okf_io import validate as okf_validate
from okf_io.validate import Report
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.compose import FilingOutcome, file_and_reconcile, rule_set
from work_tracker_okf.decisions import UNSET, Decision, Unset
from work_tracker_okf.hierarchy import nearest_epic
from work_tracker_okf.items import IGNORE, WORK_DIR, WorkItem, load_items
from work_tracker_okf.paths import decisions_ledger
from work_tracker_okf.projection import ResumeSelection, Rollup, rollup, select_resume
from work_tracker_okf.workflow import RouteResult, RouteState, route, state_for

from graph_works_core.workspace.layout import WorkspaceLayout


def run_file(
    layout: WorkspaceLayout,
    config: Config,
    *,
    type: str,
    title: str,
    description: str,
    on: date,
    words: str | None = None,
    epic_child: bool = False,
    parent: str | None = None,
    depends_on: Sequence[str] = (),
    affects: Sequence[str] = (),
    tags: Sequence[str] = (),
    dry_run: bool = True,
) -> FilingOutcome:
    """File one work item, then reconcile `work/index.md` and log the arrival.

    A direct pass-through to `compose.file_and_reconcile`: no new behavior --
    the value this adds is that a future CLI never has to touch
    `work_tracker_okf.compose` or `okf_ext.shape` itself, matching every
    other vertical's `commands.py` being the sole composition surface.
    """
    return file_and_reconcile(
        layout.bundle_dir,
        type=type,
        title=title,
        description=description,
        on=on,
        words=words,
        epic_child=epic_child,
        parent=parent,
        depends_on=depends_on,
        affects=affects,
        tags=tags,
        section_set=load_sections(config.declarations_dir / "_sections"),
        dry_run=dry_run,
    )


@dataclass(frozen=True, slots=True)
class StatusReport:
    """The rollup and the item worth resuming, read together the way a
    status display wants them."""

    rollup: Rollup
    resume: ResumeSelection | None


def run_status(layout: WorkspaceLayout) -> StatusReport:
    """Count the active items and name the one worth resuming. Never writes."""
    items = load_items(load_bundle(layout.bundle_dir, ignore=IGNORE))
    return StatusReport(rollup=rollup(items), resume=select_resume(items))


@dataclass(frozen=True, slots=True)
class NextResult:
    """*slug*'s routing state, and what running it would do."""

    state: RouteState
    result: RouteResult


def _has_open_decision(items: Sequence[WorkItem], bundle_root: Path, slug: str) -> bool:
    """Whether an OPEN decision in *slug*'s owning epic's ledger names *slug*
    in its `affects` — the same resolution
    `orchestrate/commands.py:_held_decisions` already performs for the whole
    item set, reused here for one slug rather than restated.
    """
    epic = nearest_epic(items, slug)
    if epic is None:
        return False
    archived = next((item.archived for item in items if item.slug == epic), False)
    ledger = decisions_ledger(epic, archived=archived).path(bundle_root)
    entries = _decisions.load(ledger).entries
    return bool(_decisions.query(entries, status="open", affects=slug))


def run_next(layout: WorkspaceLayout, slug: str) -> NextResult | None:
    """What to dispatch for *slug*, and what completing it would change.

    `None` when *slug* names no item — mirrors `state_for`'s own `None`, and
    `work-tracker-okf/cli.py`'s "unknown slug" exit-1 case.

    Resolves `has_open_decision` for *this slug* specifically, not just "the
    epic has some open decision" — the behavioral improvement over
    `work-tracker-okf/cli.py`'s `next_stage`, which hardcodes
    `has_open_decision=False` and so can never report the design-stage
    "blocked: open decision" case for a real ledger.

    Never writes, never stamps. `effort=` override is not exposed here —
    `next_stage` doesn't take one either.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    state = state_for(items, slug, has_open_decision=_has_open_decision(items, bundle.root, slug))
    if state is None:
        return None
    return NextResult(state=state, result=route(state))


def run_lint(
    layout: WorkspaceLayout,
    config: Config,
    *,
    repo_root: Path | None = None,
    strict: bool = False,
    today: date,
) -> Report:
    """Report conformance and lane findings for this workspace's work bundle.
    Never writes.

    `compose.rule_set(layout.bundle_dir, repo_root=repo_root,
    declarations_dir=config.declarations_dir)` fed to `okf_io.validate` —
    single bundle, single lane. This is **not** a replacement for
    `lint_drift.lint.run_mechanical`'s two-lane workspace check; it is the
    fast, synchronous, LLM-free "does the work lane conform" check
    `work-tracker-okf/cli.py`'s `lint` already provides, now
    `WorkspaceLayout`-shaped. `repo_root=None` skips
    `targets.affects-missing` / `plan.action-target-missing`, the same
    contract `rule_set` documents.

    `strict=True` promotes every warning to an error first, which fails even
    a conformant vault (`work-tracker-okf/cli.py`'s own caution, C6-L) — a
    caller wiring this into an acceptance gate wants `strict=False`.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=_work_only_ignore(layout))
    rules = rule_set(layout.bundle_dir, repo_root=repo_root, declarations_dir=config.declarations_dir)
    return okf_validate(bundle, today=today, extra_rules=rules, strict=strict)


def _work_only_ignore(layout: WorkspaceLayout) -> tuple[str, ...]:
    """Every top-level bundle member except `work/`, plus the lane's own
    recipe — the same partition `lint_drift.lanes._work_ignore` computes for
    the combined workspace lint's work lane, duplicated rather than shared:
    `graph_works_core` verticals never import each other (the independence
    contract in the root `pyproject.toml`), so the two copies stay small and
    independently correct rather than reach across that forbidden edge.

    Without this, `load_bundle` walks the whole bundle — wiki content
    included — and `okf_io.validate`'s built-in catalog (links, lifecycle,
    frontmatter) reports on pages this function was never asked about,
    contradicting `run_lint`'s own "does the work lane conform" contract:
    a broken link in an unrelated concept page must not fail a work-item
    check.
    """
    siblings = tuple(
        f"{entry.name}/*" if entry.is_dir() else entry.name
        for entry in sorted(layout.bundle_dir.iterdir(), key=lambda path: path.name)
        if entry.name != WORK_DIR
    )
    return (*siblings, *IGNORE)


def run_regen_index(layout: WorkspaceLayout, *, dry_run: bool = True) -> IndexUpdate:
    """Rebuild `work/index.md` from what's on disk. Never touches `log.md` —
    this changes derived index content, not what the vault contains, the same
    rule `sync-children` follows.

    `create_missing=True`: a vault whose first act is `file` already gets one
    through `compose.file_and_reconcile`, but a bundle that predates this
    command would otherwise never get one.

    `update_index` always returns one `IndexUpdate` per requested directory;
    exactly one is requested here, so the tuple is unwrapped rather than
    handed back — a length-1 tuple would be dead API surface at every call
    site.
    """
    updates = update_index(
        load_bundle(layout.bundle_dir, ignore=IGNORE),
        directories=[WORK_DIR],
        create_missing=True,
        dry_run=dry_run,
    )
    return updates[0]


def _ledger_path(items: Sequence[WorkItem], bundle_root: Path, slug: str) -> Path:
    """The ledger *slug*'s decisions live in: its nearest epic ancestor's.

    Raises `ValueError` for a slug with no epic ancestor — a new failure
    mode these wrappers introduce (the underlying `decisions.*` functions
    take an explicit ledger path and never face this question), matching how
    `decisions.append` / `set_fields` / `supersede` already raise for caller
    error rather than returning a refusal value.
    """
    epic = nearest_epic(items, slug)
    if epic is None:
        raise ValueError(f"{slug!r} has no epic ancestor; the decisions ledger is per-epic")
    archived = next((item.archived for item in items if item.slug == epic), False)
    return decisions_ledger(epic, archived=archived).path(bundle_root)


def run_decision_add(
    layout: WorkspaceLayout,
    slug: str,
    *,
    question: str,
    status: str,
    affects: Sequence[str] = (),
    prose: str = "",
    decided: str | None = None,
    supersedes: str | None = None,
) -> Decision:
    """Append a decision to *slug*'s owning epic's ledger.

    Resolves the ledger from **the item's own slug**, not the epic's —
    `nearest_epic`, the same way `run_next` does. Zero callers of
    `decisions.append` exist anywhere in `graph-works-core` today;
    `orchestrate` only ever reads (`load`/`query`/`counts`).
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    ledger = _ledger_path(items, bundle.root, slug)
    return _decisions.append(
        ledger,
        question=question,
        status=status,
        affects=affects,
        prose=prose,
        decided=decided,
        supersedes=supersedes,
    )


def run_decision_answer(
    layout: WorkspaceLayout,
    slug: str,
    decision_id: str,
    *,
    question: str | Unset = UNSET,
    status: str | Unset = UNSET,
    affects: Sequence[str] | Unset = UNSET,
    decided: str | Unset | None = UNSET,
    supersedes: str | Unset | None = UNSET,
    prose: str | Unset = UNSET,
    prose_merge: Callable[[str], str] | None = None,
) -> Decision:
    """Update recognized fields on one entry in *slug*'s owning epic's
    ledger.

    Same signature as `decisions.set_fields`, plus the leading item slug,
    minus the ledger path.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    ledger = _ledger_path(items, bundle.root, slug)
    return _decisions.set_fields(
        ledger,
        decision_id,
        question=question,
        status=status,
        affects=affects,
        decided=decided,
        supersedes=supersedes,
        prose=prose,
        prose_merge=prose_merge,
    )


def run_decision_supersede(
    layout: WorkspaceLayout,
    slug: str,
    old_id: str,
    *,
    question: str,
    prose: str,
    decided: str | None = None,
    affects: Sequence[str] | None = None,
) -> tuple[Decision, Decision]:
    """Flip *old_id* to `superseded` and append its `answered` replacement in
    *slug*'s owning epic's ledger. Returns `(retired, replacement)`."""
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    ledger = _ledger_path(items, bundle.root, slug)
    return _decisions.supersede(ledger, old_id, question=question, prose=prose, decided=decided, affects=affects)


__all__ = [
    "NextResult",
    "StatusReport",
    "run_decision_add",
    "run_decision_answer",
    "run_decision_supersede",
    "run_file",
    "run_lint",
    "run_next",
    "run_regen_index",
    "run_status",
]
