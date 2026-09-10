"""The four operations the `gw wiki tags` surface routes to.

Pure over a loaded bundle: the caller resolves the workspace and calls
`okf_io.load_bundle` itself, matching `graph_works_core.wiki_stats.commands`
and `okf_io.build_link_graph`.

**Merges and strips are two phases, not one plan.** A `RenamePlan` records
*positions*, and a merge that collapses two tags in one document shifts every
later index in it. A strip planned before the merge lands names positions that
have since moved, and `okf_ext.tags.apply` correctly refuses the whole
document as `stale`. So each phase is planned against the bundle as it is at
that moment, and `apply` commits its edits back into the live bundle
(`_commit_tags`), which is what makes the second planning pass see the first
phase's result with no reload.

This is also the shape the design's own sequencing wants: merges are one
commit (wave 4) and strips are another (wave 5), each independently
revertible.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, get_args

from okf_ext.tags import apply, inventory, load_vocabulary, plan_merge, plan_strip
from okf_ext.tags.model import ApplyResult, RenamePlan
from okf_io import Bundle

from graph_works_core.tag_policy.model import Disposition
from graph_works_core.tag_policy.rule import DEFAULT_CEILING, DEFAULT_FLOOR, draft

Phase = Literal["merge", "strip"]
_PHASES = frozenset(get_args(Phase))


def draft_disposition(
    bundle: Bundle,
    *,
    generated: date,
    floor: int = DEFAULT_FLOOR,
    ceiling: float = DEFAULT_CEILING,
) -> Disposition:
    """Inventory *bundle* and judge every tag it carries."""
    return draft(inventory(bundle), bundle, generated=generated, floor=floor, ceiling=ceiling)


def plan_phase(bundle: Bundle, disposition: Disposition, phase: Phase) -> RenamePlan:
    """One phase of *disposition*, as a plan nothing has applied yet.

    The merge phase groups sources by target and unions the resulting plans'
    edits: `plan_merge` takes one target at a time, and a disposition may name
    several. Every source appears in exactly one group (`disposition.load`
    guarantees each tag is declared once), so the groups touch disjoint sets
    of positions and the union can never produce two edits for one index --
    which is the invariant `apply`'s `duplicate-edit` refusal exists to
    protect.
    """
    if phase not in _PHASES:
        raise ValueError(f"unknown phase {phase!r}; expected one of {sorted(_PHASES)!r}")
    if phase == "strip":
        return plan_strip(bundle, disposition.strip_tags)

    grouped: dict[str, list[str]] = {}
    for source, target in sorted(disposition.merge_mapping.items()):
        grouped.setdefault(target, []).append(source)
    edits = [edit for target, sources in sorted(grouped.items()) for edit in plan_merge(bundle, sources, target).edits]
    edits.sort(key=lambda edit: (edit.concept_id, edit.index))
    # Skips are a property of the bundle, not of the mapping, so any one
    # planner's are all of them; an empty mapping still needs them reported.
    skipped = plan_strip(bundle, ()).skipped
    return RenamePlan(root=bundle.root, edits=tuple(edits), skipped=skipped)


def apply_phase(bundle: Bundle, disposition: Disposition, phase: Phase) -> ApplyResult:
    """Plan *phase* against *bundle* as it is now, and write it."""
    return apply(bundle, plan_phase(bundle, disposition, phase))


def undeclared(bundle: Bundle, vocabulary_path: Path) -> tuple[str, ...]:
    """Every tag *bundle* carries that *vocabulary_path* does not know.

    "Known", not "allowed": a deprecated entry is a declaration, and
    `tags.deprecated` is the separate finding that reports it. Conflating the
    two would make `gate` fire on a tag the vocabulary is actively migrating,
    which is the one state a migration must be able to pass through.

    Raises `okf_ext.tags.VocabularyError` (a `ValueError`) for a malformed
    file and propagates `OSError` for a missing one -- the CLI's existing
    `except (OSError, ValueError)` guard catches both.
    """
    vocab = load_vocabulary(vocabulary_path)
    return tuple(sorted(set(inventory(bundle).counts) - vocab.known))


__all__ = ["Phase", "apply_phase", "draft_disposition", "plan_phase", "undeclared"]
