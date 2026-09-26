"""Real advances serialize workspace commits without sweeping sibling references."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path
from queue import Queue
from threading import Thread
from time import perf_counter

from _transaction_helpers import _git, _init_git
from graph_works_core import apply_init, plan_init, transcript_capture
from graph_works_core.orchestrate.stage_advance import run_stage_advance
from graph_works_core.work.commands import run_regen_indexes
from graph_works_core.workspace import provenance
from okf_io import load

TODAY = date(2026, 9, 2)
SCRIPT = textwrap.dedent(
    """
    import sys
    from datetime import date
    from pathlib import Path
    from graph_works_core.orchestrate.stage_advance import run_stage_advance
    from graph_works_core.workspace.layout import layout_for

    layout, item = layout_for(Path(sys.argv[1])), sys.argv[2]
    print("ready", flush=True)
    assert sys.stdin.readline() == "advance\\n"
    result = run_stage_advance(layout, item, today=date(2026, 9, 2), dry_run=False)
    assert result.application is not None and result.application.ok, result
    assert result.application.commit is not None and result.application.commit.status == "committed", result
    """
)


def _ready(tmp_path: Path, items: tuple[str, ...], *, phase: str = "design"):
    layout = apply_init(plan_init(tmp_path / "ws", today=TODAY, topic="Commit tests")).layout
    for item in items:
        page = layout.bundle_dir / f"{item}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            f"---\ntype: Feature\ntitle: {item}\ndescription: d\nstatus: stable\n"
            f"work_status: in-progress\nphase: {phase}\neffort: medium\n"
            "opened: 2026-08-01\nupdated: 2026-08-01\naffects: []\n---\n\n"
            "## Summary\nd\n\n## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n",
            encoding="utf-8",
            newline="\n",
        )
    assert run_regen_indexes(layout, dry_run=False).application.ok
    _init_git(layout.root)
    if phase == "design":
        for item in items:
            refs = layout.bundle_dir / item / "references"
            refs.mkdir(parents=True)
            (refs / "01-design.md").write_text("# Design\n", encoding="utf-8", newline="\n")
    return layout


def _paths(root: Path, sha: str) -> set[str]:
    return set(_git(root, "show", "--name-only", "--format=", sha).splitlines())


def test_concurrent_sibling_commits_stay_disjoint(tmp_path: Path) -> None:
    items = ("work/feature-a", "work/feature-b")
    layout = _ready(tmp_path, items)
    initial = _git(layout.root, "rev-parse", "HEAD").strip()
    for item in items:
        placement = layout.bundle_dir / item / "references/orca-placement/k.json"
        placement.parent.mkdir()
        placement.write_text("{}\n", encoding="utf-8", newline="\n")
    procs = []
    try:
        for item in items:
            procs.append(
                subprocess.Popen(
                    [sys.executable, "-c", SCRIPT, str(layout.root), item],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        # Both interpreters import the real advance entry point before either starts.
        ready: Queue[str] = Queue()

        def await_ready(proc: subprocess.Popen[str]) -> None:
            assert proc.stdout is not None
            ready.put(proc.stdout.readline())

        for proc in procs:
            Thread(target=await_ready, args=(proc,), daemon=True).start()
        for _ in procs:
            assert ready.get(timeout=30) == "ready\n"
        for proc in procs:
            proc.stdin.write("advance\n")
            proc.stdin.flush()
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=120)
            assert proc.returncode == 0, (stdout, stderr)
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
    commits = _git(layout.root, "rev-list", f"{initial}..HEAD").splitlines()
    assert len(commits) == 2
    subjects = set()
    for sha in commits:
        subject = _git(layout.root, "log", "-1", "--format=%B", sha).strip()
        subjects.add(subject)
        stem = subject.split()[2]
        assert _paths(layout.root, sha) == {
            f"okf/work/{stem}.md",
            f"okf/work/{stem}/references/01-design.md",
            f"okf/work/{stem}/references/orca-placement/k.json",
        }
    assert subjects == {f"workspace: advance {item.split('/')[-1]} design -> plan" for item in items}
    for item in items:
        assert load(layout.bundle_dir / f"{item}.md").fm_data()["phase"] == "plan"
    assert _git(layout.root, "status", "--porcelain") == ""


def test_advance_then_capture_commits_exact_paths(tmp_path: Path) -> None:
    item = "work/feature-a"
    layout = _ready(tmp_path, (item,))
    initial = _git(layout.root, "rev-parse", "HEAD").strip()
    provenance.write_active_work(layout, item, "design", updated=TODAY.isoformat())
    started = perf_counter()
    result = run_stage_advance(layout, item, today=TODAY, dry_run=False)
    elapsed = perf_counter() - started
    print(f"real advance latency: {elapsed:.6f} s")
    assert result.application is not None and result.application.ok
    assert result.application.commit.status == "committed"
    assert _paths(layout.root, "HEAD") == {f"okf/{item}.md", f"okf/{item}/references/01-design.md"}
    assert _git(layout.root, "status", "--porcelain") == ""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"event":1}\n', encoding="utf-8", newline="\n")
    rc = transcript_capture.main(
        io.StringIO(json.dumps({"session_id": "abc", "transcript_path": str(transcript)})),
        {"GRAPH_WORKS_DIR": str(layout.root), transcript_capture.TRACE_LOG_ENV: str(tmp_path / "trace.log")},
    )
    assert rc == 0
    assert _git(layout.root, "log", "--format=%s", f"{initial}..HEAD").splitlines() == [
        "workspace: capture feature-a design transcript",
        "workspace: advance feature-a design -> plan",
    ]
    assert _paths(layout.root, "HEAD") == {f"okf/{item}.md", f"okf/{item}/references/01-design-transcript.jsonl"}
    assert (layout.bundle_dir / item / "references/01-design-transcript.jsonl").read_bytes() == transcript.read_bytes()
    assert load(layout.bundle_dir / f"{item}.md").fm_data()["phase"] == "plan"
    assert _git(layout.root, "status", "--porcelain") == ""


def test_terminal_advance_commit_subject_marks_resolved(tmp_path: Path) -> None:
    item = "work/feature-a"
    layout = _ready(tmp_path, (item,), phase="finish")
    result = run_stage_advance(layout, item, today=TODAY, resolved_in="pr-1", dry_run=False)
    assert result.application is not None and result.application.ok
    assert result.application.commit.status == "committed"
    assert _git(layout.root, "log", "-1", "--format=%B").strip() == (
        "workspace: advance feature-a finish -> done (resolved)"
    )
    assert _git(layout.root, "rev-list", "--count", "HEAD").strip() == "2"
    assert _paths(layout.root, "HEAD") == {f"okf/{item}.md"}
    assert load(layout.bundle_dir / f"{item}.md").fm_data()["work_status"] == "resolved"
    assert _git(layout.root, "status", "--porcelain") == ""
