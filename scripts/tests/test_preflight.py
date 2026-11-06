"""scripts/preflight.sh under stub executables, plus a no-bare-python3 tripwire."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from package_units import SHARED_INPUTS

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "preflight.sh"
PREFIX = "TOOLCHAIN PREFLIGHT FAILED (host, not code): "
PIN = (ROOT / ".python-version").read_text(encoding="utf-8").strip()


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _run(
    tmp_path: Path, *, git: str | None, uv: str | None, script: Path = SCRIPT, cwd: Path = ROOT
) -> subprocess.CompletedProcess[str]:
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
        ["bash", str(script)], capture_output=True, text=True, env=env, check=False, cwd=cwd
    )


def _checkout(tmp_path: Path, pin: bytes | None) -> Path:
    """A scratch repository root holding the real script and the given pin; returns the script."""
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "preflight.sh"
    shutil.copyfile(SCRIPT, script)
    if pin is not None:
        (tmp_path / "checkout" / ".python-version").write_bytes(pin)
    return script


UV_OK = f"echo {PIN}"
UV_OLD = "echo 3.9"
UV_NEWER = "echo 3.14"
UV_BROKEN = "exit 1"


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
        PREFIX + f"python interpreter — uv resolved Python 3.9; .python-version pins {PIN}\n"
    )


def test_newer_python_than_the_pin(tmp_path: Path) -> None:
    result = _run(tmp_path, git="exit 0", uv=UV_NEWER)
    assert result.returncode == 1
    assert result.stderr == (
        PREFIX + f"python interpreter — uv resolved Python 3.14; .python-version pins {PIN}\n"
    )


def test_uv_cannot_resolve_python(tmp_path: Path) -> None:
    result = _run(tmp_path, git="exit 0", uv=UV_BROKEN)
    assert result.returncode == 1
    assert result.stderr == (
        PREFIX + f"python interpreter — uv resolved Python unknown; .python-version pins {PIN}\n"
    )


def test_pin_is_read_from_the_repository_root_not_the_cwd(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".python-version").write_text("3.99\n", encoding="utf-8", newline="\n")
    script = _checkout(tmp_path, b"3.13\n")
    result = _run(tmp_path, git="exit 0", uv="echo 3.13", script=script, cwd=elsewhere)
    assert (result.returncode, result.stderr) == (0, "")


@pytest.mark.parametrize("pin", [b"3.13\r\n", b"  3.13  \n\n", b"3.13"])
def test_pin_whitespace_and_cr_are_stripped(tmp_path: Path, pin: bytes) -> None:
    script = _checkout(tmp_path, pin)
    result = _run(tmp_path, git="exit 0", uv="echo 3.13", script=script)
    assert (result.returncode, result.stderr) == (0, "")


def test_cr_in_the_resolved_version_is_stripped(tmp_path: Path) -> None:
    # Python on Windows writes CRLF to a text stdout; `$(...)` strips only the LF.
    script = _checkout(tmp_path, b"3.13\n")
    result = _run(tmp_path, git="exit 0", uv="printf '3.13\\r\\n'", script=script)
    assert (result.returncode, result.stderr) == (0, "")


def test_patch_shaped_pin_fails_rather_than_truncating(tmp_path: Path) -> None:
    script = _checkout(tmp_path, b"3.13.1\n")
    result = _run(tmp_path, git="exit 0", uv="echo 3.13", script=script)
    assert result.returncode == 1
    assert result.stderr == (
        PREFIX + "python interpreter — uv resolved Python 3.13; .python-version pins 3.13.1\n"
    )


@pytest.mark.parametrize("pin", [None, b"", b"\n"], ids=["missing", "empty", "blank"])
def test_missing_or_empty_pin_fails(tmp_path: Path, pin: bytes | None) -> None:
    script = _checkout(tmp_path, pin)
    result = _run(tmp_path, git="exit 0", uv="echo 3.13", script=script)
    assert result.returncode == 1
    assert result.stderr.count("\n") == 1
    assert result.stderr.startswith(PREFIX + "python interpreter — ")
    assert ".python-version" in result.stderr
    assert result.stdout == ""


def test_committed_pin_is_a_bare_minor() -> None:
    assert re.fullmatch(r"3\.\d+", PIN)


def test_pin_is_a_gate_shared_input() -> None:
    assert ".python-version" in SHARED_INPUTS


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
