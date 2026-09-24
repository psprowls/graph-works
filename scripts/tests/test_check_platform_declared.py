"""Acceptance tests for `scripts/check_platform_declared.py`.

Outside the repo's `testpaths` and coverage `source` list on purpose:
`scripts/` is repo tooling, not a package, so these do not move the 95% gate.
Run them with `uv run pytest scripts/tests`.

The gate is rule 3a made mechanical -- see the design spec for
work/epic-native-windows-support/children/tech-debt-publish-platform-matrix.
A package that imports a POSIX-only module, or reaches a POSIX-only process
primitive, anywhere in its shipped source must declare that in its README's
`## Platform` section (or, for a package on the named alternate-heading
allowlist, its existing boundary section). The gate is deliberately
asymmetric: it fails under-declaration only, never over-declaration.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_platform_declared import find_violations, main  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _package(root: Path, name: str) -> Path:
    pkg = root / "packages" / name
    (pkg / "src").mkdir(parents=True, exist_ok=True)
    return pkg


def test_an_unconditional_fcntl_import_with_no_platform_section_is_reported(
    tmp_path: Path,
) -> None:
    pkg = _package(tmp_path, "acme-locker")
    _write(pkg / "src" / "acme_locker" / "lock.py", "import fcntl\n")
    _write(pkg / "README.md", "# acme-locker\n\nA package.\n")

    violations = find_violations(tmp_path)

    assert len(violations) == 1
    v = violations[0]
    assert v.package == "acme-locker"
    assert v.construct == "fcntl"
    assert v.file == "packages/acme-locker/src/acme_locker/lock.py"
    assert v.line == 1


def test_the_same_package_is_clean_once_it_declares_a_platform_section(
    tmp_path: Path,
) -> None:
    pkg = _package(tmp_path, "acme-locker")
    _write(pkg / "src" / "acme_locker" / "lock.py", "import fcntl\n")
    _write(
        pkg / "README.md",
        "# acme-locker\n\n## Platform\n\nPOSIX only, see `gw util platform`.\n",
    )

    assert find_violations(tmp_path) == []


def test_a_package_with_no_posix_construct_is_never_flagged_even_with_no_readme(
    tmp_path: Path,
) -> None:
    pkg = _package(tmp_path, "acme-pure")
    _write(pkg / "src" / "acme_pure" / "core.py", "def add(a, b):\n    return a + b\n")

    assert find_violations(tmp_path) == []


def test_a_platform_section_with_no_posix_construct_is_not_a_failure(
    tmp_path: Path,
) -> None:
    """The gate is asymmetric -- over-declaring is never penalized."""
    pkg = _package(tmp_path, "acme-pure")
    _write(pkg / "src" / "acme_pure" / "core.py", "def add(a, b):\n    return a + b\n")
    _write(
        pkg / "README.md",
        "# acme-pure\n\n## Platform\n\nRuns unmodified on Windows.\n",
    )

    assert find_violations(tmp_path) == []


def test_os_kill_attribute_access_is_detected_without_a_platform_section(
    tmp_path: Path,
) -> None:
    pkg = _package(tmp_path, "acme-reaper")
    _write(
        pkg / "src" / "acme_reaper" / "backend.py",
        "import os\n\n\ndef probe(pid):\n    os.kill(pid, 0)\n",
    )
    _write(pkg / "README.md", "# acme-reaper\n")

    violations = find_violations(tmp_path)

    assert len(violations) == 1
    assert violations[0].construct == "os.kill"
    assert violations[0].line == 5


def test_an_import_nested_inside_a_function_is_still_detected(tmp_path: Path) -> None:
    pkg = _package(tmp_path, "acme-locker")
    _write(
        pkg / "src" / "acme_locker" / "lock.py",
        "def take_lock():\n    import fcntl\n    return fcntl\n",
    )
    _write(pkg / "README.md", "# acme-locker\n")

    violations = find_violations(tmp_path)

    assert len(violations) == 1
    assert violations[0].line == 2


def test_the_named_alternate_boundary_heading_satisfies_the_gate(tmp_path: Path) -> None:
    """`workflow-local` is the one named carve-out: its boundary lives under
    `## What this package does not do`, not `## Platform` -- see the design
    doc's "Two carve-outs" section."""
    pkg = _package(tmp_path, "workflow-local")
    _write(
        pkg / "src" / "workflow_local" / "backend.py",
        "import os\n\n\ndef stop(pid):\n    os.kill(pid, 0)\n",
    )
    _write(
        pkg / "README.md",
        "# workflow-local\n\n## What this package does not do\n\n- **It refuses to run on Windows.** See `os.kill`.\n",
    )

    assert find_violations(tmp_path) == []


def test_the_alternate_heading_without_an_explicit_windows_statement_still_fails(
    tmp_path: Path,
) -> None:
    pkg = _package(tmp_path, "workflow-local")
    _write(
        pkg / "src" / "workflow_local" / "backend.py",
        "import os\n\n\ndef stop(pid):\n    os.kill(pid, 0)\n",
    )
    _write(
        pkg / "README.md",
        "# workflow-local\n\n## What this package does not do\n\n- It does not provision worktrees.\n",
    )

    violations = find_violations(tmp_path)

    assert len(violations) == 1
    assert violations[0].package == "workflow-local"


def test_a_package_with_no_src_directory_is_skipped(tmp_path: Path) -> None:
    pkg = tmp_path / "packages" / "acme-empty"
    pkg.mkdir(parents=True)
    _write(pkg / "README.md", "# acme-empty\n")

    assert find_violations(tmp_path) == []


def test_main_exits_nonzero_and_names_package_construct_file_and_line(tmp_path: Path, capsys) -> None:
    pkg = _package(tmp_path, "acme-locker")
    _write(pkg / "src" / "acme_locker" / "lock.py", "import fcntl\n")
    _write(pkg / "README.md", "# acme-locker\n")

    assert main([str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "acme-locker" in err
    assert "fcntl" in err
    assert "packages/acme-locker/src/acme_locker/lock.py:1" in err
    assert "## Platform" in err


def test_main_exits_zero_on_a_clean_tree(tmp_path: Path) -> None:
    pkg = _package(tmp_path, "acme-pure")
    _write(pkg / "src" / "acme_pure" / "core.py", "def add(a, b):\n    return a + b\n")

    assert main([str(tmp_path)]) == 0
