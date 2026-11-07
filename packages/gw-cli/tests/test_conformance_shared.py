"""The shared-run protocol: one winner runs, everyone else reads its result or its failure."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from conformance_shared import shared_run


def test_second_caller_gets_the_pickled_result_without_running(tmp_path: Path) -> None:
    calls: list[int] = []

    def run() -> dict[str, int]:
        calls.append(1)
        return {"n": 1}

    assert shared_run("r", tmp_path, run, poll=0.01, timeout=5) == {"n": 1}
    assert shared_run("r", tmp_path, run, poll=0.01, timeout=5) == {"n": 1}
    assert len(calls) == 1


def test_concurrent_callers_run_the_function_once(tmp_path: Path) -> None:
    calls: list[int] = []
    release = threading.Event()
    results: list[object] = []

    def run() -> str:
        calls.append(1)
        release.wait(5)
        return "ok"

    threads = [
        threading.Thread(target=lambda: results.append(shared_run("r", tmp_path, run, poll=0.01, timeout=5)))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    release.set()
    for t in threads:
        t.join(10)
    assert results == ["ok"] * 4
    assert len(calls) == 1


def test_a_failed_run_reaches_the_waiter_with_the_winners_traceback(tmp_path: Path) -> None:
    def boom() -> None:
        raise ValueError("driver exploded")

    with pytest.raises(ValueError, match="driver exploded"):
        shared_run("r", tmp_path, boom, poll=0.01, timeout=5)
    with pytest.raises(RuntimeError, match="driver exploded"):
        shared_run("r", tmp_path, boom, poll=0.01, timeout=5)


def test_a_claim_with_no_result_times_out_naming_the_claim_file(tmp_path: Path) -> None:
    (tmp_path / "r.claim").touch()
    with pytest.raises(TimeoutError, match=r"r\.claim"):
        shared_run("r", tmp_path, lambda: None, poll=0.01, timeout=0.05)
