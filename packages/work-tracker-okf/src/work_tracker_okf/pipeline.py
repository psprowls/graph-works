"""The pipeline definition: one closed stage table plus data-shaped path rules.

Pure: no IO, no clock, no YAML. `workflow.route()` interprets it and every
other consumer in this package derives its phase facts from the accessors
here (epic D-002, D-004). The stage table is code and closed; which stages an
item walks, and the artifact each stage leaves, are data (`PathRule`,
`ArtifactSpec`) with packaged defaults in `PACKAGED_DEFINITION`.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from work_tracker_okf.vocabulary import (
    BLAST_RADII,
    EFFORTS,
    PARENT_TYPES,
    PLAN_SOURCE_ID,
    SPEC_SOURCE_ID,
    TYPES,
    is_source_id,
)

Stage = Literal["design", "plan", "execute", "finish"]
STAGES: tuple[Stage, ...] = ("design", "plan", "execute", "finish")
#: Implicit and terminal: never a row, never on a path, always last.
DONE = "done"
#: Single-stage questions use names; property questions read the stage row.
DESIGN: Final[Stage] = STAGES[0]
PLAN: Final[Stage] = STAGES[1]
EXECUTE: Final[Stage] = STAGES[2]
FINISH: Final[Stage] = STAGES[3]
#: A Literal cannot be derived; tests bond it to expected_phases().
ExpectedPhase = Literal["none", "design", "plan", "execute", "finish", "done"]
#: Types that wait on children at `execute` instead of dispatching, and
#: resolve without `resolved_in` at `finish` when they own no branch (D-006).
DECOMPOSING_TYPES: frozenset[str] = frozenset({"Epic", "Release"})


@dataclass(frozen=True, slots=True)
class StageEffect:
    """The side effects a row declares; the interpreter adds `phase`."""

    work_status: str | None = None
    document_status: str | None = None
    requires: tuple[str, ...] = ()
    sync_plan_table: bool = False
    stamp_source: str | None = None
    stamp_baseline: bool = False


@dataclass(frozen=True, slots=True)
class StageRow:
    stage: Stage
    on_enter: StageEffect | None
    on_complete: StageEffect
    read_only: bool
    results: bool
    ledger_required: bool
    child_gated_types: frozenset[str]
    return_target: Stage | None = None
    return_status: str | None = None


STAGE_TABLE: tuple[StageRow, ...] = (
    StageRow(
        stage="design",
        on_enter=None,
        on_complete=StageEffect(document_status="stable", stamp_source=SPEC_SOURCE_ID, stamp_baseline=True),
        read_only=True,
        results=False,
        ledger_required=False,
        child_gated_types=frozenset(),
    ),
    StageRow(
        stage="plan",
        on_enter=None,
        on_complete=StageEffect(work_status="accepted", sync_plan_table=True, stamp_source=PLAN_SOURCE_ID),
        read_only=True,
        results=False,
        ledger_required=True,
        child_gated_types=frozenset(),
    ),
    StageRow(
        stage="execute",
        on_enter=StageEffect(work_status="in-progress", requires=("owner",)),
        on_complete=StageEffect(),
        read_only=False,
        results=True,
        ledger_required=True,
        child_gated_types=PARENT_TYPES,
    ),
    StageRow(
        stage="finish",
        on_enter=None,
        on_complete=StageEffect(work_status="resolved", requires=("resolved_in",)),
        read_only=False,
        results=True,
        ledger_required=True,
        child_gated_types=PARENT_TYPES,
        return_target="execute",
        return_status="in-progress",
    ),
)
_ROWS: Mapping[str, StageRow] = MappingProxyType({r.stage: r for r in STAGE_TABLE})


def row(stage: str) -> StageRow:
    """The row for *stage*. Raises `KeyError` for `done` or an unknown name."""
    return _ROWS[stage]


def phase_order() -> tuple[str, ...]:
    return (*(r.stage for r in STAGE_TABLE), DONE)


def phase_ordinals() -> Mapping[str, int]:
    return MappingProxyType({phase: rank for rank, phase in enumerate(phase_order())})


def results_phases() -> frozenset[str]:
    return frozenset(r.stage for r in STAGE_TABLE if r.results)


def phase_compat() -> Mapping[str, frozenset[str]]:
    """Which phases each constrained `work_status` admits."""
    working = results_phases()
    return MappingProxyType({"accepted": working | {DONE}, "in-progress": working, "resolved": frozenset({DONE})})


def hold_phases() -> frozenset[str]:
    return frozenset({*phase_order(), "entry"})


def code_phases() -> frozenset[str]:
    return frozenset(r.stage for r in STAGE_TABLE if r.results and not r.read_only)


def read_only_phases() -> frozenset[str]:
    return frozenset(r.stage for r in STAGE_TABLE if r.read_only)


def ledger_phases() -> frozenset[str]:
    return frozenset(r.stage for r in STAGE_TABLE if r.ledger_required) | {DONE}


def dispatch_phases() -> frozenset[str]:
    return frozenset(r.stage for r in STAGE_TABLE)


def child_gated(type_: str, phase: str | None) -> bool:
    """Whether *type_* waits on its children at *phase* (row `child_gated_types`)."""
    found = _ROWS.get(phase) if phase is not None else None
    return found is not None and type_ in found.child_gated_types


def expected_phases() -> tuple[str, ...]:
    """`advance.ExpectedPhase`'s values: `none` plus every phase."""
    return ("none", *phase_order())


