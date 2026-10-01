from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from threading import Event, Thread

import pytest
from gitrepo import Upstream, git, make_upstream
from graph_works_core import __version__ as GW_VERSION
from graph_works_core.repositories import commands
from graph_works_core.repositories.commands import run_repo_add, run_repo_advance
from graph_works_core.workspace.commits import CommitOutcome
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.transactions import held_bundle_lock
from okf_io import load
from repositories_okf.git import GitFailure
from repositories_okf.lifecycle import scan_config_hash
from repositories_okf.pin import read_pin
from workspace_fixture import NOW, bundle_bytes, make_workspace, porcelain

LATER = NOW + timedelta(days=1)


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    (tmp_path / "up").mkdir()
    up = make_upstream(tmp_path / "up")
    up.commit({"README.md": "# demo\n", "src/a.py": "a = 1\n"}, "c1")
    return up


@pytest.fixture
def layout(tmp_path: Path, upstream: Upstream) -> WorkspaceLayout:
    layout = make_workspace(tmp_path)
    assert run_repo_add(layout, upstream.url, name="demo", managed=True, now=NOW).ok
    return layout


def _clone(layout: WorkspaceLayout) -> Path:
    return layout.bundle_dir / "repositories" / "demo" / "references" / "git"


def _checkout(layout: WorkspaceLayout) -> Path:
    return layout.worktrees_dir / "demo" / "main"


def _merge_on_track(layout: WorkspaceLayout, text: str = "a = 2\n") -> str:
    (_checkout(layout) / "src" / "a.py").write_text(text, encoding="utf-8", newline="")
    git(_checkout(layout), "commit", "-qam", "merged")
    return git(_checkout(layout), "rev-parse", "HEAD")


def _fake_scan(layout: WorkspaceLayout, *, errors: Sequence[str] = ()):
    calls: list[str] = []

    def rescan() -> Sequence[str]:
        calls.append(git(_clone(layout), "rev-parse", "HEAD"))
        page = layout.bundle_dir / "code-graph" / "demo.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            f"---\ntype: Repository\ntitle: demo\n---\n\nscanned at {calls[-1]}\n", encoding="utf-8", newline=""
        )
        return tuple(errors)

    return rescan, calls


def test_advance_detaches_rescans_re_pins_and_commits(layout: WorkspaceLayout) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    new = _merge_on_track(layout)
    rescan, calls = _fake_scan(layout)
    result = run_repo_advance(layout, "demo", now=LATER, rescan=rescan)
    assert result.ok and result.outcome == "advanced" and result.managed, result.refusal
    assert (result.previous, result.commit, result.commits) == (old, new, 1)
    assert calls == [new]  # scan ran against the clone already detached at the new commit
    assert git(_clone(layout), "rev-parse", "HEAD") == new
    pin = read_pin(load(layout.bundle_dir / "repositories" / "demo.md"))
    assert pin is not None and (pin.commit, pin.previous, pin.ref) == (new, old, "main")
    entry = next(repo for repo in load_workspace_config(layout).repos if repo.name == "demo")
    expected_hash = scan_config_hash(
        "okf/repositories/demo/references/git", list(entry.ignore)
    )  # bootstrap seeds a global ignore list
    assert pin.generation is not None and (pin.generation.gw_version, pin.generation.scan_config_hash) == (
        GW_VERSION,
        expected_hash,
    )
    assert "code-graph/demo.md" in result.paths and "repositories/demo.md" in result.paths
    assert git(layout.root, "log", "-1", "--format=%s") == f"workspace: advance managed repository demo to {new[:7]}"
    assert porcelain(layout) == ""
    second, _ = _fake_scan(layout)
    assert run_repo_advance(layout, "demo", now=LATER, rescan=second).outcome == "up-to-date"


def test_a_scan_failure_leaves_head_pin_and_bundle_at_the_old_commit(layout: WorkspaceLayout) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    _merge_on_track(layout)
    before = bundle_bytes(layout)
    rescan, _ = _fake_scan(layout, errors=("graph build failed",))
    result = run_repo_advance(layout, "demo", now=LATER, rescan=rescan)
    assert (
        result.refusal is not None
        and result.refusal.code == "scan-failed"
        and "graph build failed" in result.refusal.detail
    )
    assert git(_clone(layout), "rev-parse", "HEAD") == old
    assert bundle_bytes(layout) == before and porcelain(layout) == ""


def test_a_scan_that_raises_is_a_scan_failure(layout: WorkspaceLayout) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    _merge_on_track(layout)

    def rescan() -> Sequence[str]:
        raise OSError("graph dir unwritable")

    result = run_repo_advance(layout, "demo", now=LATER, rescan=rescan)
    assert result.refusal is not None and result.refusal.code == "scan-failed"
    assert git(_clone(layout), "rev-parse", "HEAD") == old


