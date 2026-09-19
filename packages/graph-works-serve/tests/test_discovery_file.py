from __future__ import annotations

import dataclasses
import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from graph_works_serve import discovery_file as df

REC = df.ServeRecord(
    pid=os.getpid(),
    host="127.0.0.1",
    port=1,
    token="tok",
    gw_version="0.3.4",
    workspace="/ws",
    started_at="2026-09-18T00:00:00+00:00",
)


def test_round_trip_and_schema(tmp_path: Path) -> None:
    """A valid record survives atomic persistence with its version marker."""
    path = df.record_path(tmp_path / "cache")
    df.write_record(path, REC)
    assert path.name == "serve.json"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert df.read_record(path) == REC


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_mode_is_0600(tmp_path: Path) -> None:
    """A discovery record does not expose its bearer token to other users."""
    path = df.record_path(tmp_path)
    df.write_record(path, REC)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_replaces_atomically_leaving_no_temp(tmp_path: Path) -> None:
    """Replacing an existing record leaves clients only the final record."""
    path = df.record_path(tmp_path)
    df.write_record(path, REC)
    df.write_record(path, dataclasses.replace(REC, port=2))
    assert df.read_record(path) == dataclasses.replace(REC, port=2)
    assert sorted(item.name for item in tmp_path.iterdir()) == [".serve.json.lock", "serve.json"]


def test_failed_replace_removes_temporary_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed atomic replacement does not leave a token-bearing temp file behind."""
    path = df.record_path(tmp_path)

    def fail_replace(source: str, destination: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(df.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        df.write_record(path, REC)
    assert sorted(item.name for item in tmp_path.iterdir()) == [".serve.json.lock"]


def test_failed_temp_hardening_closes_descriptor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure before ``fdopen`` closes the descriptor before removing the temporary file."""
    path = df.record_path(tmp_path)
    original_mkstemp = df.tempfile.mkstemp
    original_close = df.os.close
    descriptor: int | None = None
    closed: set[int] = set()

    def track_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        nonlocal descriptor
        descriptor, temporary = original_mkstemp(*args, **kwargs)
        return descriptor, temporary

    def track_close(candidate: int) -> None:
        closed.add(candidate)
        original_close(candidate)

    def fail_chmod(_: str, __: int) -> None:
        raise OSError("chmod failed")

    monkeypatch.setattr(df.tempfile, "mkstemp", track_mkstemp)
    monkeypatch.setattr(df.os, "close", track_close)
    monkeypatch.setattr(df.os, "chmod", fail_chmod)
    with pytest.raises(OSError, match="chmod failed"):
        df.write_record(path, REC)
    assert descriptor in closed
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "{}",
        '{"schema_version": 2}',
        '{"schema_version": 1, "pid": "x"}',
        (
            '{"schema_version": 1, "pid": 0, "host": "127.0.0.1", "port": 1, '
            '"token": "tok", "gw_version": "0.3.4", "workspace": "/ws", "started_at": "now"}'
        ),
        (
            '{"schema_version": 1, "pid": 1, "host": "example.com", "port": 1, '
            '"token": "tok", "gw_version": "0.3.4", "workspace": "/ws", "started_at": "now"}'
        ),
        (
            '{"schema_version": 1, "pid": 1, "host": "127.0.0.1", "port": 0, '
            '"token": "tok", "gw_version": "0.3.4", "workspace": "/ws", "started_at": "now"}'
        ),
    ],
)
def test_malformed_reads_as_none(tmp_path: Path, text: str) -> None:
    """Malformed data is stale, so it cannot probe arbitrary processes or hosts."""
    path = df.record_path(tmp_path)
    path.write_text(text, encoding="utf-8", newline="\n")
    assert df.read_record(path) is None


def test_missing_reads_as_none(tmp_path: Path) -> None:
    """No discovery file means no instance is discoverable."""
    assert df.read_record(df.record_path(tmp_path)) is None


def test_pid_alive() -> None:
    """The liveness probe recognizes this process and a definitely absent PID."""
    assert df.pid_alive(os.getpid()) is True
    assert df.pid_alive(2**22 + 12345) is False


