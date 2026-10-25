"""A new canonical plan task cannot complete on the first execute's coverage."""

from datetime import date
from hashlib import sha256
from pathlib import Path

from _gate_helpers import gate_ready
from gw_kernel import apply_init, plan_init
from gw_kernel.testing.transactions import git as _git
from gw_kernel.testing.transactions import init_git as _init_git
from gw_orchestrate.stage_advance import run_stage_advance
from gw_work.commands import run_next
from okf_io import load

TODAY = date(2026, 9, 27)
LONE = "work/feature-lone"
PLAN = f"{LONE}/references/02-plan.md"
COVERAGE = f"{LONE}/references/03-execute-coverage.md"
SCOPE = "Implement Task 4 in the canonical plan"


def git(root: Path, *args: str) -> str:
    return _git(root, *args).strip()


def workspace(tmp_path: Path, *items: tuple[str, str, str]):
    """A git workspace declaring one `code` repository, with *items* at execute in it."""
    code = tmp_path / "code"
    code.mkdir()
    _init_git(code)
    layout = apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Workspace e2e")).layout
    layout.manifest_path.write_text(
        layout.manifest_path.read_text(encoding="utf-8").replace(
            "repositories: {}", f"repositories:\n  code:\n    path: {code.as_posix()}"
        ),
        encoding="utf-8",
        newline="\n",
    )
    for path, kind, status in items:
        page = layout.bundle_dir / f"{path}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            f"---\ntype: {kind}\ntitle: Workspace e2e\ndescription: d\nstatus: stable\n"
            f"work_status: {status}\nphase: execute\neffort: medium\nowner: pat\n"
            "opened: 2026-09-27\nupdated: 2026-09-27\n"
            "repo: code\naffects:\n- code:src/example.py\n---\n\n## Summary\nd\n",
            encoding="utf-8",
            newline="\n",
        )
    _init_git(layout.root)
    return layout, code


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def commit(root: Path, message: str) -> None:
    git(root, "add", ".")
    git(root, "commit", "-m", message)


def test_new_plan_task_reaches_returned_worker_and_requires_reported_content(tmp_path: Path) -> None:
    layout, code = workspace(tmp_path, (LONE, "Feature", "in-progress"))
    page = layout.bundle_dir / f"{LONE}.md"
    plan = layout.bundle_dir / PLAN
    baseline = git(code, "rev-parse", "HEAD")
    git(code, "checkout", "-qb", "feat")
    write(code / "src/example.py", "original = True\n")
    commit(code, "implement original plan")
    doc = load(page)
    doc.set("worktree", str(code))
    doc.set("branch", "feat")
    doc.set("start_sha", baseline)
    doc.set("affects", ["src/example.py"])
    doc.save()
    original_plan = "# Plan\n\n### Task 1: Original implementation\n"
    original_coverage = "# Execute coverage\n\n- [x] Original acceptance delivered\n"
    write(plan, original_plan)
    write(layout.bundle_dir / COVERAGE, original_coverage)
    gate_ready(layout, code, LONE)
    commit(layout.root, "record original plan and passing code receipt")

    content = layout.root
    coverage = layout.bundle_dir / COVERAGE
    write(coverage, original_coverage + "- [x] Original implementation verified\n")
    commit(content, "report first execute coverage")
    first = run_stage_advance(layout, LONE, today=TODAY, expected_phase="execute", dry_run=False)
    assert first.outcome.plan.refusal is None, first
    assert first.application is not None and first.application.ok, first
    assert first.outcome.written
    assert first.gate_receipt is not None and first.gate_receipt.owner == LONE
    assert load(page).fm_data()["phase"] == "finish"
    assert "execute_return" not in load(page).fm_data()

    write(plan, original_plan + "\n### Task 4: Fix review finding\n\nImplement the newly reviewed behavior.\n")
    commit(layout.root, "append newly required canonical plan task")
    before_return = coverage.read_bytes()
    returned = run_stage_advance(
        layout,
        LONE,
        today=TODAY,
        expected_phase="finish",
        return_=True,
        return_scope=(SCOPE,),
        dry_run=False,
    )
    assert returned.outcome.plan.refusal is None, returned
    assert returned.application is not None and returned.application.ok, returned
    record = load(page).fm_data()["execute_return"]
    assert record["state"] == "active"
    assert load(page).fm_data()["phase"] == "execute"
    assert coverage.read_bytes().startswith(before_return)
    assert "Report state: prepared" in coverage.read_text(encoding="utf-8")

    next_stage = run_next(layout, LONE)
    assert next_stage.route.dispatch is not None, next_stage
    assert next_stage.route.dispatch.stage == "execute"
    fill = next(slot.fill for slot in next_stage.carried.slots if slot.name == "execute_return")
    assert fill.data["return_id"] == record["id"]
    assert fill.data["scope"] == [{"id": "R1", "text": SCOPE}]
    assert fill.data["plan"] == f"/{PLAN}"
    assert fill.data["plan_sha256"] == sha256(plan.read_bytes()).hexdigest()
    assert fill.data["coverage"] == {"resource": f"/{COVERAGE}", "path": str(coverage)}
    assert any(str(plan) in line for line in fill.lines)
    assert fill.warnings == ()

    # The old code receipt is still valid; only fresh returned evidence is missing.
    before_retry = tuple(git(root, "rev-parse", "HEAD") for root in (layout.root, content))
    immediate = run_stage_advance(layout, LONE, today=TODAY, expected_phase="execute", dry_run=False)
    assert immediate.outcome.plan.refusal == "return-evidence-stale", immediate
    assert immediate.gate_receipt == first.gate_receipt
    assert not immediate.outcome.written
    assert load(page).fm_data()["phase"] == "execute"
    assert load(page).fm_data()["execute_return"]["state"] == "active"
    assert tuple(git(root, "rev-parse", "HEAD") for root in (layout.root, content)) == before_retry

    write(code / "src/example.py", "original = True\nreview_finding_fixed = True\n")
    commit(code, "implement Task 4 review finding")
    reported = coverage.read_text(encoding="utf-8").replace("Report state: prepared", "Report state: reported")
    reported = reported.replace(f"- [ ] R1: {SCOPE}", f"- [x] R1: {SCOPE} -- implemented and verified")
    write(coverage, reported)
    commit(content, "report returned scope implementation")
    gate_ready(layout, code, LONE)
    commit(layout.root, "record passing receipt for Task 4 code tree")
    completed = run_stage_advance(layout, LONE, today=TODAY, expected_phase="execute", dry_run=False)
    assert completed.outcome.plan.refusal is None, completed
    assert completed.application is not None and completed.application.ok, completed
    assert completed.outcome.written
    assert completed.gate_receipt is not None and completed.gate_receipt.owner == LONE
    assert completed.gate_receipt.run_id != first.gate_receipt.run_id
    final = load(page).fm_data()
    assert final["phase"] == "finish"
    assert final["execute_return"] == {**record, "state": "completed"}
    assert not final.get("finish_obligations")
    assert all(git(root, "status", "--porcelain") == "" for root in (layout.root, content, code))