AttributeValue = str | bool | None
MatchValue = str | bool | tuple[str | bool, ...]
MatchSpec = Mapping[str, MatchValue]

#: The one attribute vocabulary (D-008). `None` marks a boolean attribute.
ATTRIBUTES: Mapping[str, frozenset[str] | None] = MappingProxyType(
    {
        "stage": frozenset(STAGES),
        "type": TYPES,
        "effort": EFFORTS,
        "blast_radius": BLAST_RADII,
        "has_spec": None,
        "has_plan": None,
        "spec_stale": None,
    }
)
#: Canonical enumeration order for the finite attributes path resolution forks on.
ATTRIBUTE_ORDER: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "effort": ("xtra-small", "small", "medium", "large", "xtra-large"),
        "blast_radius": ("file", "package", "domain", "system"),
    }
)


def matches(spec: MatchSpec, attrs: Mapping[str, AttributeValue]) -> bool:
    """AND across keys; scalar equality; list membership; a missing or `None`
    attribute never matches; `True` never equals the string `"true"`."""
    for attribute, constraint in spec.items():
        actual = attrs.get(attribute)
        if actual is None:
            return False
        choices = constraint if isinstance(constraint, tuple) else (constraint,)
        if not any(type(actual) is type(choice) and actual == choice for choice in choices):
            return False
    return True


#: The exact replacement match for each retired routing variant (design §6.3).
VARIANT_REPLACEMENTS: Mapping[str, str] = MappingProxyType(
    {
        "exploration": "{stage: design} (the general rule; order it before type-specific rules)",
        "diagnosis": "{stage: design, type: Bug}",
        "epic-design": "{stage: design, type: [Epic, Release]}",
        "reconcile": "two rules: {stage: design, has_spec: true} and {stage: plan, spec_stale: true}",
        "single": "{stage: plan}",
        "decompose": "{stage: plan, type: [Epic, Release]}",
        "unplanned": "{stage: execute, has_plan: false}",
        "planned": "{stage: execute, has_plan: true}",
        "branch": "{stage: finish}",
    }
)


def variant_refusal(value: object) -> str:
    """One message naming the replacement for every value a `variant` key lists."""
    values: Sequence[object] = value if isinstance(value, (list, tuple)) else (value,)
    parts = [
        f"{item} -> {VARIANT_REPLACEMENTS[item]}"
        if isinstance(item, str) and item in VARIANT_REPLACEMENTS
        else f"{item!r} -> not a routing variant; remove it"
        for item in values
    ]
    return "match.variant: routing variants are retired; replace with " + "; ".join(parts)


