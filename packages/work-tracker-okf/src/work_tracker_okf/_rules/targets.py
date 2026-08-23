"""Rules for repository targets and source-id/filename agreement.

Source confinement and existence are permanent layout concerns owned by the
``structure`` topic. This module retains the independent questions of whether
an ``affects`` path exists and whether a managed artifact filename agrees with
its source id.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from okf_io import Finding, Rule, RuleContext

from work_tracker_okf._rules._common import LaneConfig, active
from work_tracker_okf.paths import source_id_for_filename

CODES: tuple[str, ...] = (
    "targets.affects-missing",
    "targets.source-id-mismatch",
)

#: `affects` is the lane's own key and cites the module that defines it.
_SPEC = "work_tracker_okf._rules.targets"

#: The two `sources[]` codes cite a real section: `sources[]` is an OKF
#: construct and §5.1 is what makes `resource` mean something (C5-H).
_SPEC_SOURCES = "§5.1"


def _derived_id(resource: str) -> str | None:
    """The `sources[].id` *resource*'s own filename implies, or `None`.

    ``source_id_for_filename`` raises when the ordinal form is absent, so an
    unrecognizable artifact is silently not this rule's finding.
    """
    try:
        return source_id_for_filename(PurePosixPath(resource).name)
    except ValueError:
        return None


def artifacts(ctx: RuleContext) -> Iterable[Finding]:
    """Check source-id agreement only after structure resolved the resource.

    `source-id-mismatch` allows a suffix, because `SOURCE_ID_PATTERN` admits
    `transcript-plan-subagent-1` and the harvest item needs it. The comparison is
    therefore prefix-with-hyphen, not equality. The two literal ids
    (`design`, `plan`) cannot carry a suffix, and that falls out of the pattern
    without a second check here.
    """
    for item in active(ctx):
        for index, source in enumerate(item.sources):
            resource = source.resource
            if not resource:
                continue  # `provenance.source-resource-missing` owns this
            if not ctx.bundle.has_member(resource.removeprefix("/")):
                continue
            expected = _derived_id(resource)
            authored = source.id
            if expected is None or authored is None:
                continue
            if authored == expected or authored.startswith(f"{expected}-"):
                continue
            yield Finding(
                code="targets.source-id-mismatch",
                severity="warn",
                message=(f"`sources[{index}].id` {authored!r} disagrees with its filename, which implies {expected!r}"),
                spec=_SPEC_SOURCES,
                path=item.page_path,
                line=None,
            )


def _affects(repo_root: Path) -> Rule:
    """10: an `affects` entry naming a repo path that does not exist."""

    def rule(ctx: RuleContext) -> Iterable[Finding]:
        for item in active(ctx):
            for target in item.affects:
                if not target or (repo_root / target).exists():
                    continue
                yield Finding(
                    code="targets.affects-missing",
                    severity="error",
                    message=f"`affects` entry {target!r} does not exist under the repo root",
                    spec=_SPEC,
                    path=item.page_path,
                    line=None,
                )

    return rule


def rules(config: LaneConfig) -> tuple[Rule, ...]:
    """`repo_root=None` **skips** `targets.affects-missing`, as it skips
    `plan.action-target-missing`, for the same reason."""
    if config.repo_root is None:
        return (artifacts,)
    return (artifacts, _affects(config.repo_root))
