"""Deterministic guidance assembly for `gw work next` (epic decision 8).

Candidates come from three streams in a fixed order -- claims, agent
sections, answered ledger decisions -- and are admitted in that order while
the rendered file stays within the token budget. Nothing is scored or
re-ranked. Nothing here raises for a content reason, and every read failure
is a warning on the returned `Guidance`. The one exception is the tokenizer:
`count_tokens` may fetch its encoding over the network, and a failure there
propagates as `TokenizerUnavailable` (a `ValueError`) so no stream mislabels
it as its own read failure; `run_next` turns it into a warning.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from code_graph_io.tokens import count_tokens
from okf_ext.bundle import SECTIONS_DIRNAME
from okf_ext.shape import SectionError, SectionSet, audience_view, load_sections
from okf_io import Bundle, Document
from work_tracker_okf import decisions as _decisions
from work_tracker_okf.decisions import ledger_ref, prose_block
from work_tracker_okf.hierarchy import declared_repo
from work_tracker_okf.items import WorkItem, item_index
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

from graph_works_core.guidance.claims import ClaimRow, read_claims, refresh_claims
from graph_works_core.guidance.closure import WARN_NO_REPO, Closure, ClosureEntry, match_claims
from graph_works_core.guidance.commands import _item_repo, open_closure
from graph_works_core.guidance.render import (
    Candidate,
    GuidanceEntry,
    claim_block,
    ledger_block,
    page_heading,
    render_guidance,
)
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repos import declared_repositories

__all__ = [
    "CUT_WARNING",
    "GUIDANCE_TOKEN_BUDGET",
    "MISSING_ANSWER_WARNING",
    "SKIPPED_CLAIMS_WARNING",
    "Guidance",
    "GuidanceEntry",
    "TokenizerUnavailable",
    "admit",
    "affects_overlap",
    "assemble_guidance",
    "build_guidance",
    "claim_candidates",
    "ledger_candidates",
    "section_candidates",
    "write_guidance",
]

GUIDANCE_TOKEN_BUDGET: Final = 3000
CUT_WARNING: Final = (
    "guidance cut at {budget} tokens: admitted {admitted} of {total} candidates; first dropped: {path}#{id}"
)
MISSING_ANSWER_WARNING: Final = "answered ledger entries with no **Answer:** paragraph, skipped: {count}"
SKIPPED_CLAIMS_WARNING: Final = (
    "malformed claim entries skipped by the claims index: {count} (see `gw wiki claims refresh`)"
)


class TokenizerUnavailable(ValueError):
    """`count_tokens` failed -- e.g. `o200k_base` is uncached and the download
    raised. Deliberately not an `OSError`, so the streams' read-failure
    handlers let it through instead of reporting it as their own."""


def _tokens(text: str) -> int:
    try:
        return count_tokens(text)
    except (OSError, ValueError) as exc:
        raise TokenizerUnavailable(f"token counting failed: {exc}") from exc


@dataclass(frozen=True, slots=True)
class Guidance:
    phase: str
    entries: tuple[GuidanceEntry, ...]  # admitted, in assembly order
    warnings: tuple[str, ...]
    rendered: str  # the file body; "" when entries is empty
    tokens: int  # count_tokens(rendered)


def admit(
    candidates: Sequence[Candidate], *, phase: str, item_path: str, budget: int
) -> tuple[tuple[Candidate, ...], str | None]:
    """Admit in order until the first candidate that would push the rendered
    file over *budget*; stop there. Skipping it for a later, smaller one would
    break the order epic decision 8 fixes."""
    admitted: list[Candidate] = []
    for candidate in candidates:
        if _tokens(render_guidance(phase, item_path, (*admitted, candidate))) > budget:
            return tuple(admitted), CUT_WARNING.format(
                budget=budget,
                admitted=len(admitted),
                total=len(candidates),
                path=candidate.entry.path,
                id=candidate.entry.id,
            )
        admitted.append(candidate)
    return tuple(admitted), None


def build_guidance(
    candidates: Sequence[Candidate],
    *,
    phase: str,
    item_path: str,
    warnings: Sequence[str] = (),
    budget: int = GUIDANCE_TOKEN_BUDGET,
) -> Guidance:
    admitted, cut = admit(candidates, phase=phase, item_path=item_path, budget=budget)
    rendered = render_guidance(phase, item_path, admitted)
    return Guidance(
        phase=phase,
        entries=tuple(candidate.entry for candidate in admitted),
        warnings=(*warnings, *(() if cut is None else (cut,))),
        rendered=rendered,
        tokens=_tokens(rendered) if rendered else 0,
    )


def write_guidance(guidance: Guidance, target: Path) -> Path | None:
    """Write *guidance* to *target*; `None`, writing nothing, when no entry was admitted.
    Propagates `OSError`: the caller turns it into a warning."""
    if not guidance.entries:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(guidance.rendered, encoding="utf-8", newline="\n")
    return target


def _member_link(concept_id: str) -> str:
    return f"/{concept_id}.md"


def _title(document: Document | None, fallback: str) -> str:
    title = None if document is None else document.fm.title
    return title if isinstance(title, str) and title.strip() else fallback


def claim_candidates(bundle: Bundle, rows: Sequence[ClaimRow], closure: Closure, *, phase: str) -> list[Candidate]:
    """Stream 1: matched, non-superseded rows carrying *phase*, in `match_claims` order `(tier, page, id)`."""
    candidates: list[Candidate] = []
    for match in match_claims(rows, closure):
        row = match.row
        if phase not in row.phases:
            continue
        path = _member_link(row.page)
        text = claim_block(row.id, row.claim, row.constrains)
        entry = GuidanceEntry(row.kind, path, row.id, match.why, text, _tokens(text))
        title = _title(bundle.concepts.get(row.page), row.page)
        candidates.append(Candidate("claims", page_heading(title, path), entry))
    return candidates


def _takes_sections(entry: ClosureEntry) -> bool:
    """Tier 0 files, tier 1 own packages/apps, tier 2 dependencies. A tier-2
    internal neighbour package contributes claims, never sections."""
    if entry.tier == 0:
        return entry.uri.startswith("file:")
    if entry.tier == 1:
        return entry.uri.startswith(("pkg:", "app:"))
    return entry.tier == 2 and entry.uri.startswith("dependency:")


def _pages_by_resource(bundle: Bundle) -> dict[str, tuple[str, Document]]:
    """`resource` -> `(concept id, document)` for every resource exactly one concept claims.

    The lookup half of `code_wiki_okf.resources.resource_index` -- same
    `fm.resource` read, same "absent or ambiguous is no hit" contract -- built
    from `bundle.concepts` alone. `resource_index` also walks the bundle
    directory for its filesystem-identity maps, which dominated assembly time
    and which this lookup never uses.
    """
    claimed: dict[str, list[tuple[str, Document]]] = {}
    for concept_id, document in bundle.concepts.items():
        resource = document.fm.resource
        if resource is None:
            continue
        claimed.setdefault(resource, []).append((concept_id, document))
    return {resource: pages[0] for resource, pages in claimed.items() if len(pages) == 1}


def section_candidates(bundle: Bundle, closure: Closure, sections: SectionSet, *, phase: str) -> list[Candidate]:
    """Stream 2: each closure page's agent sections for *phase*, pages in `(tier, uri)` order,
    sections in declaration order. A resource two pages claim contributes nothing."""
    pages = _pages_by_resource(bundle)
    candidates: list[Candidate] = []
    for closure_entry in closure.entries:  # already sorted (tier, uri)
        if not _takes_sections(closure_entry):
            continue
        hit = pages.get(closure_entry.uri)
        if hit is None:
            continue
        concept_id, document = hit
        declaration = sections.types.get(document.fm.type or "")
        if declaration is None:
            continue
        path = _member_link(concept_id)
        title = _title(document, concept_id)
        for view in audience_view(document.body, declaration, "agent", phase=phase):
            entry = GuidanceEntry("section", path, view.heading, closure_entry.why, view.text, _tokens(view.text))
            candidates.append(Candidate("sections", page_heading(title, path, view.heading), entry))
    return candidates


def _norm(path: str) -> str:
    return path.removeprefix("./").rstrip("/")


def _related(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def affects_overlap(a: Sequence[str], b: Sequence[str]) -> bool:
    """Some entry of *a* equals, is under, or contains some entry of *b*.

    A directory relation, not a string prefix: `packages/a` does not overlap `packages/ab`.
    """
    left = [_norm(p) for p in a if _norm(p)]
    right = [_norm(p) for p in b if _norm(p)]
    return any(_related(x, y) for x in left for y in right)


def ledger_candidates(
    bundle_root: Path, items: Sequence[WorkItem], item: WorkItem, *, fallback_repo: str | None = None
) -> tuple[list[Candidate], tuple[str, ...]]:
    """Stream 3: answered entries, Answer paragraph only, from the leaf's own ledger,
    its ancestors' (nearest first), then non-archived, non-terminal items in the
    same repository whose `affects` overlap the leaf's, sorted by path.

    "Same repository" compares resolved repos -- the nearest `repo:` over each
    item's ancestor chain (`declared_repo`), else *fallback_repo* -- so a child
    that inherits its parent's `repo:` counts, and the overlap agrees with the
    closure's repository resolution. Propagates `OSError` / `UnicodeDecodeError` from a
    ledger read; `assemble_guidance` drops the stream on those.
    """
    why: dict[str, str] = {item.path: "own ledger"}
    for ancestor in reversed(item.ancestor_paths):
        why.setdefault(ancestor, "ancestor ledger")
    index = item_index(tuple(items))

    def resolved(x: WorkItem) -> str | None:
        return declared_repo(x, index)[0] or fallback_repo

    repo = resolved(item)
    overlapping = sorted(
        other.path
        for other in items
        if other.path not in why
        and not other.archived
        and other.work_status not in TERMINAL_STATUSES
        and resolved(other) == repo
        and affects_overlap(other.affects, item.affects)
    )
    for path in overlapping:
        why[path] = f"affects overlap with {path}"

    titles = {other.path: other.title for other in items}
    candidates: list[Candidate] = []
    missing = 0
    for owner, relation in why.items():  # insertion order: own, ancestors, overlaps
        ref = ledger_ref(owner)
        group = page_heading(titles.get(owner) or owner, ref.resource)
        for decision in _decisions.load(ref.path(bundle_root)).entries:
            if decision.status != "answered":
                continue
            answer = prose_block(decision, "Answer")
            if not answer:
                missing += 1
                continue
            text = ledger_block(decision.id, decision.question, answer)
            entry = GuidanceEntry("ledger", ref.resource, decision.id, relation, text, _tokens(text))
            candidates.append(Candidate("ledger", group, entry))
    warnings = (MISSING_ANSWER_WARNING.format(count=missing),) if missing else ()
    return candidates, warnings


def _claims_stream(
    layout: WorkspaceLayout, bundle: Bundle, closure: Closure, phase: str, warnings: list[str]
) -> list[Candidate]:
    try:
        refresh = refresh_claims(bundle, layout.cache_dir)
        rows = read_claims(bundle, layout.cache_dir)
    except OSError as exc:
        warnings.append(f"claims index unavailable: {exc}; claims skipped")
        return []
    if refresh.skipped:
        warnings.append(SKIPPED_CLAIMS_WARNING.format(count=len(refresh.skipped)))
    return claim_candidates(bundle, rows, closure, phase=phase)


def _sections_stream(
    layout: WorkspaceLayout, bundle: Bundle, closure: Closure, phase: str, warnings: list[str]
) -> list[Candidate]:
    try:
        sections = load_sections(layout.config_dir / SECTIONS_DIRNAME)
    except (OSError, SectionError) as exc:
        warnings.append(f"section declarations unreadable: {exc}; agent sections skipped")
        return []
    return section_candidates(bundle, closure, sections, phase=phase)


def assemble_guidance(
    layout: WorkspaceLayout,
    bundle: Bundle,
    items: Sequence[WorkItem],
    item: WorkItem,
    *,
    phase: str,
    budget: int = GUIDANCE_TOKEN_BUDGET,
) -> Guidance:
    """Claims, agent sections and answered ledger decisions for *item* at *phase*,
    capped at *budget* tokens. Never raises for a content reason or a read
    failure; a tokenizer failure propagates as `TokenizerUnavailable`.

    The closure's repository is resolved as `gw wiki claims closure` resolves
    it (`commands._item_repo`): the nearest `repo:` over the item's ancestor
    chain, else the sole declared repository. Its warning, if any, precedes
    the closure's own, and survives a graph failure. Ledger overlap uses the
    same sole-declared-repository fallback. Streams 1 and 2 share the one
    closure, so the graph is opened once.
    """
    warnings: list[str] = []
    candidates: list[Candidate] = []
    repo_warning: str | None = None
    try:
        repo, repo_warning = _item_repo(layout, item, item_index(tuple(items)))
        if repo_warning is not None:
            warnings.append(repo_warning)
        closure: Closure | None = open_closure(layout, repo=repo, affects=item.affects)
    except (OSError, sqlite3.Error, WorkspaceError) as exc:
        warnings.append(f"code graph unreadable: {exc}; claims and agent sections skipped")
        closure = None
    if closure is not None:
        # An ambiguous-repository warning already says why the closure is empty.
        warnings.extend(w for w in closure.warnings if repo_warning is None or w != WARN_NO_REPO)
        if closure.entries:  # an empty closure matches nothing: do not touch the claims cache
            candidates += _claims_stream(layout, bundle, closure, phase, warnings)
            candidates += _sections_stream(layout, bundle, closure, phase, warnings)
    try:
        declared = declared_repositories(layout)
    except (OSError, WorkspaceError):
        declared = {}  # the closure path above already warned about the manifest
    fallback_repo = next(iter(declared)) if len(declared) == 1 else None
    try:
        ledger, ledger_warnings = ledger_candidates(bundle.root, items, item, fallback_repo=fallback_repo)
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(f"decision ledgers unreadable: {exc}; ledger decisions skipped")
    else:
        candidates += ledger
        warnings.extend(ledger_warnings)
    return build_guidance(candidates, phase=phase, item_path=item.path, warnings=warnings, budget=budget)
