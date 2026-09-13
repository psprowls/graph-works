"""Acceptance tests for `scripts/check_line_endings.py`.

Outside the repo's `testpaths` and coverage `source` list on purpose:
`scripts/` is repo tooling, not a package, so these do not move the 95% gate.
Run them with `uv run pytest scripts/tests`.

The gate exists to catch what the now-deleted vendored subtree's own
`.gitattributes` enumeration approach could not: a tracked path outside its
five globs checks out
CRLF under Git for Windows' `core.autocrlf=true` default -- see the design
spec for `work/epic-native-windows-support/children/bug-enforce-lf-line-endings`.
Two assertions, in both directions:

1. no un-guarded CRLF (a tracked path without `-text` that is `i/crlf` or
   `i/mixed`), and
2. no un-declared `-text` (a `-text` path outside the allowlisted byte-exact
   fixture trees).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_line_endings import find_violations, main  # noqa: E402


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "Test", cwd=root)
    return root


def _write_crlf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))


def _commit_crlf_as_is(root: Path) -> None:
    # `-c core.autocrlf=false` so `git add` stores the CRLF bytes verbatim,
    # regardless of the host's own autocrlf configuration.
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", "add", "-A"], cwd=root, check=True, capture_output=True
    )
    _git("commit", "-q", "-m", "init", cwd=root)


def test_an_unguarded_crlf_script_is_reported(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _write_crlf(root / "run.sh", "#!/usr/bin/env bash\necho hi\n")
    _write_crlf(root / "hook-thing", "#!/usr/bin/env bash\necho hi\n")
    _commit_crlf_as_is(root)

    violations = find_violations(root)
    reported = {v.path for v in violations}
    assert reported == {"run.sh", "hook-thing"}


def test_the_same_files_are_clean_once_the_blanket_rule_applies(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    _write_crlf(root / "run.sh", "#!/usr/bin/env bash\necho hi\n")
    _commit_crlf_as_is(root)

    assert find_violations(root) == []


def test_a_crlf_file_under_an_allowlisted_text_prefix_is_clean(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitattributes").write_text(
        "packages/okf-io/tests/fixtures/** -text\n", encoding="utf-8", newline=""
    )
    _write_crlf(root / "packages/okf-io/tests/fixtures/edge/crlf.md", "line\n")
    _commit_crlf_as_is(root)

    assert find_violations(root) == []


def test_a_crlf_file_under_a_non_allowlisted_text_prefix_is_reported(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitattributes").write_text(
        "some/other/tree/** -text\n", encoding="utf-8", newline=""
    )
    _write_crlf(root / "some/other/tree/crlf.md", "line\n")
    _commit_crlf_as_is(root)

    violations = find_violations(root)
    reported = {v.path for v in violations}
    assert reported == {"some/other/tree/crlf.md"}


def test_a_binary_declared_asset_outside_the_allowlist_is_not_a_violation(tmp_path: Path) -> None:
    """`binary` is shorthand for `-text -diff -merge` -- a real image asset
    declared that way (as the now-deleted vendored subtree's own
    `.gitattributes` did for `*.png`/`*.jpg`/`*.gif`) is not the
    byte-exact-text-fixture concern
    assertion 2 exists for, and must not be flagged just for living outside
    the fixture-tree allowlist."""
    root = _repo(tmp_path)
    (root / ".gitattributes").write_text("*.png binary\n", encoding="utf-8", newline="")
    (root / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"binarydata")
    _commit_crlf_as_is(root)

    assert find_violations(root) == []


def test_an_empty_file_and_an_allowlisted_binary_file_are_both_clean(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitattributes").write_text(
        "* text=auto eol=lf\npackages/okf-io/tests/fixtures/** -text\n",
        encoding="utf-8",
        newline="",
    )
    (root / "empty.txt").write_text("", encoding="utf-8", newline="")
    (root / "packages/okf-io/tests/fixtures").mkdir(parents=True)
    (root / "packages/okf-io/tests/fixtures/blob.bin").write_bytes(b"\x00\x01\x02binary")
    _commit_crlf_as_is(root)

    assert find_violations(root) == []


def test_main_exits_nonzero_and_reports_on_a_violation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _write_crlf(root / "run.sh", "#!/usr/bin/env bash\necho hi\n")
    _commit_crlf_as_is(root)

    assert main([str(root)]) == 1


def test_main_exits_zero_on_a_clean_tree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8", newline="")
    _write_crlf(root / "run.sh", "#!/usr/bin/env bash\necho hi\n")
    _commit_crlf_as_is(root)

    assert main([str(root)]) == 0


def test_plugin_fork_experiments_are_byte_exact_without_exempting_adjacent_source(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitattributes").write_text(
        "packages/plugin-fork-io/** -text\n", encoding="utf-8", newline=""
    )
    fixture = "packages/plugin-fork-io/tests/fixtures/experiments/older-local/SKILL.md"
    adjacent = {
        "packages/plugin-fork-io/src/plugin_fork_io/normal.py",
        "packages/plugin-fork-io/tests/fixtures/normal.md",
        "packages/plugin-fork-io/tests/fixtures/experiments-other/raw.md",
    }
    for relative in (fixture, *sorted(adjacent)):
        _write_crlf(root / relative, "original evidence\n")
    _commit_crlf_as_is(root)

    assert {v.path for v in find_violations(root)} == adjacent
    assert (root / fixture).read_bytes() == b"original evidence\r\n"
