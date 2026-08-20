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

One hard error, `ValueError` on an unknown slug — the same door
`commands.py:run_next` and `commands.py:_decision_context` open for a caller
mistake. Everything else degrades to partial evidence plus a warning: a
`ReconcileContext` carrying warnings and empty groups is a success, not a
failure.

"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from okf_io import load_bundle
from work_tracker_okf import anchors
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.hierarchy import nearest_epic
from work_tracker_okf.items import IGNORE, WorkItem, load_items
from work_tracker_okf.paths import artifact_path, decisions_ledger, item_page
from work_tracker_okf.vocabulary import SPEC_SOURCE_ID, TERMINAL_STATUSES

from graph_works_core.workspace import provenance
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import resolve_repo


@dataclass(frozen=True, slots=True)
class LandedSibling:
    """One item that has both gone terminal and left a ref behind."""

    slug: str
    resolved_in: str | None
    affects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CommitRef:
    """One commit in the range since the spec's anchor -- enough to name it in
    a report without re-invoking git for the subject line."""

    sha: str
    subject: str


@dataclass(frozen=True, slots=True)
class CitedDecision:
    """A `D-nnn` the spec cites, resolved against the epic's ledger.

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

    `anchor_source` is a closed vocabulary: `"spec-git-history"`,
    `"last-reconciled-heading"`, `"baseline-commit"`, `"none"`.

    The nested types are frozen dataclasses in tuples rather than `list[dict]`:
    the CLI's explicit per-command projection — not `dataclasses.asdict()` — is
    the public JSON contract, so the internal shape is free to be typed.
    """

    epic_slug: str
    slug: str
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


def _has_landed(item: WorkItem) -> bool:
    """Terminal AND carrying a ref. Terminal alone is not enough: a `wontfix`
    sibling changed no code and cannot have invalidated anything.

    Checks `workflow_status`, not `status`: `TERMINAL_STATUSES` is the
    work-lifecycle vocabulary (`resolved`/`wontfix`/`superseded`), while
    `WorkItem.status` is OKF's own document status (`draft`/`stable`/
    `deprecated`, `DOCUMENT_STATUSES`) -- a different axis entirely. Every
    other terminality check in this codebase (`hierarchy.py`,
    `dependencies.py`, `filing.py`, `workflow.py`, `_rules/state.py`,
    `_rules/graph.py`, `orchestrate/commands.py`) reads `workflow_status` for
    the same reason.
    """
    return item.workflow_status in TERMINAL_STATUSES and bool(item.resolved_in)


def _landed_siblings(items: Sequence[WorkItem], item: WorkItem, epic_slug: str | None) -> tuple[LandedSibling, ...]:
    """`depends_on` union (landed items sharing *item*'s nearest epic whose
    `affects` intersect its own).

    The declared arm is the coupling the author wrote down. The overlap arm
    catches undeclared coupling — two children editing the same files — while
    staying fully mechanical: a path-set intersection makes no judgment about
    which sibling "seems relevant", which is exactly what makes widening the
    scope safe. Epic membership comes from `hierarchy.nearest_epic`, so an item
    in a different epic never enters the overlap arm.

    Union by slug, so an item matching both arms appears once. Order follows
    *items*, which `load_items` returns sorted — the result is deterministic.
    """
    own_affects = set(item.affects)
    declared = {edge.slug for edge in item.depends_on}
    selected: dict[str, LandedSibling] = {}
    for other in items:
        if other.slug == item.slug or not _has_landed(other):
            continue
        overlaps = (
            epic_slug is not None
            and nearest_epic(items, other.slug) == epic_slug
            and bool(own_affects & set(other.affects))
        )
        if other.slug in declared or overlaps:
            selected[other.slug] = LandedSibling(slug=other.slug, resolved_in=other.resolved_in, affects=other.affects)
    return tuple(selected.values())


def _spec_ref(item: WorkItem) -> str:
    """*item*'s design spec, bundle-relative.

    `sources[]` first: it is where an adopted or relocated spec is recorded,
    and the conventional path computed unconditionally would miss it. The
    `archived=` flag on the fallback is what makes an archived item's spec
    findable at all — without it the lookup reads the active lane for a page
    that has moved.
    """
    for source in item.sources:
        if source.id == SPEC_SOURCE_ID and source.resource:
            return source.resource.lstrip("/")
    return artifact_path(item.slug, "design", "spec", archived=item.archived).rel


