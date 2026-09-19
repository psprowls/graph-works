"""One real-socket run found through ``serve.json``."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
import urllib.request

import pytest
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve import discovery_file as df


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal shutdown")
def test_gw_serve_answers_status_then_cleans_up(workspace: WorkspaceLayout) -> None:
    path = df.record_path(workspace.cache_dir)
    proc = subprocess.Popen(
        [sys.executable, "-m", "graph_works_serve.main", "--workspace", str(workspace.root), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
        [sys.executable, "-m", "graph_works_serve.main", "--workspace", str(workspace.root), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
            item = workspace.bundle_dir / "work" / "feature-smoke.md"
            item.write_text("---\ntitle: smoke\n---\n", encoding="utf-8", newline="\n")
            for line in lines:
                if line == "event: changes":
                    assert "work/feature-smoke" in next(lines)
                    break
            else:
                pytest.fail("stream ended without a change")
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