def test_a_write_failure_after_the_scan_rolls_back_scan_output_too(
    layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    _merge_on_track(layout)
    before = bundle_bytes(layout)

    def boom(*_args: object, **_kwargs: object) -> str | None:
        raise OSError("disk full")

    monkeypatch.setattr(commands, "_append_log", boom)
    rescan, _ = _fake_scan(layout)
    result = run_repo_advance(layout, "demo", now=LATER, rescan=rescan)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert git(_clone(layout), "rev-parse", "HEAD") == old
    assert bundle_bytes(layout) == before and porcelain(layout) == ""


def test_a_commit_failure_rolls_back_scan_and_pin(layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    _merge_on_track(layout)
    before = bundle_bytes(layout)

    def failed_commit(*_args: object, **_kwargs: object) -> tuple[CommitOutcome, tuple[str, ...]]:
        return CommitOutcome("failed", None, "advance", (), "hook failed"), ()

    monkeypatch.setattr(commands, "_commit", failed_commit)
    rescan, _ = _fake_scan(layout)
    result = run_repo_advance(layout, "demo", now=LATER, rescan=rescan)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert git(_clone(layout), "rev-parse", "HEAD") == old
    assert bundle_bytes(layout) == before and porcelain(layout) == ""


def test_dry_run_reports_new_and_count_and_writes_nothing(layout: WorkspaceLayout) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    _merge_on_track(layout)
    new = _merge_on_track(layout, "a = 3\n")
    before = bundle_bytes(layout)
    result = run_repo_advance(layout, "demo", now=LATER, dry_run=True)
    assert result.ok and result.dry_run and (result.commit, result.commits) == (new, 2)
    assert git(_clone(layout), "rev-parse", "HEAD") == old and bundle_bytes(layout) == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_rev_count_failure_refuses_before_detach_or_scan(
    layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    _merge_on_track(layout)
    rescan, calls = _fake_scan(layout)
    monkeypatch.setattr(commands, "rev_count", lambda *_args: GitFailure("nonzero", "git rev-list", "bad range"))
    result = run_repo_advance(layout, "demo", now=LATER, dry_run=dry_run, rescan=rescan)
    assert result.refusal is not None and result.refusal.code == "git-failed"
    assert "bad range" in result.refusal.detail
    assert calls == [] and git(_clone(layout), "rev-parse", "HEAD") == old


def test_scan_rollback_preserves_a_writer_waiting_for_the_bundle_lock(layout: WorkspaceLayout) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    _merge_on_track(layout)
    attempted, written = Event(), Event()
    human_page = layout.bundle_dir / "concepts" / "human.md"
    scan_page = layout.bundle_dir / "code-graph" / "demo.md"
    early_writes: list[bool] = []
    worker: Thread | None = None

    def write_human_page() -> None:
        attempted.set()
        with held_bundle_lock(layout):
            human_page.parent.mkdir(parents=True, exist_ok=True)
            human_page.write_text("human edit\n", encoding="utf-8", newline="")
        written.set()

    def rescan() -> Sequence[str]:
        nonlocal worker
        scan_page.parent.mkdir(parents=True, exist_ok=True)
        scan_page.write_text("scan output\n", encoding="utf-8", newline="")
        worker = Thread(target=write_human_page, daemon=True)
        worker.start()
        assert attempted.wait(2)
        early_writes.append(written.wait(0.2))
        return ("scan failed",)

    result = run_repo_advance(layout, "demo", now=LATER, rescan=rescan)
    assert worker is not None
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert early_writes == [False]
    assert result.refusal is not None and result.refusal.code == "scan-failed"
    assert git(_clone(layout), "rev-parse", "HEAD") == old
    assert not scan_page.exists() and human_page.read_text(encoding="utf-8") == "human edit\n"


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (
            lambda layout: (_checkout(layout) / "wip.txt").write_text("x\n", encoding="utf-8", newline=""),
            "checkout-dirty",
        ),
        (lambda layout: git(_checkout(layout), "checkout", "-q", "-b", "feature"), "checkout-off-track"),
        (
            lambda layout: (
                (layout.bundle_dir / "concepts").mkdir(exist_ok=True)
                or (layout.bundle_dir / "concepts" / "wip.md").write_text("x\n", encoding="utf-8", newline="")
            ),
            "workspace-dirty",
        ),
        (lambda layout: git(_clone(layout), "checkout", "-q", "-b", "oops"), "clone-not-detached"),
        (
            lambda layout: (_clone(layout) / "scratch.txt").write_text("x\n", encoding="utf-8", newline=""),
            "clone-dirty",
        ),
        (
            lambda layout: git(_clone(layout), "worktree", "remove", "--force", str(_checkout(layout))),
            "checkout-missing",
        ),
    ],
)
def test_preconditions_refuse_before_anything_moves(layout: WorkspaceLayout, setup, code: str) -> None:
    old = git(_clone(layout), "rev-parse", "HEAD")
    _merge_on_track(layout)
    setup(layout)
    rescan, calls = _fake_scan(layout)
    result = run_repo_advance(layout, "demo", now=LATER, rescan=rescan)
    assert result.refusal is not None and result.refusal.code == code
    assert calls == [] and git(_clone(layout), "rev-parse", "HEAD") == old


def test_to_is_unsupported_and_a_missing_rescan_is_a_caller_error(layout: WorkspaceLayout) -> None:
    _merge_on_track(layout)
    assert run_repo_advance(layout, "demo", to="main", now=LATER).refusal.code == "to-unsupported"  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="rescan"):
        run_repo_advance(layout, "demo", now=LATER)


