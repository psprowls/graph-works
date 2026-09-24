"""Unit tests for code_graph_io.requirements — pip requirements-file reading."""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest
from code_graph_io import requirements


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def test_follows_same_dir_includes_in_order(tmp_path: Path) -> None:
    root = _write(tmp_path / "requirements.txt", "-r requirements-base.txt\n-r requirements-heavy.txt\n")
    _write(tmp_path / "requirements-base.txt", "fastapi>=0.110\nuvicorn[standard]==0.30.1\n")
    _write(tmp_path / "requirements-heavy.txt", "numpy>=1.26\n")

    read = requirements.read_requirements(root, tmp_path)

    assert read.runtime == ("fastapi>=0.110", "uvicorn[standard]==0.30.1", "numpy>=1.26")
    assert read.dev == ()
    assert read.has_own_requirements is False
    assert read.direct_includes == (
        (tmp_path / "requirements-base.txt").resolve(),
        (tmp_path / "requirements-heavy.txt").resolve(),
    )
    assert read.visited == frozenset(
        p.resolve() for p in (root, tmp_path / "requirements-base.txt", tmp_path / "requirements-heavy.txt")
    )


@pytest.mark.parametrize(
    "include_line",
    ["-r base.txt", "-rbase.txt", "--requirement base.txt", "--requirement=base.txt"],
)
def test_include_spellings(tmp_path: Path, include_line: str) -> None:
    root = _write(tmp_path / "requirements.txt", f"{include_line}\n")
    _write(tmp_path / "base.txt", "httpx\n")

    assert requirements.read_requirements(root, tmp_path).runtime == ("httpx",)


def test_include_cycle_is_a_no_op(tmp_path: Path) -> None:
    root = _write(tmp_path / "requirements.txt", "-r b.txt\nflask\n")
    _write(tmp_path / "b.txt", "-r requirements.txt\nrequests\n")

    read = requirements.read_requirements(root, tmp_path)

    assert read.runtime == ("requests", "flask")
    assert read.has_own_requirements is True


def test_self_include_is_a_no_op(tmp_path: Path) -> None:
    root = _write(tmp_path / "requirements.txt", "-r requirements.txt\nflask\n")

    assert requirements.read_requirements(root, tmp_path).runtime == ("flask",)


