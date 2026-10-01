"""`gw work next` reports the resolved path and every stage artifact (design §3.4, D-015)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work
from graph_works_wire.work import next_payload


def _layout(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=date(2026, 8, 23), topic="Next")).layout


def _write(layout, path, *, type="Feature", phase="design", effort="medium", work_status="open"):
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    fields = {
        "type": type,
        "title": path,
        "description": "d",
        "status": "stable",
        "work_status": work_status,
        "phase": phase,
        "effort": effort,
    }
    page.write_text(
        "---\n"
        + "".join(f"{key}: {value}\n" for key, value in fields.items() if value is not None)
        + "affects: [packages/a]\n---\n",
        encoding="utf-8",
        newline="\n",
    )


def _set_pipeline(layout, **pipeline):
    (layout.root / "dispatch.yaml").write_text(
        json.dumps({"version": 1, "pipeline": pipeline}), encoding="utf-8", newline="\n"
    )


def _payload(layout, path: str) -> dict:
    return next_payload(work.run_next(layout, path), bundle_root=layout.bundle_dir)


def test_resolved_path_golden(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/bug-x", type="Bug", phase="design", effort="small")
    refs = layout.bundle_dir / "work/bug-x/references"
    payload = _payload(layout, "work/bug-x")
    assert payload["path"] == {
        "stages": ["design", "execute", "finish"],
        "current": "design",
        "next": "execute",
        "rule": {"name": "small-bug-like-skips-plan", "source": "packaged", "index": 1},
        "candidates": [],
    }
    assert payload["artifacts"] == {
        "design": {
            "file": "01-design.md",
            "source": "design",
            "required": True,
            "on_path": True,
            "path": str(refs / "01-design.md"),
            "exists": False,
        },
        "plan": {
            "file": "02-plan.md",
            "source": "plan",
            "required": True,
            "on_path": False,
            "path": str(refs / "02-plan.md"),
            "exists": False,
        },
        "execute": {
            "file": "03-execute-coverage.md",
            "source": "execute-coverage",
            "required": False,
            "on_path": True,
            "path": str(refs / "03-execute-coverage.md"),
            "exists": False,
        },
    }
    assert payload["artifact"] == {"path": str(refs / "01-design.md")}


def test_unsized_testgap_is_blocked_with_candidates(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/test-gap-x", type="TestGap", phase=None, effort=None)
    path = _payload(layout, "work/test-gap-x")["path"]
    assert path["stages"] is None and path["next"] is None and path["rule"] is None
    assert {tuple(c["stages"]) for c in path["candidates"]} == {("plan", "execute", "finish"), ("execute", "finish")}
    assert all(set(c["rule"]) == {"name", "source", "index"} for c in path["candidates"])


def test_unsized_bug_at_design_forks_at_next(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/bug-x", type="Bug", phase="design", effort=None)
    path = _payload(layout, "work/bug-x")["path"]
    assert path["stages"] is None and path["current"] == "design" and path["next"] is None
    assert {tuple(c["stages"]) for c in path["candidates"]} == {
        ("design", "plan", "execute", "finish"),
        ("design", "execute", "finish"),
    }


def test_unentered_item_current_is_the_entry_stage(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-x", phase=None)
    path = _payload(layout, "work/feature-x")["path"]
    assert (path["current"], path["next"]) == ("design", "plan")


def test_off_path_phase_has_no_next(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/bug-x", type="Bug", phase="plan", effort="small")
    path = _payload(layout, "work/bug-x")["path"]
    assert path["stages"] == ["design", "execute", "finish"]
    assert (path["current"], path["next"]) == ("plan", None)


@pytest.mark.parametrize("phase, status", [("done", "resolved"), ("done", "open"), ("bogus", "open")])
def test_terminal_item_has_null_path_and_empty_artifacts(tmp_path: Path, phase: str, status: str) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-x", phase=phase, work_status=status)
    payload = _payload(layout, "work/feature-x")
    assert (payload["path"], payload["artifacts"]) == (None, {})


def test_workspace_path_rule_reports_its_source(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _set_pipeline(
        layout,
        path=[{"name": "features-skip-plan", "match": {"type": "Feature"}, "stages": ["design", "execute", "finish"]}],
    )
    _write(layout, "work/feature-x", phase="design")
    payload = _payload(layout, "work/feature-x")
    rule = payload["path"]["rule"]
    assert rule["name"] == "features-skip-plan"
    assert rule["source"] == str(layout.root / "dispatch.yaml")
    assert rule["index"] == 0
    assert payload["artifacts"]["plan"]["on_path"] is False


def test_renamed_artifacts_are_reported(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _set_pipeline(
        layout, artifacts={"design": {"file": "spec.md"}, "execute": {"file": "coverage.md", "required": False}}
    )
    _write(layout, "work/feature-x", phase="design")
    artifacts = _payload(layout, "work/feature-x")["artifacts"]
    assert (artifacts["design"]["file"], artifacts["design"]["source"]) == ("spec.md", "design")
    assert artifacts["execute"]["file"] == "coverage.md"


def test_existing_artifact_is_reported_as_existing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _write(layout, "work/feature-x", phase="plan")
    design = layout.bundle_dir / "work/feature-x/references/01-design.md"
    design.parent.mkdir(parents=True, exist_ok=True)
    design.write_text("# D\n", encoding="utf-8")
    assert _payload(layout, "work/feature-x")["artifacts"]["design"]["exists"] is True


@pytest.mark.parametrize("dry_run", [True, False])
def test_malformed_dispatch_file_reports_no_path_or_artifacts(tmp_path: Path, dry_run: bool) -> None:
    layout = _layout(tmp_path)
    (layout.root / "dispatch.yaml").write_text("pipeline: {path: nope}\n", encoding="utf-8")
    _write(layout, "work/feature-x", phase="design")
    payload = next_payload(work.run_next(layout, "work/feature-x", dry_run=dry_run), bundle_root=layout.bundle_dir)
    assert (payload["path"], payload["artifacts"]) == (None, {})
    assert payload["blockers"]  # child 2's preflight still reports the load error


@pytest.mark.parametrize(
    "kind, phase, effort, stages, current, following",
    [
        ("Feature", None, "medium", ("design", "plan", "execute", "finish"), "design", "plan"),
        ("Bug", "design", "small", ("design", "execute", "finish"), "design", "execute"),
        ("Bug", "plan", "small", ("design", "execute", "finish"), "plan", None),
        ("Feature", "finish", "medium", ("design", "plan", "execute", "finish"), "finish", None),
        ("Bug", "design", None, None, "design", None),
        ("TestGap", None, None, None, None, None),
    ],
)
def test_path_report_unit(kind, phase, effort, stages, current, following, tmp_path):
    from graph_works_core.work.path_report import artifact_reports, path_report
    from work_tracker_okf.pipeline import PACKAGED_DEFINITION
    from work_tracker_okf.workflow import RouteState, route

    state = RouteState(type=kind, work_status="open", phase=phase, effort=effort)
    report = path_report(state, route(state), PACKAGED_DEFINITION)
    assert report is not None
    assert (report.stages, report.current, report.next) == (stages, current, following)
    artifacts = artifact_reports(tmp_path, "work/a", PACKAGED_DEFINITION, report)
    assert len(artifacts) == 3
    if kind == "TestGap":
        assert {c.stages for c in report.candidates} == {("plan", "execute", "finish"), ("execute", "finish")}
        assert [a.stage for a in artifacts if a.on_path] == ["execute"]
    if kind == "Bug" and effort is None:
        assert [a.stage for a in artifacts if a.on_path] == ["design", "execute"]


@pytest.mark.parametrize("dry_run", [True, False])
def test_renamed_artifact_and_path_survive_apply(tmp_path, dry_run):
    layout = _layout(tmp_path)
    _set_pipeline(layout, artifacts={"design": {"file": "spec.md"}})
    _write(layout, "work/feature-x")
    payload = next_payload(work.run_next(layout, "work/feature-x", dry_run=dry_run), bundle_root=layout.bundle_dir)
    assert payload["artifact"] == {"path": str(layout.bundle_dir / "work/feature-x/references/spec.md")}
    assert payload["path"]["current"] == "design"
    assert payload["artifacts"]["design"]["file"] == "spec.md"


@pytest.mark.parametrize("phase, status", [("done", "resolved"), ("done", "open"), ("bogus", "open")])
def test_suppressed_report_unit(phase, status, tmp_path):
    from graph_works_core.work.path_report import artifact_reports, path_report
    from work_tracker_okf.pipeline import PACKAGED_DEFINITION
    from work_tracker_okf.workflow import RouteState, route

    state = RouteState(type="Feature", work_status=status, phase=phase)
    report = path_report(state, route(state), PACKAGED_DEFINITION)
    assert report is None
    assert artifact_reports(tmp_path, "work/a", PACKAGED_DEFINITION, report) == ()


def test_artifact_files_and_empty_candidates_unit(tmp_path):
    from dataclasses import replace

    from graph_works_core.work.path_report import PathReport, artifact_reports
    from work_tracker_okf.pipeline import PACKAGED_DEFINITION, ArtifactSpec

    definition = replace(PACKAGED_DEFINITION, artifacts={"design": ArtifactSpec("design", "spec.md", "design", True)})
    target = tmp_path / "work/a/references/spec.md"
    target.parent.mkdir(parents=True)
    report = PathReport(None, None, None, None, ())
    missing = artifact_reports(tmp_path, "work/a", definition, report)[0]
    assert (missing.file, missing.path, missing.exists, missing.on_path) == ("spec.md", target, False, False)
    target.mkdir()
    assert artifact_reports(tmp_path, "work/a", definition, report)[0].exists is False
    target.rmdir()
    target.write_text("# Spec\n", encoding="utf-8", newline="\n")
    assert artifact_reports(tmp_path, "work/a", definition, report)[0].exists is True