@pytest.mark.parametrize("foreign", [True, False])
def test_advance_refuses_foreign_or_nested_checkout(layout: WorkspaceLayout, tmp_path: Path, foreign: bool) -> None:
    from graph_works_core.workspace.manifest import set_value

    old = git(_clone(layout), "rev-parse", "HEAD")
    _merge_on_track(layout)
    if foreign:
        target = tmp_path / "foreign"
        git(tmp_path, "clone", "-q", str(_checkout(layout)), str(target))
        git(target, "switch", "-q", "-C", "main")
    else:
        target = _checkout(layout) / "src"
    set_value(layout.manifest_path, "repositories.demo.checkout", target.as_posix())
    rescan, calls = _fake_scan(layout)
    result = run_repo_advance(layout, "demo", now=LATER, rescan=rescan)
    assert result.refusal is not None and result.refusal.code == "checkout-foreign"
    assert calls == []
    assert git(_clone(layout), "rev-parse", "HEAD") == old


def test_failed_advance_restores_real_graph_queries(layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from code_graph_io import open_reader
    from graph_works_core.graph.commands import build, graph_target
    from graph_works_core.scan.commands import run_scan

    old = git(_clone(layout), "rev-parse", "HEAD")
    assert build(graph_target(layout)).ok
    _merge_on_track(layout, "def after_advance():\n    pass\n")

    def rescan() -> Sequence[str]:
        result = asyncio.run(
            run_scan(layout, load_workspace_config(layout), today=LATER.date(), at=LATER, narrate=False, dry_run=False)
        )
        assert not result.errors
        with open_reader(graph_dir=layout.cache_dir) as reader:
            assert reader.find(name="after_advance", kind="function")
        return result.errors

    def fail_log(*_args: object, **_kwargs: object) -> str | None:
        raise OSError("disk full")

    monkeypatch.setattr(commands, "_append_log", fail_log)
    result = run_repo_advance(layout, "demo", now=LATER, rescan=rescan)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert git(_clone(layout), "rev-parse", "HEAD") == old
    with open_reader(graph_dir=layout.cache_dir) as reader:
        assert not reader.find(name="after_advance", kind="function")
        assert reader.metadata("last_indexed_commit:repo:local/demo") == old


@pytest.mark.parametrize("failure", ["bundle", "clone", "graph"])
def test_rollback_failure_is_explicit(layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    from contextlib import contextmanager

    old = git(_clone(layout), "rev-parse", "HEAD")
    _merge_on_track(layout)
    rescan, _ = _fake_scan(layout, errors=("scan failed",))

    def broken(*_args, **_kwargs):
        raise OSError(f"{failure} recovery failed")

    if failure == "bundle":
        monkeypatch.setattr(commands, "discard_bundle_changes", broken)
    elif failure == "clone":
        real_detach = commands.detach

        def detach(git_runner, clone, commit):
            if commit == old:
                return GitFailure("nonzero", "checkout", "clone recovery failed")
            return real_detach(git_runner, clone, commit)

        monkeypatch.setattr(commands, "detach", detach)
    else:

        @contextmanager
        def checkpoint(_graph_dir):
            yield broken

        monkeypatch.setattr(commands, "graph_checkpoint", checkpoint)
    result = run_repo_advance(layout, "demo", now=LATER, rescan=rescan)
    assert result.refusal is not None and result.refusal.code == "scan-failed"
    assert "rollback incomplete" in result.refusal.detail
    assert f"{failure} recovery failed" in result.refusal.detail
    if failure != "clone":
        assert git(_clone(layout), "rev-parse", "HEAD") == old
