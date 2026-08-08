"""The rule that turns a `SyncSnapshot` into `Finding`s over a bundle.

    validate(bundle, today=..., extra_rules=[sync_rule(snapshot)])

Three codes, one per `SyncSnapshot` field. Nothing here recomputes
staleness -- `snapshot_bundle` already answered "is this resource
stale/missing/orphaned"; this module only turns bundle membership into
`Finding`s.

Distinguished from `lifecycle.stale`, which this has nothing to do with:
that is a human-authored `stale_after:` date field; `sync.stale-page` is
commit-derived. A reader grepping "stale" will find both -- this is the one
place that needs saying explicitly.
"""

from __future__ import annotations

from collections.abc import Iterable

from okf_io import Finding, Rule, RuleContext, Severity

from code_wiki_okf.resources import resource_index
from code_wiki_okf.sync.snapshot import SyncSnapshot

#: The topic prefix this rule set claims. Collides with none of the eight
#: built-in prefixes (computation, frontmatter, legacy, lifecycle, links,
#: provenance, reserved, trust) -- confirmed against okf_io's own
#: `_rules/__init__.py` mapping.
TOPIC = "sync"

CODES = (
    "sync.stale-page",  # page's last_updated_commit predates a source change a fresh plan would apply
    "sync.missing-page",  # tracked source (file or graph entity) has no page yet
    "sync.orphan-page",  # page's source vanished; sync would delete or decline to delete it
)

_CODE_STALE, _CODE_MISSING, _CODE_ORPHAN = CODES

#: There is no OKF v0.2 section for commit-derived staleness -- it is this
#: package's own concept, not the spec's, so `Finding.spec` says so rather
#: than reaching for a citation that doesn't exist.
_SPEC_CITATION = "code-wiki-okf epic §Drift, staleness, deletion"


def sync_rule(snapshot: SyncSnapshot, *, severity: Severity = "warn") -> Rule:
    """Build an `okf_io.Rule` that reports *snapshot* as `Finding`s.

    `severity` defaults to `"warn"`: staleness is advisory, matching every
    other `okf_ext` factory's own `severity=` default. `validate`'s
    `--strict` flag is the escalation path, not a different default here.

    `.stale` and `.orphaned` are anchored to the page carrying the matching
    `resource:` value via `resource_index`; a resource with no such page is
    silently skipped rather than guessed at, since there is no page to
    anchor a `path` to. `.missing` has no page by definition, so its
    `Finding.path` is always `None`.
    """

    def rule(context: RuleContext) -> Iterable[Finding]:
        index = resource_index(context.bundle)

        for resource in sorted(snapshot.stale):
            entry = index.get(resource)
            if entry is None:
                continue
            yield Finding(
                code=_CODE_STALE,
                severity=severity,
                message=f"`{resource}` changed since this page's `last_updated_commit`; a sync run would update it.",
                spec=_SPEC_CITATION,
                path=f"{entry.concept_id}.md",
            )

        for resource in sorted(snapshot.missing):
            yield Finding(
                code=_CODE_MISSING,
                severity=severity,
                message=f"`{resource}` is tracked but has no page yet; a sync run would create one.",
                spec=_SPEC_CITATION,
                path=None,
            )

        for resource in sorted(snapshot.orphaned):
            entry = index.get(resource)
            if entry is None:
                continue
            yield Finding(
                code=_CODE_ORPHAN,
                severity=severity,
                message=(
                    f"`{resource}` no longer has a tracked source; a sync run would delete or decline to "
                    "delete this page."
                ),
                spec=_SPEC_CITATION,
                path=f"{entry.concept_id}.md",
            )

    return rule


__all__ = ["CODES", "TOPIC", "sync_rule"]
