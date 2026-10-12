"""One real-socket run found through ``serve.json``."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator

import pytest
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_serve import discovery_file as df
from watcher_helpers import write


def _until_change(lines: Iterator[str], path: str) -> None:
    deadline = time.monotonic() + 10
    for line in lines:
        assert time.monotonic() < deadline, f"stream did not deliver {path}"
        if line == "event: changes":
            data = next(lines)
            assert data.startswith("data: "), data
            payload = json.loads(data.removeprefix("data: "))
            if any(event["path"] == path for event in payload["events"]):
                return
    pytest.fail(f"stream ended without {path}")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal shutdown")
def test_gw_serve_answers_status_then_cleans_up(workspace: WorkspaceLayout) -> None:
    path = df.record_path(workspace.cache_dir)
    proc = subprocess.Popen(
        [sys.executable, "-m", "gw_serve.main", "--workspace", str(workspace.root), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "WATCHFILES_FORCE_POLLING": "true"},
    )
    try:
        deadline = time.monotonic() + 20
        record = None
        while time.monotonic() < deadline and record is None:
            record = df.read_record(path)
            time.sleep(0.05)
        assert record is not None, proc.stderr.read1().decode() if proc.stderr else ""
        request = urllib.request.Request(
            f"{record.url()}/v1/work/status", headers={"Authorization": f"Bearer {record.token}"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            body = json.loads(response.read())
        assert {"total", "by_work_status", "resume"} <= set(body)
    finally:
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=20)
    assert proc.returncode == 0
    assert not path.exists()
    assert record.token not in err.decode()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal shutdown")
def test_events_stream_and_sigint_exits_promptly(workspace: WorkspaceLayout) -> None:
    import httpx

    path = df.record_path(workspace.cache_dir)
    proc = subprocess.Popen(
        [sys.executable, "-m", "gw_serve.main", "--workspace", str(workspace.root), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "WATCHFILES_FORCE_POLLING": "true"},
    )
    try:
        deadline = time.monotonic() + 20
        record = None
        while time.monotonic() < deadline and record is None:
            record = df.read_record(path)
            time.sleep(0.05)
        assert record is not None
        with (
            httpx.Client(timeout=10) as client,
            client.stream("GET", f"{record.url()}/v1/events?token={record.token}") as response,
        ):
            assert response.status_code == 200
            lines = response.iter_lines()
            assert next(lines) == "retry: 2000"
            assert next(lines) == "event: ready"
            assert next(lines).startswith("data: ")
            # SSE ready acknowledges the subscription, not native registration.
            # Prove the watcher is delivering before the single work-item edit.
            sentinel = workspace.bundle_dir / "work" / "feature-socket-ready.md"
            stop = threading.Event()

            def pulse() -> None:
                count = 0
                while not stop.is_set():
                    count += 1
                    write(sentinel, f"---\ntitle: socket ready\n---\n\n<!-- socket ready {count} -->\n")
                    stop.wait(0.1)

            probe = threading.Thread(target=pulse)
            probe.start()
            try:
                _until_change(lines, "work/feature-socket-ready")
            finally:
                stop.set()
                probe.join(timeout=5)
                assert not probe.is_alive()
            item = workspace.bundle_dir / "work" / "feature-smoke.md"
            write(item, "---\ntitle: smoke\n---\n")
            _until_change(lines, "work/feature-smoke")
            start = time.monotonic()
            proc.send_signal(signal.SIGINT)
            for _ in lines:
                pass
        proc.wait(timeout=5)
        assert time.monotonic() - start < 5
        assert not path.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        _, stderr = proc.communicate(timeout=5)
    assert record.token not in stderr.decode()
