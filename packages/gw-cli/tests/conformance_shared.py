"""Run an expensive conformance driver once per pytest session, across xdist workers.

A `session` fixture is once per xdist worker, so a narrow selection re-ran each driver on
every worker that drew a consumer. The first worker to claim a run builds it and publishes
a pickle; the others wait for that pickle (or for the winner's traceback).
"""

from __future__ import annotations

import os
import pickle
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any


def shared_run(
    name: str,
    directory: Path,
    run: Callable[[], Any],
    *,
    poll: float = 0.25,
    timeout: float = 600.0,
) -> Any:
    """Return `run()`'s result, computing it in exactly one caller per *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    claim = directory / f"{name}.claim"
    done = directory / f"{name}.pickle"
    failed = directory / f"{name}.failed"
    try:
        os.close(os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return _await(name, claim, done, failed, poll, timeout)
    try:
        result = run()
    except BaseException:
        _publish(failed, traceback.format_exc().encode("utf-8"))
        raise
    _publish(done, pickle.dumps(result))
    return result


def _publish(target: Path, payload: bytes) -> None:
    staging = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    staging.write_bytes(payload)
    staging.replace(target)


def _await(name: str, claim: Path, done: Path, failed: Path, poll: float, timeout: float) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        if done.exists():
            return pickle.loads(done.read_bytes())
        if failed.exists():
            detail = failed.read_text(encoding="utf-8")
            raise RuntimeError(f"conformance run {name!r} failed in another worker:\n{detail}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"conformance run {name!r} produced no result in {timeout}s; claim file: {claim}")
        time.sleep(poll)
