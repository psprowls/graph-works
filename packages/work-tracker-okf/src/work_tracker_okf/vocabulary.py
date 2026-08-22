"""The closed sets this lane's declarations encode, and the `sources[]` id rule.

Ported from `work_io.lifecycle_lint`'s module-level frozensets with W-G, W-H
and W-K applied: `security` and `perf` became `Bug` tags, so `BUG_LIKE_TYPES`
and `DIAGNOSIS_TYPES` shrink; `PARENT_TYPES` absorbs the old
`FEATURE_LIKE_KINDS`, which becomes indistinguishable from it once rule 16 and
`target` are deleted.

**These sets exist twice** -- here as `frozenset`s and in
`assets/schema/_base.schema.json` as `enum` arrays. Generating one from the
other means either a build step or import-time file I/O, both to avoid a
problem `tests/test_vocabulary.py` solves by asserting the two agree. That is
the move `okf_io`'s `test_catalog.py` already makes for the rule catalog.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

from okf_ext.tags import TagDefinition

#: The six concept types this lane declares. They differ in how child 3 routes
#: them, not in frontmatter shape -- which is why the six schema wrappers carry
#: nothing but a `const`.
TYPES: frozenset[str] = frozenset({"Epic", "Feature", "Bug", "TechDebt", "TestGap", "Spike"})

#: W-C's seven. The work-lifecycle axis, distinct from OKF's own `status`.
WORKFLOW_STATUSES: frozenset[str] = frozenset(
    {"open", "accepted", "in-progress", "mitigated", "resolved", "wontfix", "superseded"}
)

#: The pipeline's five stages. `phase` is not required on a page: items
#: predating the pipeline have none, and the router's entry branch is defined
#: for `phase: None`.
PHASES: frozenset[str] = frozenset({"design", "plan", "execute", "finish", "done"})

EFFORTS: frozenset[str] = frozenset({"xtra-small", "small", "medium", "large", "xtra-large"})

#: W-D's axis: OKF's own document status, not the work one.
DOCUMENT_STATUSES: frozenset[str] = frozenset({"draft", "stable", "deprecated"})

#: The filing scope carried by a work item when it is created.
BLAST_RADII: frozenset[str] = frozenset({"file", "package", "domain", "system"})

#: The three `workflow_status` values that end a pipeline. Archive eligibility
#: (child 4) is membership in this set and nothing else.
TERMINAL_STATUSES: frozenset[str] = frozenset({"resolved", "wontfix", "superseded"})

#: Shrunk by W-K: the old `BUG_LIKE_KINDS` was `{bug, security, perf, tech-debt,
#: test-gap}`, and the first three collapsed into `Bug` with tags.
BUG_LIKE_TYPES: frozenset[str] = frozenset({"Bug", "TechDebt", "TestGap"})

#: Shrunk the same way: `DIAGNOSIS_KINDS` was `{bug, security, perf}`.
DIAGNOSIS_TYPES: frozenset[str] = frozenset({"Bug"})

#: An item this small skips the planning stage.
SMALL_EFFORTS: frozenset[str] = frozenset({"xtra-small", "small"})

#: The types that may hold children. Absorbs the old `FEATURE_LIKE_KINDS`,
#: which survey §4.3 showed becomes indistinguishable from this once rule 16
#: and `target` are deleted.
PARENT_TYPES: frozenset[str] = frozenset({"Epic", "Feature"})

#: The tags this package contributes to a shared vault's `tags.yaml`.
#:
#: W-K's two. `security` and `perf` stopped being *kinds* and became `Bug`
#: *tags*, and until now nothing in the shipped code declared either string
#: anywhere -- so a human editing a Bug had no way to discover them. Merged in
#: at install time by `init.install_bundle`, which is what puts them in front
#: of `vocabulary_rule`'s "Did you mean `security`?" suggestion.
#:
#: A package contributes *entries*; it never ships `tags.yaml` itself. The
#: file is the vault's, and `okf_ext.bundle.plan_install` refuses it as a
#: whole-file member for exactly that reason.
CONTRIBUTED_TAGS: tuple[TagDefinition, ...] = (
    TagDefinition(name="perf", description="A defect whose impact is performance."),
    TagDefinition(name="security", description="A defect with a security impact."),
)

#: The phases that produce an artifact, in pipeline order. A tuple, not a
#: frozenset -- order is the point. `done` is absent because a terminal phase
#: produces nothing new, and work-io's synthetic `open` is absent because it
#: existed only to give the archived page its `00-` ordinal, which W-E retires.
ARTIFACT_PHASES: tuple[str, ...] = ("design", "plan", "execute", "finish")

#: The five kinds a `references/` member can be. `work-io` had four; `transcript`
#: is the fifth because under W-I transcripts are members under `references/`
#: rather than living outside the vault (C2-D).
ARTIFACT_KINDS: frozenset[str] = frozenset({"spec", "plan", "guidance", "results", "transcript"})

#: `type` -> the kebab-case segment its slug carries. Six entries, one per `TYPES`
#: member, with `test_vocabulary.py` pinning the key set — the same reconciliation
#: move the schema enums get. An explicit map rather than a PascalCase-splitting
#: regex: six lines that cannot surprise beat a regex that has to be reasoned about.
SLUG_PREFIXES: Mapping[str, str] = MappingProxyType(
    {
        "Epic": "epic",
        "Feature": "feature",
        "Bug": "bug",
        "TechDebt": "tech-debt",
        "TestGap": "test-gap",
        "Spike": "spike",
    }
)

#: The two load-bearing `sources[]` ids. `has_spec_doc` and `has_plan_doc` are
#: membership tests for these, and child 3 routes on the result.
SPEC_SOURCE_ID = "design-spec"
PLAN_SOURCE_ID = "plan"

#: The `sources[]` id vocabulary as a **pattern family**, not an enumerated set
#: (C1-B). An `enum` would catch a typo at the moment it is written, but it
#: cannot express the per-subagent transcript ids the harvest item needs
#: (`transcript-plan-subagent-1`) without growing unboundedly -- and that item
#: already depends on this lane.
#:
#: The phase alternation is uniform across all three families rather than
#: narrowed per family: one alternation used three times is more legible than
#: three narrower ones, and a `results-design` nobody writes costs nothing. The
#: suffix slot is deliberately unvalidated -- it is the seam the transcript work
#: lands in without reopening this file.
#:
#: This exact string is also the `pattern` on `sources[].id` in
#: `_base.schema.json`; `test_vocabulary.py` asserts the two are identical.
SOURCE_ID_PATTERN = (
    r"^(design-spec|plan|(guidance|results|transcript)-(design|plan|execute|finish)(-[a-z0-9]+(-[a-z0-9]+)*)?)$"
)

_SOURCE_ID_RE = re.compile(SOURCE_ID_PATTERN)


def is_source_id(value: str) -> bool:
    """Whether *value* is a well-formed `sources[].id` for this lane.

    `fullmatch`, not `match`: Python's `$` also matches immediately before a
    trailing newline, so `re.match` would accept `"plan\\n"`. JSON Schema's
    `pattern` has no such hole, and using `fullmatch` here is what lets the two
    keep one identical pattern string instead of diverging by an anchor.
    """
    return _SOURCE_ID_RE.fullmatch(value) is not None


__all__ = [
    "ARTIFACT_KINDS",
    "ARTIFACT_PHASES",
    "BLAST_RADII",
    "BUG_LIKE_TYPES",
    "CONTRIBUTED_TAGS",
    "DIAGNOSIS_TYPES",
    "DOCUMENT_STATUSES",
    "EFFORTS",
    "PARENT_TYPES",
    "PHASES",
    "PLAN_SOURCE_ID",
    "SLUG_PREFIXES",
    "SMALL_EFFORTS",
    "SOURCE_ID_PATTERN",
    "SPEC_SOURCE_ID",
    "TERMINAL_STATUSES",
    "TYPES",
    "WORKFLOW_STATUSES",
    "is_source_id",
]
