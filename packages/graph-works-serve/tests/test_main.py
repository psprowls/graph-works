"""Tests for the ``gw-serve`` launch boundary."""

from __future__ import annotations

import json
import multiprocessing
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import code_graph_io.exit_codes as codes
import pytest
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import discovery_file as df
from graph_works_serve import main as serve_main


def _launch_without_http_until_released(workspace: str, started: object, release: object, result: object) -> None:
    """Hold a real launcher after its record write but before HTTP readiness."""

    def wait_for_release(_app: object, _sock: socket.socket) -> None:
        assert isinstance(started, multiprocessing.synchronize.Event)
        assert isinstance(release, multiprocessing.synchronize.Event)
        started.set()
        assert release.wait(timeout=10)

    assert isinstance(result, multiprocessing.queues.Queue)
    result.put(serve_main.main(["--workspace", workspace], serve_fn=wait_for_release))


def test_exit_codes_mirror_the_cli() -> None:
    assert serve_main.NOT_INITIALIZED == codes.NOT_INITIALIZED


def test_describe_needs_no_workspace(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GRAPH_WORKS_DIR", raising=False)
    assert serve_main.main(["--describe"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["server"] == "graph-works-serve" and doc["version"] == serve_main.version()


def test_no_workspace_exits_not_initialized_before_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GRAPH_WORKS_DIR", raising=False)
    called = []
    assert serve_main.main([], serve_fn=lambda app, sock: called.append(1)) == 3
    assert called == [] and "Error" in capsys.readouterr().err


def test_launch_writes_serve_json_then_removes_it(
    workspace: WorkspaceLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    path = df.record_path(workspace.cache_dir)
    record_seen: df.ServeRecord | None = None
    port_seen: int | None = None

    def fake_serve(_app: object, sock: socket.socket) -> None:
        nonlocal port_seen, record_seen
        record = df.read_record(path)
        assert record is not None
        record_seen = record
        port_seen = sock.getsockname()[1]

    assert serve_main.main(["--workspace", str(workspace.root)], serve_fn=fake_serve) == 0
    assert record_seen is not None and port_seen is not None
    assert record_seen.pid == os.getpid() and record_seen.port == port_seen and record_seen.host == "127.0.0.1"
    assert record_seen.workspace == str(workspace.root) and len(record_seen.token) >= 32
    assert not path.exists()
    err = capsys.readouterr().err
    assert record_seen.token not in err and "listening on" in err


def test_a_live_instance_refuses_a_second_launch(
    workspace: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    live = df.ServeRecord(pid=1, host="127.0.0.1", port=9, token="x", gw_version="v", workspace="w", started_at="s")

    @contextmanager
    def already_claimed(_path: Path) -> Iterator[df.InstanceClaim]:
        yield df.InstanceClaim(acquired=False, running=live)

    monkeypatch.setattr(serve_main, "claim_instance", already_claimed)
    assert serve_main.main(["--workspace", str(workspace.root)], serve_fn=lambda a, s: None) == 1
    err = capsys.readouterr().err
    assert "already running" in err and "http://127.0.0.1:9" in err


@pytest.mark.skipif(os.name == "nt", reason="POSIX fcntl locking")
def test_simultaneous_launch_refuses_before_first_health_readiness(
    workspace: WorkspaceLayout, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second process cannot replace a first sidecar's pre-health record."""
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    release = context.Event()
    result = context.Queue()
    first = context.Process(
        target=_launch_without_http_until_released, args=(str(workspace.root), started, release, result)
    )
    first.start()
    try:
        assert started.wait(timeout=10)
        assert serve_main.main(["--workspace", str(workspace.root)], serve_fn=lambda _app, _sock: None) == 1
        assert "already running" in capsys.readouterr().err
    finally:
        release.set()
        first.join(timeout=10)
        if first.is_alive():
            first.terminate()
            first.join(timeout=10)
    assert first.exitcode == 0
    assert result.get(timeout=1) == 0


def test_a_stale_file_is_overwritten(workspace: WorkspaceLayout) -> None:
    path = df.record_path(workspace.cache_dir)
    df.write_record(
        path,
        df.ServeRecord(
            pid=2**22 + 7, host="127.0.0.1", port=9, token="old", gw_version="v", workspace="w", started_at="s"
        ),
    )
    tokens = []
    serve_main.main(
        ["--workspace", str(workspace.root)],
        serve_fn=lambda _app, _sock: tokens.append(cast(df.ServeRecord, df.read_record(path)).token),
    )
    assert tokens and tokens[0] != "old"


def test_another_pids_file_survives_exit(workspace: WorkspaceLayout) -> None:
    path = df.record_path(workspace.cache_dir)

    def hijack(_app: object, _sock: socket.socket) -> None:
        df.write_record(
            path,
            df.ServeRecord(
                pid=os.getpid() + 1, host="127.0.0.1", port=9, token="t", gw_version="v", workspace="w", started_at="s"
            ),
        )

    serve_main.main(["--workspace", str(workspace.root)], serve_fn=hijack)
    assert path.exists()
