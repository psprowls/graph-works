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
from _transaction_helpers import _git, _init_git
from code_wiki_okf.config import Config, StateGateConfig
from graph_works_core import apply_init, plan_init, transcript_capture
from graph_works_core.transcript_capture import GUARD_ENV, TRACE_LOG_ENV, main
from graph_works_core.work import commands as work
from graph_works_core.workspace import provenance


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    base = {TRACE_LOG_ENV: str(tmp_path / "trace.log")}
    base.update(overrides)
    return base


def _stdin(payload: object) -> io.StringIO:
    return io.StringIO(json.dumps(payload))


def _trace_text(tmp_path: Path) -> str:
    log = tmp_path / "trace.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_guard_zero_skips_and_traces(tmp_path: Path) -> None:
    rc = main(_stdin({"session_id": "abc", "transcript_path": "x"}), _env(tmp_path, **{GUARD_ENV: "0"}))
    assert rc == 0
    assert "skip" in _trace_text(tmp_path)
    assert "guard=0" in _trace_text(tmp_path)


def test_malformed_json_is_traced_as_error_and_returns_zero(tmp_path: Path) -> None:
    rc = main(io.StringIO("not json"), _env(tmp_path))
    assert rc == 0
    assert "error" in _trace_text(tmp_path)


def test_non_object_payload_is_traced_as_error_and_returns_zero(tmp_path: Path) -> None:
    rc = main(_stdin(["not", "an", "object"]), _env(tmp_path))
    assert rc == 0
    assert "error" in _trace_text(tmp_path)


def test_missing_transcript_path_skips(tmp_path: Path) -> None:
    rc = main(_stdin({"session_id": "abc"}), _env(tmp_path))
    assert rc == 0
    assert "no-transcript" in _trace_text(tmp_path)


def test_nonexistent_transcript_path_skips(tmp_path: Path) -> None:
    rc = main(_stdin({"session_id": "abc", "transcript_path": str(tmp_path / "missing.jsonl")}), _env(tmp_path))
    assert rc == 0
    assert "no-transcript" in _trace_text(tmp_path)


def test_no_active_work_pointer_skips(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8")
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 2), topic="t")).layout

    rc = main(
        _stdin({"session_id": "abc", "transcript_path": str(transcript)}),
        _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
    )

    assert rc == 0
    assert "no-pointer" in _trace_text(tmp_path)


def test_invalid_pointer_path_skips(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8")
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 2), topic="t")).layout
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    (layout.cache_dir / "active-work.json").write_text(
        json.dumps({"path": "not/a/work/path", "phase": "execute"}) + "\n", encoding="utf-8"
    )

    rc = main(
        _stdin({"session_id": "abc", "transcript_path": str(transcript)}),
        _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
    )

    assert rc == 0
    assert "invalid-path" in _trace_text(tmp_path)


def test_happy_path_copies_main_transcript_and_subagent_sidechains(tmp_path: Path) -> None:
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 2), topic="t")).layout
    work_path = "work/feature-x"
    page = layout.bundle_dir / f"{work_path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntype: Feature\n---\n", encoding="utf-8")
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    (layout.cache_dir / "active-work.json").write_text(
        json.dumps({"path": work_path, "phase": "execute"}) + "\n", encoding="utf-8"
    )

    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8")
    sidechain_dir = tmp_path / "session" / "subagents"
    sidechain_dir.mkdir(parents=True)
    (sidechain_dir / "agent-42.jsonl").write_text('{"e":2}\n', encoding="utf-8")

    rc = main(
        _stdin({"session_id": "abcdef123456", "transcript_path": str(transcript)}),
        _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
    )

    assert rc == 0
    references = layout.bundle_dir / work_path / "references"
    assert (references / "03-execute-transcript.jsonl").read_text(encoding="utf-8") == '{"e":1}\n'
    assert (references / "03-execute-transcript-subagent-42.jsonl").read_text(encoding="utf-8") == '{"e":2}\n'
    assert "copied" in _trace_text(tmp_path)

    from okf_io import load

    document = load(page)
    sources = document.fm_data().get("sources")
    assert sources == [
        {
            "id": "execute-transcript",
            "resource": "/work/feature-x/references/03-execute-transcript.jsonl",
            "title": "Execute session transcript",
        }
    ]


def test_rerun_with_unchanged_transcript_does_not_duplicate_the_sources_entry(tmp_path: Path) -> None:
    from okf_io import load

    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 2), topic="t")).layout
    work_path = "work/feature-x"
    page = layout.bundle_dir / f"{work_path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntype: Feature\n---\n", encoding="utf-8")
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    (layout.cache_dir / "active-work.json").write_text(
        json.dumps({"path": work_path, "phase": "execute"}) + "\n", encoding="utf-8"
    )

    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8")

    env = _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root))
    payload = {"session_id": "abcdef123456", "transcript_path": str(transcript)}

    rc_first = main(_stdin(payload), env)
    rc_second = main(_stdin(payload), env)

    assert rc_first == 0
    assert rc_second == 0
    document = load(page)
    sources = document.fm_data().get("sources")
    assert sources == [
        {
            "id": "execute-transcript",
            "resource": "/work/feature-x/references/03-execute-transcript.jsonl",
            "title": "Execute session transcript",
        }
    ]


def test_exception_mid_copy_is_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 2), topic="t")).layout
    work_path = "work/feature-x"
    page = layout.bundle_dir / f"{work_path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntype: Feature\n---\n", encoding="utf-8")
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    (layout.cache_dir / "active-work.json").write_text(
        json.dumps({"path": work_path, "phase": "execute"}) + "\n", encoding="utf-8"
    )
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"e":1}\n', encoding="utf-8")

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk exploded")

    monkeypatch.setattr("shutil.copy2", _boom)

    rc = main(
        _stdin({"session_id": "abc", "transcript_path": str(transcript)}),
        _env(tmp_path, GRAPH_WORKS_DIR=str(layout.root)),
    )

    assert rc == 0
    assert "error" in _trace_text(tmp_path)
    assert "disk exploded" in _trace_text(tmp_path)


def test_default_trace_log_lives_under_tempfile_gettempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tempfile as tempfile_module

    monkeypatch.setattr(tempfile_module, "gettempdir", lambda: str(tmp_path))
    rc = main(_stdin({"session_id": "abc"}), {})

    assert rc == 0
    assert (tmp_path / "claude-hooks" / "transcript-capture-trace.log").exists()


def test_trace_write_failure_is_swallowed(tmp_path: Path) -> None:
    # A directory where the trace log file would go: mkdir/open both fail with OSError.
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "trace.log").mkdir()

    rc = main(_stdin({"session_id": "abc"}), _env(tmp_path, **{TRACE_LOG_ENV: str(blocked / "trace.log")}))

    assert rc == 0


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
    from graph_works_core.workspace.anchors import open_anchor

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
    from graph_works_core.workspace.commits import WorkspaceCommit
    from graph_works_core.workspace.transactions import commit_pending

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
    manifest = layout.manifest_path.read_text(encoding="utf-8")
    layout.manifest_path.write_text(
        manifest.replace("workflow:\n", "workflow:\n  workspace_commits: invalid\n"), encoding="utf-8", newline="\n"
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