class PipelineConfigError(ValueError):
    """A path rule or artifact declaration that cannot be used, naming source, rule and field."""


@dataclass(frozen=True, slots=True)
class PathOrigin:
    source: str
    index: int
    name: str | None


@dataclass(frozen=True, slots=True)
class PathRule:
    name: str | None
    match: MatchSpec
    stages: tuple[Stage, ...]
    origin: PathOrigin


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    stage: Stage
    file: str
    source: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class PipelineDefinition:
    stage_table: tuple[StageRow, ...]
    path_rules: tuple[PathRule, ...]
    artifacts: Mapping[Stage, ArtifactSpec]


@dataclass(frozen=True, slots=True)
class PathCandidate:
    """One resolved stage list, the rule that won it, and the values assumed
    for unset attributes (empty when nothing was unset)."""

    stages: tuple[Stage, ...]
    rule: PathOrigin
    assignment: Mapping[str, str]

    def __hash__(self) -> int:
        return hash((self.stages, self.rule, tuple(sorted(self.assignment.items()))))


@dataclass(frozen=True, slots=True)
class PathResolution:
    candidates: tuple[PathCandidate, ...]
    unset: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """Every possible value of an unset path attribute found a rule."""
        return len(self.candidates) == len(tuple(itertools.product(*(ATTRIBUTE_ORDER[a] for a in self.unset))))

    @property
    def agreed(self) -> PathCandidate | None:
        """The one candidate when every candidate names the same stages."""
        if not self.complete or not self.candidates or len({c.stages for c in self.candidates}) != 1:
            return None
        return self.candidates[0]


_PATH_KEYS = frozenset({"name", "match", "stages"})
_ARTIFACT_KEYS = frozenset({"file", "source", "required"})


def _fail(where: str, message: str) -> PipelineConfigError:
    return PipelineConfigError(f"{where}: {message}")


def _constraint(attribute: str, value: object, *, where: str) -> MatchValue:
    if value is None:
        raise _fail(where, f"match.{attribute}: null constraints are not allowed")
    values = value if isinstance(value, list) else [value]
    if not values:
        raise _fail(where, f"match.{attribute}: list must be non-empty")
    if not all(type(item) is type(values[0]) for item in values):
        raise _fail(where, f"match.{attribute}: list values must be homogeneous")
    allowed = ATTRIBUTES[attribute]
    if allowed is None:
        if not all(type(item) is bool for item in values):
            raise _fail(where, f"match.{attribute}: expects a boolean or homogeneous boolean list")
    elif not all(isinstance(item, str) and item in allowed for item in values):
        raise _fail(where, f"match.{attribute}: value must be one of {sorted(allowed)}")
    copied = tuple(item for item in values if isinstance(item, (str, bool)))
    return copied if isinstance(value, list) else copied[0]


def _path_match(raw: object, *, where: str) -> MatchSpec:
    if not isinstance(raw, Mapping):
        raise _fail(where, "match: required and must be a mapping")
    match: dict[str, MatchValue] = {}
    for attribute, value in raw.items():
        if attribute == "variant":
            raise _fail(where, variant_refusal(value))
        if attribute == "stage":
            raise _fail(where, "match.stage: stage is an output of routing; a path rule may not match on it")
        if not isinstance(attribute, str) or attribute not in ATTRIBUTES:
            raise _fail(
                where, f"match.{attribute}: unknown attribute; expected one of {sorted(set(ATTRIBUTES) - {'stage'})}"
            )
        match[attribute] = _constraint(attribute, value, where=where)
    return MappingProxyType(match)


