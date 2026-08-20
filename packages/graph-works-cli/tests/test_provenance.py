"""Provenance guard — worktree-correct `gw` routing staleness detection.

Uses real `git worktree` fixtures: the bug's own repro is a worktree sharing its parent's object
database, and the guard's central git call is only meaningful against that relationship.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROUTING_CORE = Path("packages/graph-works-core/src/graph_works_core/__init__.py")
ROUTING_WORK_TRACKER = Path("packages/work-tracker-okf/src/work_tracker_okf/__init__.py")
MARKER = Path("packages/graph-works-core/pyproject.toml")


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _seed_checkout(root: Path) -> None:
    """Lay down the minimal file shape the guard's marker walk looks for."""
    for rel in (ROUTING_CORE, ROUTING_WORK_TRACKER):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("VALUE = 1\n", encoding="utf-8")
    (root / MARKER).parent.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text("[project]\nname = 'graph-works-core'\n", encoding="utf-8")


@pytest.fixture
def checkouts(tmp_path: Path) -> tuple[Path, Path]:
    """(main_checkout, worktree) — a real git worktree pair sharing one ODB."""
    main = tmp_path / "main"
    main.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=main)
    _seed_checkout(main)
    _git(["add", "-A"], cwd=main)
    _git(["commit", "-qm", "seed"], cwd=main)
    worktree = tmp_path / "wt"
    _git(["worktree", "add", "-q", str(worktree), "-b", "feat"], cwd=main)
    return main, worktree


def _run_guard(monkeypatch, *, cwd: Path, source: Path | None) -> None:
    """Invoke the guard with the source checkout pinned and cwd relocated."""
    from graph_works_cli import provenance

    monkeypatch.setattr(provenance, "_source_checkout_root", lambda: source)
    monkeypatch.chdir(cwd)
    provenance.warn_if_stale_routing()


def test_warns_when_worktree_routing_differs(checkouts, monkeypatch, capsys) -> None:
    main, worktree = checkouts
    (worktree / ROUTING_CORE).write_text("VALUE = 2\n", encoding="utf-8")

    _run_guard(monkeypatch, cwd=worktree, source=main)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "gw is running routing code from" in captured.err
    assert str(main) in captured.err
    assert "GRAPH_WORKS_PROVENANCE_GUARD=0" in captured.err


def test_warns_when_the_editable_source_checkout_is_dirty(checkouts, monkeypatch, capsys) -> None:
    main, worktree = checkouts
    (main / ROUTING_CORE).write_text("VALUE = 2\n", encoding="utf-8")

    _run_guard(monkeypatch, cwd=worktree, source=main)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "gw is running routing code from" in captured.err


def test_silent_when_routing_sources_identical(checkouts, monkeypatch, capsys) -> None:
    main, worktree = checkouts
    _run_guard(monkeypatch, cwd=worktree, source=main)
    assert capsys.readouterr() == ("", "")


def test_silent_when_non_routing_file_differs(checkouts, monkeypatch, capsys) -> None:
    """Only the two routing source trees are compared."""
    main, worktree = checkouts
    other = worktree / "packages" / "okf-io" / "src" / "okf_io"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("CHANGED = True\n", encoding="utf-8")

    _run_guard(monkeypatch, cwd=worktree, source=main)
    assert capsys.readouterr() == ("", "")


def test_silent_when_same_checkout(checkouts, monkeypatch, capsys) -> None:
    main, _ = checkouts
    _run_guard(monkeypatch, cwd=main, source=main)
    assert capsys.readouterr() == ("", "")


def test_silent_when_kill_switch_set(checkouts, monkeypatch, capsys) -> None:
    main, worktree = checkouts
    (worktree / ROUTING_CORE).write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setenv("GRAPH_WORKS_PROVENANCE_GUARD", "0")

    _run_guard(monkeypatch, cwd=worktree, source=main)
    assert capsys.readouterr() == ("", "")


def test_silent_when_source_marker_absent(checkouts, monkeypatch, capsys) -> None:
    """A real non-editable wheel install has no packages/ marker above it."""
    _, worktree = checkouts
    (worktree / ROUTING_CORE).write_text("VALUE = 2\n", encoding="utf-8")

    _run_guard(monkeypatch, cwd=worktree, source=None)
    assert capsys.readouterr() == ("", "")


def test_silent_when_cwd_outside_any_checkout(checkouts, tmp_path, monkeypatch, capsys) -> None:
    main, _ = checkouts
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    _run_guard(monkeypatch, cwd=elsewhere, source=main)
    assert capsys.readouterr() == ("", "")


def test_silent_and_quiet_when_shas_unrelated(checkouts, tmp_path, monkeypatch, capsys) -> None:
    """Unrelated clones: the sha will not resolve (git exits 128 with `fatal:`).

    Treated as skip, and git's own stderr must not leak through.
    """
    main, _ = checkouts
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=unrelated)
    _seed_checkout(unrelated)
    _git(["add", "-A"], cwd=unrelated)
    _git(["commit", "-qm", "seed"], cwd=unrelated)

    _run_guard(monkeypatch, cwd=unrelated, source=main)

    captured = capsys.readouterr()
    assert captured == ("", "")
    assert "fatal" not in captured.err


def test_silent_when_git_unavailable(checkouts, monkeypatch, capsys) -> None:
    from graph_works_cli import provenance

    main, worktree = checkouts
    (worktree / ROUTING_CORE).write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setattr(provenance, "_git", lambda args, cwd: None)

    _run_guard(monkeypatch, cwd=worktree, source=main)
    assert capsys.readouterr() == ("", "")


def test_git_returns_none_when_subprocess_cannot_start(monkeypatch, tmp_path: Path) -> None:
    from graph_works_cli import provenance

    def raise_os_error(*args, **kwargs):
        raise OSError("git is unavailable")

    monkeypatch.setattr(provenance.subprocess, "run", raise_os_error)

    assert provenance._git(["status"], tmp_path) is None


def test_source_checkout_root_resolves_from_running_core() -> None:
    """The real resolver finds this checkout's root from the genuinely-installed `graph_works_core`."""
    import graph_works_core
    from graph_works_cli import provenance

    root = provenance._source_checkout_root()

    assert root is not None
    assert (root / MARKER).is_file()
    assert str(Path(graph_works_core.__file__).resolve()).startswith(str(root))


def test_silent_when_git_cannot_produce_a_trustworthy_diff(checkouts, monkeypatch, capsys) -> None:
    """The guard is advisory: an untrustworthy git result must never produce a false stale warning."""
    from graph_works_cli import provenance

    main, worktree = checkouts
    real_git = provenance._git

    def flaky_git(args: list[str], *, cwd: Path) -> object:
        if args and args[0] == "diff":
            return subprocess.CompletedProcess(args, returncode=128, stdout="", stderr="fatal: bad object")
        return real_git(args, cwd=cwd)

    monkeypatch.setattr(provenance, "_git", flaky_git)
    (worktree / ROUTING_CORE).write_text("VALUE = 2\n", encoding="utf-8")

    _run_guard(monkeypatch, cwd=worktree, source=main)

    assert capsys.readouterr().err == ""
