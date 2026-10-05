"""A workspace that renames stage artifacts is read and stamped under its names (design §3.2, §5)."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date
from pathlib import Path

from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate import stage_advance as stage
from graph_works_core.orchestrate.gate_receipts import GateRun, parse_gate_receipt, render_receipt
from graph_works_core.work import commands as work
from graph_works_core.work.reconcile import run_reconcile_context
from graph_works_core.workspace.anchor import spec_ref
from graph_works_core.workspace.dispatch_artifacts import missing_design_source
from graph_works_core.workspace.dispatch_config import load_dispatch_config
from graph_works_core.workspace.layout import layout_for
from okf_io import load, load_bundle
from ruamel.yaml import YAML
from work_tracker_okf.items import IGNORE, load_items


def _set_pipeline(layout, **blocks: object) -> None:
    yaml = YAML()
    target = layout.root / "dispatch.yaml"
    document = yaml.load(target.read_text(encoding="utf-8")) or {}
    document.setdefault("pipeline", {}).update(blocks)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.dump(document, handle)


TODAY = date(2026, 8, 23)


def _workspace(tmp_path: Path, manifest: str = "version: 1\n"):
    apply_init(plan_init(tmp_path, today=TODAY, topic="Artifacts"))
    if "workflow:" in manifest:
        manifest = manifest.replace("workflow:\n", "workflow:\n  dispatch_rules: dispatch.yaml\n")
    else:
        manifest += "workflow:\n  dispatch_rules: dispatch.yaml\n"
    (tmp_path / "dispatch.yaml").write_text("pipeline:\n  rules: []\n", encoding="utf-8")
    (tmp_path / "workspace.yaml").write_text(manifest, encoding="utf-8")
    layout = layout_for(tmp_path)
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    return layout


def _write(
    layout,
    path: str,
    *,
    type: str = "Feature",
    phase: str | None = "plan",
    work_status: str = "open",
    affects: tuple[str, ...] = ("packages/a",),
    extra: str = "",
) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    rendered = "affects: []" if not affects else "affects:\n" + "".join(f"- {entry}\n" for entry in affects).rstrip()
    phase_line = f"phase: {phase}\n" if phase is not None else ""
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\n"
        f"work_status: {work_status}\n{phase_line}effort: medium\nopened: 2026-08-01\n"
        f"updated: 2026-08-01\n{extra}{rendered}\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )


def _git_repo(path: Path) -> Path:
    """A real repository with one commit on `main`."""
    import subprocess

    (path / "packages/a").mkdir(parents=True)
    (path / "packages/a/x.py").write_text("one\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "T"),
        ("add", "."),
        ("commit", "-m", "first"),
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True)
    return path


RENAMED = {"design": {"file": "spec.md"}, "execute": {"file": "coverage.md", "required": False}}


def test_spec_ref_and_missing_design_source_read_the_renamed_file(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _set_pipeline(layout, artifacts=RENAMED)
    _write(layout, "work/feature-x", phase="design")
    (layout.bundle_dir / "work/feature-x/references").mkdir(parents=True)
    (layout.bundle_dir / "work/feature-x/references/spec.md").write_text("# Spec\n", encoding="utf-8")
    definition = load_dispatch_config(layout).definition
    item = next(i for i in load_items(load_bundle(layout.bundle_dir, ignore=IGNORE)) if i.path == "work/feature-x")
    assert spec_ref(item, definition=definition) == "work/feature-x/references/spec.md"
    missing = missing_design_source(layout.bundle_dir, item, definition=definition)
    assert missing is not None and missing[0].rel.endswith("references/spec.md")
    assert "no design spec" not in " ".join(run_reconcile_context(layout, "work/feature-x").warnings)


def test_renamed_design_artifact_end_to_end(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _set_pipeline(layout, artifacts=RENAMED)
    _write(layout, "work/feature-x", phase="design")
    refused = stage.run_stage_advance(layout, "work/feature-x", today=TODAY, dry_run=False)
    assert refused.outcome.plan.refusal == "artifact-missing"
    spec = layout.bundle_dir / "work/feature-x/references/spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("# Spec\n", encoding="utf-8")
    advanced = stage.run_stage_advance(layout, "work/feature-x", today=TODAY, dry_run=False)
    assert advanced.outcome.plan.refusal is None
    page = (layout.bundle_dir / "work/feature-x.md").read_text(encoding="utf-8")
    assert "resource: /work/feature-x/references/spec.md" in page
    assert "id: design" in page


def test_execute_advance_upserts_and_derives_obligations_from_the_renamed_coverage(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "code")
    layout = _workspace(tmp_path / "ws", f"version: 1\nrepositories:\n  code:\n    path: {repo}\n")
    _set_pipeline(layout, artifacts=RENAMED)
    start = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _write(layout, "work/feature-x", phase="execute", work_status="in-progress", extra=f"start_sha: {start}\n")
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    (repo / "packages/a/x.py").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "change"], cwd=repo, check=True, capture_output=True)
    gate_ready(layout, repo, "work/feature-x")
    coverage = layout.bundle_dir / "work/feature-x/references/coverage.md"
    coverage.parent.mkdir(parents=True, exist_ok=True)
    coverage.write_text("- [x] one\n- [ ] two -- not done\n", encoding="utf-8")
    result = stage.run_stage_advance(layout, "work/feature-x", today=TODAY, dry_run=False, cwd=repo)
    assert result.outcome.plan.refusal is None
    assert result.outcome.written
    assert result.results_path is not None
    results = result.results_path.read_text(encoding="utf-8")
    assert f"**Commits:** `{start[:7]}`.." in results
    assert "(1 commit)" in results
    assert "packages/a/x.py" in results
    page = (layout.bundle_dir / "work/feature-x.md").read_text(encoding="utf-8")
    assert "resource: /work/feature-x/references/coverage.md" in page
    obligations = load(layout.bundle_dir / "work/feature-x.md").fm_raw["finish_obligations"]
    assert obligations[0]["text"] == "two -- not done"
    assert obligations[0]["origin"] == "coverage"


def test_plan_lookup_for_affects_drift_reads_the_renamed_plan(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _set_pipeline(layout, artifacts={"plan": {"file": "the-plan.md"}})
    _write(layout, "work/feature-x", phase="plan")
    plan = layout.bundle_dir / "work/feature-x/references/the-plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("**Files:**\n- Modify: `packages/a/x.py`\n", encoding="utf-8")
    definition = load_dispatch_config(layout).definition
    item = next(i for i in load_items(load_bundle(layout.bundle_dir, ignore=IGNORE)) if i.path == "work/feature-x")
    warnings = stage._affects_drift_warnings(layout.bundle_dir, item, definition=definition)
    assert "no plan artifact to read" not in " ".join(warnings)


def test_next_artifact_names_the_renamed_design(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _set_pipeline(layout, artifacts=RENAMED)
    _write(layout, "work/feature-x", phase="design")
    result = work.run_next(layout, "work/feature-x")
    assert result.artifact is not None and result.artifact.rel.endswith("references/spec.md")


def test_execute_less_path_finishes_without_a_gate_or_obligations(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "code")
    layout = _workspace(tmp_path / "ws", f"version: 1\nrepositories:\n  code:\n    path: {repo}\n")
    _set_pipeline(
        layout,
        path=[{"name": "no-execute", "match": {"type": "Feature"}, "stages": ["design", "plan", "finish"]}],
    )
    _write(layout, "work/feature-x", phase="plan")
    assert work.run_regen_indexes(layout, dry_run=False).application.ok
    references = layout.bundle_dir / "work/feature-x/references"
    references.mkdir(parents=True, exist_ok=True)
    (references / "02-plan.md").write_text("# Plan\n", encoding="utf-8")
    (references / "03-execute-coverage.md").write_text("- [ ] must not derive\n", encoding="utf-8")

    result = stage.run_stage_advance(layout, "work/feature-x", today=TODAY, dry_run=False, cwd=repo)

    assert result.outcome.written, result.outcome.plan
    page = load(layout.bundle_dir / "work/feature-x.md")
    assert page.fm_raw["phase"] == "finish"
    assert "finish_obligations" not in page.fm_raw
    assert "execute-coverage" not in {source["id"] for source in page.fm_raw.get("sources", [])}
    assert result.results_path is None
    assert result.gate_receipt is None


def test_packaged_execute_to_finish_still_requires_a_gate_receipt(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "code")
    layout = _workspace(
        tmp_path / "ws", f"version: 1\nrepositories:\n  code:\n    path: {repo}\n    gate:\n      full: 'true'\n"
    )
    start = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _write(layout, "work/feature-x", phase="execute", work_status="in-progress", extra=f"start_sha: {start}\n")
    (repo / "packages/a/x.py").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "change"], cwd=repo, check=True, capture_output=True)
    page = layout.bundle_dir / "work/feature-x.md"
    before = page.read_bytes()

    result = stage.run_stage_advance(layout, "work/feature-x", today=TODAY, dry_run=False, cwd=repo)

    assert result.outcome.plan.refusal == "no-gate-receipt"
    assert page.read_bytes() == before


def test_a_return_from_finish_never_runs_the_gate(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "code")
    layout = _workspace(tmp_path / "ws", f"version: 1\nrepositories:\n  code:\n    path: {repo}\n")
    _write(layout, "work/feature-x", phase="finish", work_status="accepted", extra="owner: pat\n")
    assert work.run_regen_indexes(layout, dry_run=False).application.ok

    result = stage.run_stage_advance(
        layout, "work/feature-x", today=TODAY, dry_run=False, cwd=repo, return_=True, return_scope=("Rework",)
    )

    assert result.outcome.written, result.outcome.plan
    assert load(layout.bundle_dir / "work/feature-x.md").fm_raw["phase"] == "execute"
    assert not any("execute -> finish gate" in warning for warning in result.warnings)
    assert result.results_path is None


def write_receipt(bundle_dir: Path, owner: str, *, worktree, tree, scope="full", command="true", exit=0) -> None:
    target = bundle_dir / owner / "references" / "03-gate-receipts.md"
    runs = list(parse_gate_receipt(target.read_text(encoding="utf-8"))[1]) if target.exists() else []
    runs.append(
        GateRun(
            run_id=f"20260928T12000{len(runs)}Z-0000000{len(runs)}",
            repo="code",
            worktree=str(worktree),
            head="b" * 40,
            tree=tree,
            clean=True,
            tree_changed=False,
            scope=scope,
            command=command,
            names=(),
            exit=exit,
            log_path="/l",
            log_tail="",
            started=f"2026-09-28T12:00:0{len(runs)}Z",
            duration_s=1.0,
        )
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_receipt(owner, runs, created="2026-09-28"), encoding="utf-8", newline="\n")


def gate_ready(layout, declared: Path, item: str, *, tree_of: Path | None = None) -> None:
    """Declare *declared* as repository `code` with a full gate, and mint a green receipt for
    the current tree of *tree_of* (default: *declared*) -- for tests that advance a clean item
    past execute -> finish and are not about the gate."""
    import subprocess

    text = layout.manifest_path.read_text(encoding="utf-8")
    block = f"repositories:\n  code:\n    path: {json.dumps(str(declared))}\n    gate:\n      full: 'true'\n"
    text = re.sub(r"^repositories:.*(?:\n[ ].*)*\n", block, text, count=1, flags=re.MULTILINE)
    layout.manifest_path.write_text(text, encoding="utf-8", newline="\n")
    root = tree_of or declared
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    write_receipt(layout.bundle_dir, item, worktree=root, tree=tree)


def test_missing_optional_plan_advances_without_registering_a_missing_file(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    path = "work/feature-optional"
    _write(layout, path, phase="plan")
    _set_pipeline(layout, artifacts={"plan": {"file": "plan.md", "required": False}})

    result = stage.run_stage_advance(layout, path, today=TODAY, dry_run=False)

    assert result.application is not None and result.application.ok
    assert result.outcome.written
    assert result.outcome.stamped is None
    assert not result.outcome.plan_row
    page = load(layout.bundle_dir / f"{path}.md")
    assert page.fm_data()["phase"] == "execute"
    assert not any(source.id == "plan" for source in page.fm.sources)
    assert "references/plan.md" not in page.body
    following = work.run_next(layout, path)
    assert not following.state.has_plan_doc
    assert following.dispatch_resolution is not None
    assert following.dispatch_resolution.profile.skill == "superpowers:test-driven-development"


def test_off_path_execute_repair_does_not_run_completion_gates(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "code")
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "worktree", "add", "-b", "repair", str(checkout)], cwd=repo, check=True, capture_output=True)
    layout = _workspace(tmp_path / "ws", f"version: 1\nrepositories:\n  code:\n    path: {repo}\n")
    path = "work/feature-repair"
    _write(
        layout, path, phase="execute", work_status="in-progress", extra="owner: tester\nstart_sha: " + "a" * 40 + "\n"
    )
    _set_pipeline(
        layout,
        path=[{"match": {}, "stages": ["design", "finish"]}],
        artifacts={"execute": {"file": "coverage.md", "required": True}},
    )
    result = stage.run_stage_advance(layout, path, today=TODAY, expected_phase="execute", cwd=checkout, dry_run=False)
    assert result.outcome.plan.refusal is None
    assert result.application is not None and result.application.ok
    assert result.outcome.plan.trigger == "repair"
    assert result.results_path is None
    page = load(layout.bundle_dir / f"{path}.md")
    assert page.fm_data()["phase"] == "finish"
    assert page.fm_data()["work_status"] == "in-progress"
    assert not page.fm.sources
    assert "worktree" not in page.fm_data() and "branch" not in page.fm_data()
    assert page.fm_data()["start_sha"] == "a" * 40