def _stages(raw: object, *, where: str) -> tuple[Stage, ...]:
    if not isinstance(raw, list) or not raw:
        raise _fail(where, "stages: must be a non-empty list")
    stages: list[Stage] = []
    for value in raw:
        found = next((stage for stage in STAGES if stage == value), None)
        if found is None:
            raise _fail(where, f"stages: unknown stage {value!r}")
        stages.append(found)
    ranks = [STAGES.index(stage) for stage in stages]
    if ranks != sorted(set(ranks)):
        raise _fail(where, "stages: must follow design, plan, execute, finish in order, each at most once")
    if stages[-1] != "finish":
        raise _fail(where, "stages: must end in finish")
    return tuple(stages)


def parse_path_rules(raw: object, *, source: str) -> tuple[PathRule, ...]:
    """Validate and copy one source's ordered path rules. Plain data in; no YAML."""
    if not isinstance(raw, list):
        raise PipelineConfigError(f"{source}: pipeline.path must be a list")
    rules: list[PathRule] = []
    for index, candidate in enumerate(raw):
        where = f"{source}: path rule {index}"
        if not isinstance(candidate, Mapping):
            raise _fail(where, "rule must be a mapping")
        name = candidate.get("name")
        if "name" in candidate and (not isinstance(name, str) or not name.strip()):
            raise _fail(where, "name: must be a non-empty string when supplied")
        if isinstance(name, str):
            where += f" ({name})"
        unknown = sorted(str(key) for key in candidate if key not in _PATH_KEYS)
        if unknown:
            raise _fail(where, f"unknown keys: {unknown}")
        match = _path_match(candidate.get("match"), where=where)
        stages = _stages(candidate.get("stages"), where=where)
        rule_name = name if isinstance(name, str) else None
        rules.append(PathRule(rule_name, match, stages, PathOrigin(source, index, rule_name)))
    return tuple(rules)


def parse_artifacts(raw: object, *, source: str) -> Mapping[Stage, ArtifactSpec]:
    """Validate and copy one source's stage -> artifact declarations."""
    if not isinstance(raw, Mapping):
        raise PipelineConfigError(f"{source}: pipeline.artifacts must be a mapping")
    specs: dict[Stage, ArtifactSpec] = {}
    for key, value in raw.items():
        where = f"{source}: artifacts.{key}"
        stage = next((s for s in STAGES if s == key), None)
        if stage is None:
            raise PipelineConfigError(f"{where}: unknown stage; expected one of {list(STAGES)}")
        if not isinstance(value, Mapping):
            raise PipelineConfigError(f"{where}: must be a mapping")
        unknown = sorted(str(k) for k in value if k not in _ARTIFACT_KEYS)
        if unknown:
            raise PipelineConfigError(f"{where}: unknown keys: {unknown}")
        file = value.get("file")
        if not isinstance(file, str) or not file.strip() or "/" in file or "\\" in file:
            raise PipelineConfigError(f"{where}: file: must be a non-empty file name with no directory part")
        source_id = value.get("source")
        if not isinstance(source_id, str) or not is_source_id(source_id):
            raise PipelineConfigError(f"{where}: source: must be a kebab-case source id")
        required = value.get("required", True)
        if type(required) is not bool:
            raise PipelineConfigError(f"{where}: required: must be a boolean")
        specs[stage] = ArtifactSpec(stage, file, source_id, required)
    return MappingProxyType(specs)


def path_attributes(
    *,
    type_: str,
    effort: str | None,
    blast_radius: str | None,
    has_spec: bool,
    has_plan: bool,
    spec_stale: bool,
) -> Mapping[str, AttributeValue]:
    """The attributes a path rule may match: every dispatch attribute but `stage`."""
    return MappingProxyType(
        {
            "type": type_,
            "effort": effort,
            "blast_radius": blast_radius,
            "has_spec": has_spec,
            "has_plan": has_plan,
            "spec_stale": spec_stale,
        }
    )