def test_live_instance_matrix(tmp_path: Path) -> None:
    """A record is live only when it names both a running and healthy sidecar."""
    path = df.record_path(tmp_path)
    assert df.live_instance(path) is None
    df.write_record(path, REC)
    assert df.live_instance(path, alive=lambda _: False, healthy=lambda _: True) is None
    assert df.live_instance(path, alive=lambda _: True, healthy=lambda _: False) is None
    assert df.live_instance(path, alive=lambda _: True, healthy=lambda _: True) == REC


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fcntl locking")
def test_instance_claim_refuses_a_second_launcher_before_health_is_ready(tmp_path: Path) -> None:
    """The lifetime claim, rather than HTTP readiness, owns a launch slot."""
    path = df.record_path(tmp_path)
    with df.claim_instance(path, alive=lambda _: True, healthy=lambda _: False) as first:
        assert first.acquired is True and first.running is None
        with df.claim_instance(path, alive=lambda _: True, healthy=lambda _: False) as second:
            assert second.acquired is False and second.running is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fcntl locking")
def test_instance_claim_releases_after_launcher_failure(tmp_path: Path) -> None:
    """A failed launch cannot leave the workspace permanently unavailable."""
    path = df.record_path(tmp_path)
    with pytest.raises(RuntimeError, match="bind failed"), df.claim_instance(path):
        raise RuntimeError("bind failed")
    with df.claim_instance(path) as retry:
        assert retry.acquired is True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fcntl locking")
def test_instance_claim_reclaims_record_with_unrepresentable_pid(tmp_path: Path) -> None:
    """A stale record with an oversized PID does not block a new launcher."""
    path = df.record_path(tmp_path)
    df.write_record(path, dataclasses.replace(REC, pid=2**63))

    with df.claim_instance(path, healthy=lambda _: False) as claim:
        assert claim.acquired is True
        assert claim.running is None


def test_remove_only_when_owned(tmp_path: Path) -> None:
    """A process can remove only the discovery file naming its own PID."""
    path = df.record_path(tmp_path)
    df.write_record(path, REC)
    assert df.remove_if_owned(path, REC.pid + 1) is False and path.exists()
    assert df.remove_if_owned(path, REC.pid) is True and not path.exists()
    assert df.remove_if_owned(path, REC.pid) is False


def test_remove_missing_parent_returns_false_without_creating_it(tmp_path: Path) -> None:
    """Cleanup of an absent cache is idempotent and does not create it."""
    path = df.record_path(tmp_path / "missing-cache")
    assert df.remove_if_owned(path, REC.pid) is False
    assert not path.parent.exists()


def test_remove_returns_false_if_parent_disappears_before_lock_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup tolerates the cache disappearing before its lock can open."""
    path = df.record_path(tmp_path / "vanishing-cache")
    path.parent.mkdir()
    original_open = df.os.open

    def remove_parent_before_open(candidate: str, flags: int, mode: int = 0o777) -> int:
        path.parent.rmdir()
        return original_open(candidate, flags, mode)

    monkeypatch.setattr(df.os, "open", remove_parent_before_open)
    assert df.remove_if_owned(path, REC.pid) is False
    assert not path.parent.exists()


def test_remove_cannot_delete_a_successor_written_concurrently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cleanup holds the record lock until it has both checked and unlinked."""
    path = df.record_path(tmp_path)
    successor = dataclasses.replace(REC, port=2)
    df.write_record(path, REC)
    original_read_record = df.read_record
    remover_read = threading.Event()
    allow_removal = threading.Event()
    writer_finished = threading.Event()

    def pause_after_read(candidate: Path) -> df.ServeRecord | None:
        record = original_read_record(candidate)
        remover_read.set()
        assert allow_removal.wait(timeout=1)
        return record

    monkeypatch.setattr(df, "read_record", pause_after_read)
    remover = threading.Thread(target=df.remove_if_owned, args=(path, REC.pid))
    writer = threading.Thread(target=lambda: (df.write_record(path, successor), writer_finished.set()))
    remover.start()
    assert remover_read.wait(timeout=1)
    writer.start()
    writer_was_blocked = not writer_finished.wait(timeout=0.1)
    try:
        assert writer_was_blocked
    finally:
        allow_removal.set()
        remover.join(timeout=1)
        writer.join(timeout=1)
    assert not remover.is_alive()
    assert not writer.is_alive()
    assert original_read_record(path) == successor


def test_health_ok_against_a_real_socket() -> None:
    """Health checks send the record token and reject an unavailable listener."""
    seen: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen["auth"] = self.headers.get("Authorization", "")
            self.send_response(200 if self.path == "/v1/health" else 404)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    record = dataclasses.replace(REC, port=server.server_port)
    assert df.health_ok(record) is True
    assert seen["auth"] == "Bearer tok"
    server.server_close()
    assert df.health_ok(record, timeout=0.2) is False
