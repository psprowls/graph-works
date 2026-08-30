"""Variant to skill, mode and prompt tail -- the mapping `work-tracker-okf`
refuses by name.

`workflow.route()` returns a `Dispatch(stage, variant)` and names no skill;
that package's README declines the mapping explicitly. This module is where it
lands, structurally a sibling of `roles.py`: a packaged default, a manifest
override layer, and no discovery call -- `layout` is an argument, `None` means
packaged-only.

Keyed on **`variant` alone**. The nine variants partition cleanly across the
four stages (`exploration`/`diagnosis`/`reconcile`/`epic-design` are design-only,
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

**A skill name is used verbatim.** A colon-qualified value means
`<plugin>:<skill>` and the dispatching skill invokes it as written; a bare
name is equally valid and routes to a user-level or repo-local skill. There
is no implicit plugin namespace and nothing prepends one -- which is why
every packaged value here is qualified, and why a test asserts it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from config_io import PlainYamlStore, expand_wildcards, resolve_key

from graph_works_core.workspace.errors import WorkspaceError
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
        "exploration": PipelineEntry("superpowers:brainstorming", "attend", ATTEND_TAIL),
        "diagnosis": PipelineEntry("superpowers:systematic-debugging", "attend", ATTEND_TAIL),
        "reconcile": PipelineEntry("gw:reconciling-spec", "autonomous"),
        "epic-design": PipelineEntry("gw:epic-design", "attend", ATTEND_TAIL),
        "decompose": PipelineEntry("gw:planning-epics", "autonomous"),
        "single": PipelineEntry("superpowers:writing-plans", "autonomous"),
        "planned": PipelineEntry("superpowers:subagent-driven-development", "autonomous"),
        "unplanned": PipelineEntry("superpowers:test-driven-development", "autonomous"),
        "branch": PipelineEntry("superpowers:finishing-a-development-branch", "relay"),
    }
)


def is_valid_skill_name(value: str) -> bool:
    """Whether *value* is a well-formed stage skill name.

    A **bare** name -- no colon -- is valid and is used verbatim: user-level and
    repo-local skills carry no plugin prefix, so requiring qualification would
    make them unroutable. A colon nonetheless signals *qualification intent*, so
    a malformed qualification (`a:`, `:b`, `a:b:c`) is still a refusable shape.

    There is deliberately **no charset rule**. What three different harnesses
    accept in a skill name is not something this repo knows, and inventing a
    pattern would refuse valid names to guard against nothing observed.
    """
    if not value.strip():
        return False
    if ":" not in value:
        return True
    plugin, _, skill = value.partition(":")
    return ":" not in skill and bool(plugin.strip()) and bool(skill.strip())


def check_skill_name(value: object, *, key: str, source: Path) -> None:
    """A well-formed stage skill name, or a refusal naming the key and the file.

    Beside `manifest.checked()` and for the same reason: a hand-edited manifest
    bypasses config-io's set-time checks, so a reader that trusts a stored value
    trusts a file nothing validated. Placing it in `workspace_pipeline()` -- the
    one place a manifest-sourced skill value enters the process -- is what makes
    `pipeline_table(layout=...)`, and therefore auto-drive, guarded too.

    Raises:
        WorkspaceError: for an empty, whitespace-only or malformed-qualification
            name, or a non-string one.
    """
    if not isinstance(value, str) or not is_valid_skill_name(value):
        raise WorkspaceError(
            f"{source}: {key}: expects a skill name — non-empty, optionally "
            f"qualified as <plugin>:<skill> — got {value!r}"
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
        if field == "skill":
            check_skill_name(value, key=key, source=layout.manifest_path)
        overrides.setdefault(variant, {})[field] = value
    return overrides


def pipeline_table(*, layout: WorkspaceLayout | None = None) -> Mapping[str, PipelineEntry]:
    """The packaged table with this workspace's overrides layered on.

    An override for a variant the packaged table does not carry is **dropped**,
    not added: the key set is the closed `Variant` set, and letting a manifest
    typo introduce a tenth entry would move the hole from "unmapped variant"
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
    "PACKAGED_PIPELINE",
    "PIPELINE_PREFIX",
    "RELAY_TAIL_SEED",
    "PipelineEntry",
    "check_skill_name",
    "entry_for",
    "is_valid_skill_name",
    "pipeline_table",
    "workspace_pipeline",
]