def resolve_path(definition: PipelineDefinition, attrs: Mapping[str, AttributeValue]) -> PathResolution:
    """Resolve under every value of each unset finite attribute any rule references (D-005).

    Later matching rule wins and its `stages` replace outright. One candidate
    per assignment, in `ATTRIBUTE_ORDER`; a single candidate with an empty
    assignment when nothing referenced is unset. No candidate at all means no
    rule matched (a custom definition without a catch-all).
    """
    referenced = {attribute for rule in definition.path_rules for attribute in rule.match}
    unset = tuple(a for a in ATTRIBUTE_ORDER if a in referenced and attrs.get(a) is None)
    candidates: list[PathCandidate] = []
    for values in itertools.product(*(ATTRIBUTE_ORDER[a] for a in unset)):
        assignment = dict(zip(unset, values, strict=True))
        full = {**attrs, **assignment}
        winner = next((rule for rule in reversed(definition.path_rules) if matches(rule.match, full)), None)
        if winner is not None:
            candidates.append(PathCandidate(winner.stages, winner.origin, MappingProxyType(assignment)))
    return PathResolution(tuple(candidates), unset)


def entry_stage(definition: PipelineDefinition, attrs: Mapping[str, AttributeValue]) -> Stage | None:
    """The stage a never-entered item opens, or `None` when candidates disagree or none match."""
    resolution = resolve_path(definition, attrs)
    if not resolution.complete:
        return None
    firsts = {candidate.stages[0] for candidate in resolution.candidates}
    return next(iter(firsts)) if len(firsts) == 1 else None


_PACKAGED_PATH: list[dict[str, object]] = [
    {"name": "default", "match": {}, "stages": ["design", "plan", "execute", "finish"]},
    {
        "name": "small-bug-like-skips-plan",
        "match": {"type": ["Bug", "TechDebt", "TestGap"], "effort": ["xtra-small", "small"]},
        "stages": ["design", "execute", "finish"],
    },
    {"name": "testgap-enters-at-plan", "match": {"type": "TestGap"}, "stages": ["plan", "execute", "finish"]},
    {
        "name": "small-testgap-enters-at-execute",
        "match": {"type": "TestGap", "effort": ["xtra-small", "small"]},
        "stages": ["execute", "finish"],
    },
]
_PACKAGED_ARTIFACTS: dict[str, object] = {
    "design": {"file": "01-design.md", "source": SPEC_SOURCE_ID},
    "plan": {"file": "02-plan.md", "source": PLAN_SOURCE_ID},
    "execute": {"file": "03-execute-coverage.md", "source": "execute-coverage", "required": False},
}

#: What every caller receives until child 2 loads `pipeline.path` / `pipeline.artifacts`.
PACKAGED_DEFINITION = PipelineDefinition(
    stage_table=STAGE_TABLE,
    path_rules=parse_path_rules(_PACKAGED_PATH, source="packaged"),
    artifacts=parse_artifacts(_PACKAGED_ARTIFACTS, source="packaged"),
)


__all__ = [
    "ATTRIBUTES",
    "ATTRIBUTE_ORDER",
    "DECOMPOSING_TYPES",
    "DESIGN",
    "DONE",
    "EXECUTE",
    "FINISH",
    "PACKAGED_DEFINITION",
    "PLAN",
    "STAGES",
    "STAGE_TABLE",
    "VARIANT_REPLACEMENTS",
    "ArtifactSpec",
    "AttributeValue",
    "ExpectedPhase",
    "MatchSpec",
    "MatchValue",
    "PathCandidate",
    "PathOrigin",
    "PathResolution",
    "PathRule",
    "PipelineConfigError",
    "PipelineDefinition",
    "Stage",
    "StageEffect",
    "StageRow",
    "child_gated",
    "code_phases",
    "dispatch_phases",
    "entry_stage",
    "expected_phases",
    "hold_phases",
    "ledger_phases",
    "matches",
    "parse_artifacts",
    "parse_path_rules",
    "path_attributes",
    "phase_compat",
    "phase_order",
    "phase_ordinals",
    "read_only_phases",
    "resolve_path",
    "results_phases",
    "row",
    "variant_refusal",
]
