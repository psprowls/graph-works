"""Variant to skill, mode and prompt tail -- the mapping `work-tracker-okf`
refuses by name.

`workflow.route()` returns a `Dispatch(stage, variant)` and names no skill;
that package's README declines the mapping explicitly. This module is where it
lands, structurally a sibling of `roles.py`: a packaged default, a manifest
override layer, and no discovery call -- `layout` is an argument, `None` means
packaged-only.

Keyed on **`variant` alone**. The eight variants partition cleanly across the
four stages (`exploration`/`diagnosis`/`reconcile` are design-only,
`decompose`/`single` plan-only, `planned`/`unplanned` execute-only, `branch`
finish-only), so variant is already a total key -- and
`config_io.expand_wildcards` supports exactly one `*` segment, so a
`workflow.pipeline.*.*` shape would not resolve.

**Mode is declared per variant, not derived from stage.** The source derived it
from phase and kept a per-skill override table beside it, recording why the
redundancy was worth it: a mode decides whether a worker expects a human in the
room, and the safe direction to fail in is "assume a human is needed". One
table per variant is that property without the two-map indirection.

The packaged table is **total over the closed `Variant` set**, which is what
makes an override able to replace an entry but never leave a hole; `mode`
carries `allowed=DISPATCH_MODES` in the catalog, so config-io refuses a bad
value at set time rather than at dispatch time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from config_io import PlainYamlStore, expand_wildcards, resolve_key

from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import CATALOG as MANIFEST_CATALOG
from graph_works_core.workspace.manifest import checked

#: The catalog prefix this module owns, and the one `roles.py` filters against.
PIPELINE_PREFIX = "workflow.pipeline."

#: The vendor-neutral tail an `attend` worker carries. It is a packaged default
#: rather than config because it names no vendor: it tells a human-in-the-room
#: worker to ask its questions the ordinary way. The `relay` tail is the
#: opposite case -- it is a value the workspace owns -- so it stays `None` in
#: the packaged table and is seeded into a new workspace's manifest instead
#: (`RELAY_TAIL_SEED`, below).
ATTEND_TAIL = "The user may join this session to answer this stage's questions; ask normally."

#: The obligation both execute variants carry. A packaged default rather than
#: config for `ATTEND_TAIL`'s reason -- it names no vendor. It is *reported,
#: not enforced*: the file is surfaced to a human by `auto-drive` §4.1 and
#: registered into `sources[]` by the advance, and no transition is gated on
#: its contents. A gate that read a worker's self-report would be trusting the
#: exact judgement the defect this tail exists for shows a model getting wrong.
EXECUTE_TAIL = (
    "Before you advance, write {workspace}/okf/{path}/references/03-execute-coverage.md: "
    "one markdown task-list line per item in this stage's design spec `## Acceptance` section, "
    "each line `- [x]` when delivered or `- [ ]` when not, each with a one-line justification. "
    "Where the spec has no `## Acceptance` section, enumerate its `## Scope` / "
    "`## What this design changes` headings instead and say in the file that you did. "
    "Mark honestly -- an unchecked box is a normal, expected outcome; an inaccurate checked box "
    "is not. Pass that file's path as --report-path on your worker_done."
)

#: The `relay` tail a **new** workspace is seeded with, written into its
#: manifest by `init.plan_init` rather than shipped in `PACKAGED_PIPELINE`.
#: The tail is a value the workspace owns; core seeds it and a workspace
#: replaces it with its own vendor wording through `gw config set`.
#:
#: Both halves are load-bearing and neither may be dropped: the relay skill
#: triggers on the literal `Auto-drive context:` prefix, and reads the merge
#: target verbatim out of the same line. A tail carrying one without the other
#: arms half the seam.
RELAY_TAIL_SEED = (
    "Auto-drive context: relay the merge/PR/hold/discard decision to your coordinator "
    "rather than asking interactively; merge target is {merge_target}."
)


@dataclass(frozen=True, slots=True)
class PipelineEntry:
    """What one variant dispatches to."""

    skill: str
    mode: str
    prompt_tail: str | None = None


#: The packaged table. Read by `pipeline_table`, never mutated.
PACKAGED_PIPELINE: Mapping[str, PipelineEntry] = MappingProxyType(
    {
        "exploration": PipelineEntry("brainstorming", "attend", ATTEND_TAIL),
        "diagnosis": PipelineEntry("systematic-debugging", "attend", ATTEND_TAIL),
        "reconcile": PipelineEntry("reconciling-spec", "autonomous"),
        "decompose": PipelineEntry("planning-epics", "autonomous"),
        "single": PipelineEntry("writing-plans", "autonomous"),
        "planned": PipelineEntry("subagent-driven-development", "autonomous", EXECUTE_TAIL),
        "unplanned": PipelineEntry("test-driven-development", "autonomous", EXECUTE_TAIL),
        "branch": PipelineEntry("finishing-a-development-branch", "relay"),
    }
)


def workspace_pipeline(layout: WorkspaceLayout) -> dict[str, dict[str, Any]]:
    """The `workflow.pipeline.<variant>.<field>` overrides in this manifest.

    Filtered by prefix -- the catalog carries a second wildcard family
    (`roles.*`) that `roles.py` owns.

    Every value is `checked` against its catalog entry before it is layered: a
    hand-edited manifest bypasses config-io's set-time checks, and an unchecked
    `mode` surfaces as `UnsupportedMode` from the backend rather than as a
    sentence naming this file.

    **An explicit `null` is dropped, not refused** -- the opposite of
    `workflow.auto_drive.max_parallel` / `permission_mode`, deliberately. Every
    default in this family is `None` so that an unset field stays absent rather
    than shadowing the packaged value `pipeline_table` layers under it; a null
    is therefore genuinely equivalent to unset here. The auto-drive scalars
    have real defaults, so a null there lets a workspace inherit one while
    believing it set something, and `manifest.checked_int` refuses it.
    """
    store = PlainYamlStore(layout.manifest_path)
    overrides: dict[str, dict[str, Any]] = {}
    for key in expand_wildcards(MANIFEST_CATALOG, store=store):
        if not key.startswith(PIPELINE_PREFIX):
            continue
        variant, field = key[len(PIPELINE_PREFIX) :].split(".", 1)
        value = checked(
            resolve_key(MANIFEST_CATALOG, key, store=store, environ={}),
            source=layout.manifest_path,
        )
        if value is None:
            continue
        overrides.setdefault(variant, {})[field] = value
    return overrides


def pipeline_table(*, layout: WorkspaceLayout | None = None) -> Mapping[str, PipelineEntry]:
    """The packaged table with this workspace's overrides layered on.

    An override for a variant the packaged table does not carry is **dropped**,
    not added: the key set is the closed `Variant` set, and letting a manifest
    typo introduce an eighth entry would move the hole from "unmapped variant"
    to "variant nothing routes to".
    """
    if layout is None:
        return PACKAGED_PIPELINE
    merged = dict(PACKAGED_PIPELINE)
    for variant, fields in workspace_pipeline(layout).items():
        packaged = merged.get(variant)
        if packaged is None:
            continue
        merged[variant] = replace(packaged, **fields)
    return MappingProxyType(merged)


def entry_for(variant: str, *, layout: WorkspaceLayout | None = None) -> PipelineEntry:
    """One variant's entry.

    Raises:
        KeyError: when *variant* is outside the closed set -- a caller bug, not
            content: `route()` only ever yields a `Variant` member.
    """
    return pipeline_table(layout=layout)[variant]


__all__ = [
    "ATTEND_TAIL",
    "EXECUTE_TAIL",
    "PACKAGED_PIPELINE",
    "PIPELINE_PREFIX",
    "RELAY_TAIL_SEED",
    "PipelineEntry",
    "entry_for",
    "pipeline_table",
    "workspace_pipeline",
]
