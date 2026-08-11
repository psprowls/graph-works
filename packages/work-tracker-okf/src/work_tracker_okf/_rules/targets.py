"""Rules for what an item points at outside itself.

Two kinds of pointer, one question: does the thing it names exist. `affects`
names repo paths, which need `repo_root` injected; `sources[].resource` names
bundle members, which do not.

**The epic spec's rule-23 absorption claim does not hold** (C5-G). okf-io's link
graph is built from body prose only: `links.build()` never reads frontmatter, and
`provenance.source-resource-missing` asserts only that the `resource` key is
*present*, never that its value resolves to a member. Nothing in okf-io or
okf-ext validates a `sources[].resource` target, so rule 23 survives here as
`targets.artifact-missing`. That is a gap in tier 2, not a defect here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from okf_io import Finding, Rule, RuleContext

from work_tracker_okf._rules._common import LaneConfig, active
from work_tracker_okf.paths import source_id_for

CODES: tuple[str, ...] = (
    "targets.affects-missing",
    "targets.artifact-missing",
    "targets.source-id-mismatch",
)

#: `affects` is the lane's own key and cites the module that defines it.
_SPEC = "work_tracker_okf._rules.targets"

#: The two `sources[]` codes cite a real section: `sources[]` is an OKF
#: construct and §5.1 is what makes `resource` mean something (C5-H).
_SPEC_SOURCES = "§5.1"

#: `NN-<phase>-<kind>[-<suffix>].<ext>` -- `paths.artifact_path`'s filename, read
#: back. Deliberately tolerant: anything that does not match is not this rule's
#: finding.
_ARTIFACT_FILENAME_RE = re.compile(r"^\d{2}-([a-z]+)-([a-z]+)(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?\.[a-z0-9]+$")


def _derived_id(resource: str) -> str | None:
    """The `sources[].id` *resource*'s own filename implies, or `None`.

    `paths.source_id_for` **raises** on an unknown phase or kind and on an
    invalid pair (`spec` outside `design`) -- it is that module's one raising
    door, for caller error. So the filename is parsed defensively here and an
    unrecognizable artifact is silently not this rule's finding; the schema
    `pattern` on `sources[].id` already guards the id's shape.
    """
    match = _ARTIFACT_FILENAME_RE.fullmatch(PurePosixPath(resource).name)
    if match is None:
        return None
    try:
        return source_id_for(match.group(1), match.group(2))
    except ValueError:
        return None


def artifacts(ctx: RuleContext) -> Iterable[Finding]:
    """23 (renamed) and one new code, both about `sources[]`.

    `artifact-missing` is `warn`, matching `links.broken`'s severity for the
    reason ADR-0004 gives: a pointer to knowledge not yet written is not a
    conformance failure.

    A source whose resource does not resolve is **not** also checked for id
    agreement. Two findings about one broken pointer says the same thing twice,
    and the resource is the fault worth acting on first.

    `source-id-mismatch` allows a suffix, because `SOURCE_ID_PATTERN` admits
    `transcript-plan-subagent-1` and the harvest item needs it. The comparison is
    therefore prefix-with-hyphen, not equality. The two literal ids
    (`design-spec`, `plan`) cannot carry a suffix, and that falls out of the
    pattern without a second check here.
    """
    for item in active(ctx):
        for index, source in enumerate(item.sources):
            resource = source.resource
            if not resource:
                continue  # `provenance.source-resource-missing` owns this
            if not ctx.bundle.has_member(resource.removeprefix("/")):
                yield Finding(
                    code="targets.artifact-missing",
                    severity="warn",
                    message=f"`sources[{index}].resource` {resource!r} is not a bundle member",
                    spec=_SPEC_SOURCES,
                    path=item.path,
                    line=None,
                )
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
                path=item.path,
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
                    path=item.path,
                    line=None,
                )

    return rule


def rules(config: LaneConfig) -> tuple[Rule, ...]:
    """`repo_root=None` **skips** `targets.affects-missing`, as it skips
    `plan.action-target-missing`, for the same reason."""
    if config.repo_root is None:
        return (artifacts,)
    return (artifacts, _affects(config.repo_root))