def test_missing_include_warns_and_continues(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _write(tmp_path / "requirements.txt", "-r nope.txt\nflask\n")

    read = requirements.read_requirements(root, tmp_path)

    assert read.runtime == ("flask",)
    assert read.direct_includes == ((tmp_path / "nope.txt").resolve(),)
    err = capsys.readouterr().err
    assert err.count("warning:") == 1
    assert "nope.txt" in err


def test_outside_repo_include_warns_and_is_skipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    _write(tmp_path / "outside.txt", "secret-dep\n")
    root = _write(repo / "requirements.txt", "-r ../outside.txt\nflask\n")

    read = requirements.read_requirements(root, repo)

    assert read.runtime == ("flask",)
    assert "outside the repository" in capsys.readouterr().err


def test_constraint_files_are_not_followed(tmp_path: Path) -> None:
    root = _write(tmp_path / "requirements.txt", "-c constraints.txt\nflask\n")
    _write(tmp_path / "constraints.txt", "werkzeug<3\n")

    read = requirements.read_requirements(root, tmp_path)

    assert read.runtime == ("flask",)
    assert (tmp_path / "constraints.txt").resolve() not in read.visited


def test_comments_options_markers_and_extras(tmp_path: Path) -> None:
    root = _write(
        tmp_path / "requirements.txt",
        "# full-line comment\n"
        "--index-url https://example.invalid/simple\n"
        "-e ./local-pkg\n"
        "requests[socks]>=2.31  # inline comment\n"
        'tomli>=2; python_version < "3.11"\n'
        "pkg @ https://example.invalid/pkg.whl\n"
        "\n",
    )

    assert requirements.read_requirements(root, tmp_path).runtime == (
        "requests[socks]>=2.31",
        'tomli>=2; python_version < "3.11"',
        "pkg @ https://example.invalid/pkg.whl",
    )


def test_crlf_continuation_and_hash_are_joined_and_stripped(tmp_path: Path) -> None:
    root = tmp_path / "requirements.txt"
    root.write_bytes(b"numpy>=1.26 \\\r\n    --hash=sha256:deadbeef\r\nflask\r\n")

    assert requirements.read_requirements(root, tmp_path).runtime == ("numpy>=1.26", "flask")


def test_utf16_bom_file_decodes(tmp_path: Path) -> None:
    """`pip freeze > requirements.txt` in Windows PowerShell 5 writes UTF-16LE + BOM."""
    root = tmp_path / "requirements.txt"
    root.write_bytes(codecs.BOM_UTF16_LE + "fastapi==0.110.0\r\nuvicorn==0.30.1\r\n".encode("utf-16-le"))

    assert requirements.read_requirements(root, tmp_path).runtime == ("fastapi==0.110.0", "uvicorn==0.30.1")


def test_utf8_bom_file_decodes(tmp_path: Path) -> None:
    root = tmp_path / "requirements.txt"
    root.write_bytes(codecs.BOM_UTF8 + b"flask\n")

    assert requirements.read_requirements(root, tmp_path).runtime == ("flask",)


def test_undecodable_file_warns_and_yields_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "requirements.txt"
    root.write_bytes(b"fl\xffask\n")

    read = requirements.read_requirements(root, tmp_path)

    assert read.runtime == ()
    assert "warning:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("requirements-dev.txt", True),
        ("requirements_test.txt", True),
        ("dev-requirements.txt", True),
        ("test_requirements.txt", True),
        ("requirements-docs.txt", True),
        ("requirements-typing.txt", True),
        ("REQUIREMENTS-CI.txt", True),
        ("requirements-heavy.txt", False),
        ("requirements-base.txt", False),
        ("requirements.txt", False),
        ("requirements-dev.in", False),
    ],
)
def test_is_dev_requirements_name(filename: str, expected: bool) -> None:
    assert requirements.is_dev_requirements_name(filename) is expected


def test_dev_named_include_tags_lines_dev(tmp_path: Path) -> None:
    root = _write(tmp_path / "requirements.txt", "flask\n-r requirements-dev.txt\n-r requirements/lint.txt\n")
    _write(tmp_path / "requirements-dev.txt", "pytest\n-r requirements-heavy.txt\n")
    _write(tmp_path / "requirements-heavy.txt", "numpy\n")
    _write(tmp_path / "requirements" / "lint.txt", "ruff\n")

    read = requirements.read_requirements(root, tmp_path)

    assert read.runtime == ("flask",)
    # dev propagates: a file included FROM a dev file is dev too.
    assert read.dev == ("pytest", "numpy", "ruff")


def test_dev_true_forces_everything_dev_and_skip_excludes_files(tmp_path: Path) -> None:
    root = _write(tmp_path / "requirements.txt", "flask\n")
    sibling = _write(tmp_path / "requirements-dev.txt", "-r requirements.txt\npytest\n")

    read = requirements.read_requirements(sibling, tmp_path, dev=True, skip=frozenset({root.resolve()}))

    assert read.runtime == ()
    assert read.dev == ("pytest",)


def test_dev_sibling_files_lists_only_dev_named_files(tmp_path: Path) -> None:
    root = _write(tmp_path / "requirements.txt", "flask\n")
    _write(tmp_path / "requirements-heavy.txt", "numpy\n")
    _write(tmp_path / "requirements-dev.txt", "pytest\n")
    _write(tmp_path / "test-requirements.txt", "hypothesis\n")

    assert requirements.dev_sibling_files(root) == (
        (tmp_path / "requirements-dev.txt").resolve(),
        (tmp_path / "test-requirements.txt").resolve(),
    )