def _resolve_anchor(repo: Path | None, spec_path: Path, spec_text: str) -> tuple[str | None, str]:
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


def run_reconcile_context(
    layout: WorkspaceLayout,
    slug: str,
    *,
    repo_name: str | None = None,
    repo: Path | None = None,
) -> ReconcileContext:
    """Assemble the reconcile context for one work item. Read-only.

    *repo* is an explicit override for the code repository; when it is `None`,
    `resolve_repo(layout, repo_name=repo_name)` decides and its resolution note
    is folded into `warnings`. `resolve_repo`'s own refusals — an ambiguous or
    malformed `_repositories.yaml` — propagate as `WorkspaceError`; they are
    that function's closed contract, not a degrade this module invents around.

    Raises:
        ValueError: for a slug naming no work item. §3.2 of the consuming spec
            calls it a hard caller error, and it is the same door
            `commands.py:run_next` and `commands.py:_decision_context` already
            open.
    """
    bundle = load_bundle(layout.bundle_dir, ignore=IGNORE)
    items = load_items(bundle)
    item = next((candidate for candidate in items if candidate.slug == slug), None)
    if item is None:
        raise ValueError(f"unknown slug {slug!r}: no work item at {item_page(slug).path(bundle.root)}")

    warnings: list[str] = []
    if repo is None:
        repo, note = resolve_repo(layout, repo_name=repo_name)
        if note:
            warnings.append(note)
    if repo is None:
        warnings.append("no repo resolved; code drift unavailable")

    epic_slug = nearest_epic(items, slug)
    if epic_slug is None:
        warnings.append(f"no owning epic for {slug!r}; ledger drift unavailable")

    spec_path = bundle.root / _spec_ref(item)
    spec_text = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
    if not spec_text:
        warnings.append(f"no design spec at {spec_path}; nothing to reconcile against")

    anchor, anchor_source = _resolve_anchor(repo, spec_path, spec_text)
    if anchor is None and repo is not None:
        warnings.append(
            "could not resolve an anchor commit (spec untracked here, no `## Reconciled` heading, "
            "no `**Baseline commit:**` line); code drift is unscoped this pass"
        )
    commit_range = f"{anchor}..HEAD" if anchor else None

    landed_siblings = _landed_siblings(items, item, epic_slug)
    touched_paths = tuple(sorted({*item.affects, *(path for sibling in landed_siblings for path in sibling.affects)}))
    commits_since: tuple[CommitRef, ...] = ()
    diff_command: str | None = None
    if repo is not None and commit_range and touched_paths:
        commits_since = tuple(
            CommitRef(sha=sha, subject=subject)
            for sha, subject in provenance.commits_touching(repo, commit_range, touched_paths)
        )
        diff_command = f"git diff {commit_range} -- {' '.join(touched_paths)}"

    # ONE read. `commands.py:_has_open_decision` reloads the ledger from disk,
    # and a second read is a second non-atomic snapshot -- the precise
    # inconsistency `OrchestrateResult`'s docstring records for the orchestrate
    # path. Having the parse in hand makes reuse free and a re-read pure
    # downside.
    if epic_slug is None:
        parsed = _decisions.LedgerParse()
    else:
        epic_archived = next((candidate.archived for candidate in items if candidate.slug == epic_slug), False)
        parsed = _decisions.load(decisions_ledger(epic_slug, archived=epic_archived).path(bundle.root))
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
        epic_slug=epic_slug or "",
        slug=slug,
        spec_path=str(spec_path),
        spec_anchor_commit=anchor,
        anchor_source=anchor_source,
        commit_range=commit_range,
        landed_siblings=landed_siblings,
        touched_paths=touched_paths,
        commits_since=commits_since,
        cited_decisions=cited_decisions,
        contradictions=tuple(entry for entry in cited_decisions if entry.status == "superseded"),
        has_open_decision=bool(_decisions.query(entries, status="open", affects=slug)),
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
