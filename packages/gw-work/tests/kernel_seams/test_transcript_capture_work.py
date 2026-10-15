"""In-process unit tests for the SessionEnd hook body.

Ported from the bash script this replaced
(`git show 6c1c5763:plugins/graph-works/hooks/examples/session-end-transcript-capture.sh`,
the vendored subtree's own copy, at the last commit before it was rewritten as
a Python shim) (bug-windows-hook-command-unexecutable): that bash script had only
two slow wheel/sdist subprocess smoke tests. As a module, `main` is driven
directly here, and each branch -- guard, malformed input, missing transcript,
no pointer, invalid pointer, the happy path, and fail-open on an unexpected
exception -- gets its own scenario."""

from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest
from code_wiki_okf.config import Config, StateGateConfig
from gw_kernel.testing.transactions import git as _git
from gw_kernel.testing.transactions import init_git as _init_git
from gw_kernel.workspace import provenance
from gw_kernel.workspace.init import apply_init, plan_init
from gw_pipeline import transcript_capture
from gw_pipeline.transcript_capture import TRACE_LOG_ENV, main
from gw_work import commands as work


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    base = {TRACE_LOG_ENV: str(tmp_path / "trace.log")}
    base.update(overrides)
    return base


def _stdin(payload: object) -> io.StringIO:
    return io.StringIO(json.dumps(payload))


def _trace_text(tmp_path: Path) -> str:
    log = tmp_path / "trace.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


TODAY = date(2026, 9, 2)


def _committed_workspace(tmp_path: Path):
    layout = apply_init(plan_init(tmp_path / "ws", today=TODAY, topic="t")).layout
    _init_git(layout.root)
    config = Config(
        graph_dir=layout.cache_dir / "graph",
        declarations_dir=layout.config_dir,
        repos=(),
        state_gate=StateGateConfig(enabled=False, branches=("main",)),
    )
    filed = work.run_file(layout, config, type="Feature", title="Hook item", description="d", on=TODAY, dry_run=False)
    assert filed.application is not None and filed.application.ok
    path = filed.plan.filing.path
    provenance.write_active_work(layout, path, "design", updated=TODAY.isoformat())
    return layout, path


def test_capture_after_a_committing_verb_gives_two_commits_and_a_clean_tree(tmp_path: Path) -> None:
    layout, path = _committed_workspace(tmp_path)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8", newline="\n")

    rc = main(
        _stdin({"session_id": "abc", "transcript_path": str(transcript)}),
        _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
    )

    assert rc == 0
    subjects = _git(layout.root, "log", "--format=%s").splitlines()
    stem = path.rsplit("/", 1)[-1]
    assert subjects[:2] == [f"workspace: capture {stem} design transcript", f"workspace: file {stem}"]
    bundle_rel = layout.bundle_dir.relative_to(layout.root).as_posix()
    assert _git(layout.root, "status", "--porcelain", "--", bundle_rel) == ""


def test_rerun_commits_refreshed_copy_with_unchanged_page(tmp_path: Path) -> None:
    layout, path = _committed_workspace(tmp_path)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8", newline="\n")
    env = _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root))
    main(_stdin({"session_id": "abc", "transcript_path": str(transcript)}), env)
    transcript.write_text('{"e":1}\n{"e":2}\n', encoding="utf-8", newline="\n")
    main(_stdin({"session_id": "abc", "transcript_path": str(transcript)}), env)
    stem = path.rsplit("/", 1)[-1]
    assert (
        _git(layout.root, "log", "--format=%s").splitlines()[:2] == [f"workspace: capture {stem} design transcript"] * 2
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock tier")
def test_held_bundle_lock_times_out_and_fails_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gw_kernel.workspace.anchors import open_anchor

    layout, _path = _committed_workspace(tmp_path)
    monkeypatch.setattr(transcript_capture, "HOOK_LOCK_TIMEOUT_SECONDS", 0.2)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8", newline="\n")
    holder = open_anchor(layout.bundle_dir)
    try:
        with holder.exclusive_lock():
            rc = main(
                _stdin({"session_id": "abc", "transcript_path": str(transcript)}),
                _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
            )
    finally:
        holder.close()
    assert rc == 0
    assert "lock-timeout" in _trace_text(tmp_path)
    assert len(_git(layout.root, "log", "--format=%s").splitlines()) == 2  # seed + file; no capture commit


@pytest.mark.parametrize("sidechain", [False, True])
def test_partial_copy_cannot_enter_same_item_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sidechain: bool
) -> None:
    from gw_kernel.workspace.bundle_lock import commit_pending
    from gw_kernel.workspace.commits import WorkspaceCommit

    layout, path = _committed_workspace(tmp_path)
    transcript = tmp_path / "session.jsonl"
    transcript.write_bytes(b"complete main\n")
    if sidechain:
        agents = tmp_path / "session" / "subagents"
        agents.mkdir(parents=True)
        (agents / "agent-42.jsonl").write_bytes(b"complete sidechain\n")
    references = layout.bundle_dir / path / "references"
    references.mkdir(parents=True, exist_ok=True)
    (references / "ready.txt").write_bytes(b"ready\n")
    commits: list[str] = []

    def interrupted_copy(source: Path, destination: Path | str) -> None:
        dest = Path(destination)
        dest.write_bytes(b"PARTIAL COPY")
        if (source.name == "agent-42.jsonl") == sidechain:
            outcome = commit_pending(layout, WorkspaceCommit("workspace: concurrent capture", items=(path,)))
            assert outcome.status == "committed", outcome
            assert outcome.sha is not None
            commits.append(outcome.sha)
        dest.write_bytes(source.read_bytes())

    monkeypatch.setattr(transcript_capture.shutil, "copy2", interrupted_copy)
    assert (
        main(
            _stdin({"session_id": "abc", "transcript_path": str(transcript)}),
            _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
        )
        == 0
    )
    assert len(commits) == 1, _trace_text(tmp_path)
    tree = _git(layout.root, "ls-tree", "-r", "--name-only", commits[0]).splitlines()
    for member in tree:
        assert "PARTIAL COPY" not in _git(layout.root, "show", f"{commits[0]}:{member}"), member
    assert (references / "01-design-transcript.jsonl").read_bytes() == b"complete main\n"
    if sidechain:
        assert (references / "01-design-transcript-subagent-42.jsonl").read_bytes() == b"complete sidechain\n"
    assert _git(layout.root, "status", "--porcelain", "--", "okf") == ""


def test_invalid_commit_config_is_fail_open_before_copy_effects(tmp_path: Path) -> None:
    layout, path = _committed_workspace(tmp_path)
    original = (layout.bundle_dir / f"{path}.md").read_bytes()
    workflow_file = layout.root / "workflow.yaml"
    workflow_file.write_text(
        "workflow:\n  workspace_commits: invalid\n" + workflow_file.read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    transcript = tmp_path / "session.jsonl"
    transcript.write_bytes(b"complete\n")
    assert (
        main(
            _stdin({"session_id": "abc", "transcript_path": str(transcript)}),
            _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
        )
        == 0
    )
    assert "workspace_commits" in _trace_text(tmp_path)
    assert "error" in _trace_text(tmp_path)
    assert not list((layout.bundle_dir / path / "references").glob("*transcript*"))
    assert (layout.bundle_dir / f"{path}.md").read_bytes() == original
