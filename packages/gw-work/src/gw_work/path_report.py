"""The resolved pipeline path and stage artifacts `gw work next` reports (design §3.4, D-015).

Pure over a routed item: no IO except the `is_file` check that fills
`StageArtifactReport.exists`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from work_tracker_okf.compose import stage_artifact_ref
from work_tracker_okf.pipeline import PathOrigin, PipelineDefinition, entry_stage, resolve_path
from work_tracker_okf.workflow import RouteResult, RouteState, route_attributes

#: Route blocker kinds for which no path is reported: nothing will run.
_NO_PATH_KINDS = frozenset({"invalid", "done", "terminal"})


@dataclass(frozen=True, slots=True)
class PathRuleRef:
    name: str | None
    source: str
    index: int


@dataclass(frozen=True, slots=True)
class PathCandidateReport:
    stages: tuple[str, ...]
    rule: PathRuleRef


@dataclass(frozen=True, slots=True)
class PathReport:
    """`stages` / `rule` are `None` and `candidates` non-empty when resolution
    is blocked or forks; `next` is `None` at the end, off the path, or on a fork."""

    stages: tuple[str, ...] | None
    current: str | None
    next: str | None
    rule: PathRuleRef | None
    candidates: tuple[PathCandidateReport, ...]


@dataclass(frozen=True, slots=True)
class StageArtifactReport:
    stage: str
    file: str
    source: str
    required: bool
    on_path: bool
    path: Path
    exists: bool


def _rule(origin: PathOrigin) -> PathRuleRef:
    return PathRuleRef(origin.name, origin.source, origin.index)


def path_report(state: RouteState, computed: RouteResult, definition: PipelineDefinition) -> PathReport | None:
    """*state*'s resolved path under *definition*, or `None` for an item nothing will run."""
    if computed.blockers and computed.blockers[0].kind in _NO_PATH_KINDS:
        return None
    attrs = route_attributes(state)
    resolution = resolve_path(definition=definition, attrs=attrs)
    agreed = resolution.agreed
    current = state.phase or entry_stage(definition=definition, attrs=attrs)
    following: str | None = None
    if agreed is not None and current is not None and current in agreed.stages:
        index = agreed.stages.index(current)
        following = agreed.stages[index + 1] if index + 1 < len(agreed.stages) else None
    return PathReport(
        stages=None if agreed is None else tuple(agreed.stages),
        current=current,
        next=following,
        rule=None if agreed is None else _rule(agreed.rule),
        candidates=()
        if agreed is not None
        else tuple(PathCandidateReport(tuple(c.stages), _rule(c.rule)) for c in resolution.candidates),
    )


def artifact_reports(
    bundle_root: Path, item_path: str, definition: PipelineDefinition, report: PathReport | None
) -> tuple[StageArtifactReport, ...]:
    """Every configured stage artifact, on the path or not; `()` when *report* is `None`."""
    if report is None:
        return ()
    paths = [report.stages] if report.stages is not None else [c.stages for c in report.candidates]
    reports: list[StageArtifactReport] = []
    for stage, artifact in definition.artifacts.items():
        target = stage_artifact_ref(item_path, artifact).path(bundle_root)
        reports.append(
            StageArtifactReport(
                stage=stage,
                file=artifact.file,
                source=artifact.source,
                required=artifact.required,
                on_path=bool(paths) and all(stage in stages for stages in paths),
                path=target,
                exists=target.is_file(),
            )
        )
    return tuple(reports)


__all__ = [
    "PathCandidateReport",
    "PathReport",
    "PathRuleRef",
    "StageArtifactReport",
    "artifact_reports",
    "path_report",
]
