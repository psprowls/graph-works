"""Cross-process contention and in-place reset contracts for the read index."""

from __future__ import annotations

import multiprocessing
import sqlite3
import time
from multiprocessing.process import BaseProcess
from pathlib import Path

from ext_helpers import write
from okf_ext.readindex import IndexBusy, open_index, read, reconcile
from readindex_helpers import age, assert_equivalent, tree


def _reconcile_loop(db: str, root: str, rounds: int) -> None:
    for _ in range(rounds):
        try:
            with open_index(Path(db), Path(root), busy_timeout_ms=2000) as index:
                reconcile(index)
        except IndexBusy:
            pass  # Contention is allowed; a final quiescent reconcile must converge.


def _rewrite_loop(root: str, rounds: int) -> None:
    for n in range(rounds):
        for i in range(10):
            write(Path(root) / f"p{i}.md", f"---\ntitle: v{n}\n---\n[x](p{(i + 1) % 10}.md)\n")


def _busy_probe(db: str, root: str) -> None:
    try:
        with open_index(Path(db), Path(root), busy_timeout_ms=50) as index:
            reconcile(index)
    except IndexBusy:
        return
    raise AssertionError("held cross-process write lock did not raise IndexBusy")


def _run_bounded(workers: list[BaseProcess]) -> None:
    """Bound the whole group and reap children even after a failure or timeout."""
    started: list[BaseProcess] = []
    deadline = time.monotonic() + 90
    try:
        for worker in workers:
            worker.start()
            started.append(worker)
        for worker in started:
            worker.join(timeout=max(0, deadline - time.monotonic()))
            assert not worker.is_alive(), f"{worker.name} exceeded the 90-second group deadline"
            assert worker.exitcode == 0, f"{worker.name} failed with exit code {worker.exitcode}"
    finally:
        for worker in started:
            if worker.is_alive():
                worker.terminate()
        for worker in started:
            worker.join(timeout=5)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=5)
            assert not worker.is_alive(), f"could not reap {worker.name}"
            worker.close()


def test_two_reconcilers_and_a_writer_converge(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {f"p{i}.md": "x" for i in range(10)})
    db = tmp_path / "i.db"
    with open_index(db, root) as index:
        reconcile(index)
    ctx = multiprocessing.get_context("spawn")
    _run_bounded(
        [
            ctx.Process(target=_reconcile_loop, args=(str(db), str(root), 20), name="reconciler-a"),
            ctx.Process(target=_reconcile_loop, args=(str(db), str(root), 20), name="reconciler-b"),
            ctx.Process(target=_rewrite_loop, args=(str(root), 20), name="bundle-writer"),
        ]
    )
    # Quiescent convergence must not be forced by changing every stored stat.
    assert_equivalent(root, db)


def test_held_write_lock_raises_busy_in_another_process(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {"a.md": "x"})
    db = tmp_path / "i.db"
    open_index(db, root).close()
    blocker = sqlite3.connect(db, isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        ctx = multiprocessing.get_context("spawn")
        _run_bounded([ctx.Process(target=_busy_probe, args=(str(db), str(root)), name="busy-probe")])
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert_equivalent(root, db)


def test_version_reset_while_other_handle_open(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {"a.md": "---\ntitle: Before\n---\n"})
    db = tmp_path / "i.db"
    with open_index(db, root, fingerprint="one") as holder:
        reconcile(holder)
        write(root / "a.md", "---\ntitle: After\n---\n")
        age(root)
        with open_index(db, root, fingerprint="two") as other:
            assert other.rebuilt_reason == "fingerprint"
            reconcile(other)
        assert db.exists()
        with read(holder) as view:
            member = view.member("a.md")
            assert member is not None and member.title == "After"
