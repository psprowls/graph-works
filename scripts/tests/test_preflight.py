"""scripts/preflight.sh under stub executables, plus a no-bare-python3 tripwire."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "preflight.sh"
PREFIX = "TOOLCHAIN PREFLIGHT FAILED (host, not code): "


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _run(tmp_path: Path, *, git: str | None, uv: str | None) -> subprocess.CompletedProcess[str]:
    stubs = tmp_path / "bin"
    stubs.mkdir()
    if git is not None:
        _stub(stubs, "git", git)
    if uv is not None:
        _stub(stubs, "uv", uv)
    # Only the stubs plus the bare essentials bash needs; never the host's git or uv.
    env = {"PATH": f"{stubs}:/usr/bin:/bin"}
    for tool in ("git", "uv"):
        host = subprocess.run(
            ["/bin/sh", "-c", f"command -v {tool}"], capture_output=True, text=True, env=env, check=False
        )
        if host.stdout.strip() and not host.stdout.strip().startswith(str(stubs)):
            # a host copy sits on /usr/bin:/bin; shadow it with a failing stub when unwanted
            if (tool == "git" and git is None) or (tool == "uv" and uv is None):
                _stub(stubs, tool, "exit 127")
    return subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, check=False, cwd=ROOT
    )


UV_OK = 'exit 0'
UV_OLD = 'case "$*" in *"print("*) echo 3.9;; *) exit 1;; esac'


def test_git_exit_69(tmp_path: Path) -> None:
    result = _run(tmp_path, git="exit 69", uv=UV_OK)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        PREFIX + "git --version exited 69 — accept the Xcode licence (`sudo xcodebuild -license`) "
        "or put a working git (e.g. `/opt/homebrew/bin`) first on PATH\n"
    )


def test_git_other_failure(tmp_path: Path) -> None:
    result = _run(tmp_path, git="exit 2", uv=UV_OK)
    assert result.returncode == 1
    assert result.stderr == PREFIX + "git --version — `git --version` failed with exit 2\n"


def test_uv_absent(tmp_path: Path) -> None:
    result = _run(tmp_path, git="exit 0", uv=None)
    assert result.returncode == 1
    assert result.stderr == PREFIX + "uv not found — install uv and put it on PATH\n"


def test_old_python(tmp_path: Path) -> None:
    result = _run(tmp_path, git="exit 0", uv=UV_OLD)
    assert result.returncode == 1
    assert result.stderr == (
        PREFIX + "python interpreter — uv resolved Python 3.9; this workspace requires ≥3.12\n"
    )


def test_all_green(tmp_path: Path) -> None:
    result = _run(tmp_path, git="exit 0", uv=UV_OK)
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


BARE = re.compile(r"(?<![\w./-])python3(?![\w.-])")


def _bare_hits(path: Path) -> list[str]:
    hits = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("#!"):
            continue
        if BARE.search(line):
            hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    return hits


def test_no_bare_python3_in_justfile_or_auto_drive() -> None:
    files = [ROOT / "justfile"] + [
        p
        for p in (ROOT / "plugins" / "gw" / "skills" / "auto-drive").rglob("*")
        if p.is_file() and p.suffix in {".md", ".sh", ".py"}
    ]
    hits = [hit for f in files for hit in _bare_hits(f)]
    assert hits == []


@pytest.mark.skipif(os.name == "nt", reason="bash script")
def test_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)
