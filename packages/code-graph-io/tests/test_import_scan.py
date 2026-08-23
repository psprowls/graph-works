"""Unit tests for code_graph_io.import_scan."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from code_graph_io import packages, store, structural_nodes
from code_graph_io.import_scan import (
    resolve_js_import_file,
    resolve_python_import_file,
    scan_files_imports,
    scan_package_imports,
)
from code_graph_io.uri import RepoContext

CTX = RepoContext(org="testorg", repo="testrepo")


# ---------- helpers ----------


def _setup(tmp_path: Path) -> sqlite3.Connection:
    return store.connect(tmp_path / "code.db", create=True)


def _emit_pipeline(conn: sqlite3.Connection, repo_root: Path) -> None:
    """packages.refresh + structural_nodes.emit (populates File.attrs.is_test)."""
    with store.transaction(conn):
        packages.refresh(
            conn, repo_root=repo_root, ctx=CTX, manifests=packages.discover_manifest_packages(repo_root, ctx=CTX)
        )
        structural_nodes.emit(conn, repo_root=repo_root, ctx=CTX, skip_dirs=frozenset())


def _write_python_pkg(root: Path, name: str, files: dict[str, str]) -> None:
    """Build a minimal src-layout Python package + write files relative to pkg root."""
    pkg_dir = root / "packages" / name
    importable = name.replace("-", "_")
    src_dir = pkg_dir / "src" / importable
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("")
    (pkg_dir / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
    for rel, content in files.items():
        (pkg_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg_dir / rel).write_text(content)


def _write_js_pkg(root: Path, dir_name: str, pkg_name: str, files: dict[str, str]) -> None:
    """Build a minimal JS package at packages/<dir_name>/ with package.json name=<pkg_name>."""
    pkg_dir = root / "packages" / dir_name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "package.json").write_text(json.dumps({"name": pkg_name}))
    for rel, content in files.items():
        (pkg_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg_dir / rel).write_text(content)


def _pkg_rows(conn: sqlite3.Connection) -> list[tuple[str, str | None, str | None]]:
    return [
        (r[0], r[1], r[2])
        for r in conn.execute("SELECT name, path, attrs_json FROM nodes WHERE kind='package'").fetchall()
    ]


# ---------- (a) Python imports resolved via py_importable_to_pkg ----------


def test_scan_package_imports_python(tmp_path: Path) -> None:
    _write_python_pkg(
        tmp_path,
        "pkg-a",
        {
            "src/pkg_a/foo.py": "from pkg_b import bar\n",
        },
    )
    _write_python_pkg(
        tmp_path,
        "pkg-b",
        {
            "src/pkg_b/bar.py": "x = 1\n",
        },
    )
    conn = _setup(tmp_path)
    _emit_pipeline(conn, tmp_path)

    result = scan_package_imports(
        conn,
        tmp_path,
        "pkg-a",
        "packages/pkg-a",
        include_test_files=False,
    )
    assert ("pkg-b", "packages/pkg-b") in result


# ---------- (b) JS bare-spec imports ----------


def test_scan_js_bare_spec_resolved(tmp_path: Path) -> None:
    _write_js_pkg(
        tmp_path,
        "jspkg-a",
        "jspkg-a",
        {
            "src/index.js": 'import { x } from "jspkg-b";\n',
        },
    )
    _write_js_pkg(
        tmp_path,
        "jspkg-b",
        "jspkg-b",
        {
            "src/index.js": "export const x = 1;\n",
        },
    )
    conn = _setup(tmp_path)
    _emit_pipeline(conn, tmp_path)

    result = scan_package_imports(
        conn,
        tmp_path,
        "jspkg-a",
        "packages/jspkg-a",
        include_test_files=False,
    )
    assert ("jspkg-b", "packages/jspkg-b") in result


# ---------- (c) JS relative imports resolved via _owning_package ----------


def test_scan_js_relative_import_resolved(tmp_path: Path) -> None:
    _write_js_pkg(
        tmp_path,
        "jspkg-a",
        "jspkg-a",
        {
            "src/index.js": 'import { x } from "../../jspkg-b/src/foo";\n',
        },
    )
    _write_js_pkg(
        tmp_path,
        "jspkg-b",
        "jspkg-b",
        {
            "src/foo.js": "export const x = 1;\n",
        },
    )
    conn = _setup(tmp_path)
    _emit_pipeline(conn, tmp_path)

    result = scan_package_imports(
        conn,
        tmp_path,
        "jspkg-a",
        "packages/jspkg-a",
        include_test_files=False,
    )
    assert ("jspkg-b", "packages/jspkg-b") in result


# ---------- (d) JS scoped-package imports (@scope/name) ----------


def test_scan_js_scoped_package(tmp_path: Path) -> None:
    _write_js_pkg(
        tmp_path,
        "consumer",
        "consumer",
        {
            "src/index.js": 'import { x } from "@scope/foo";\n',
        },
    )
    _write_js_pkg(
        tmp_path,
        "scope__foo",
        "@scope/foo",
        {
            "src/index.js": "export const x = 1;\n",
        },
    )
    conn = _setup(tmp_path)
    _emit_pipeline(conn, tmp_path)

    result = scan_package_imports(
        conn,
        tmp_path,
        "consumer",
        "packages/consumer",
        include_test_files=False,
    )
    assert ("@scope/foo", "packages/scope__foo") in result


# ---------- (e) Test files excluded when include_test_files=False ----------


def test_scan_excludes_test_files_by_default(tmp_path: Path) -> None:
    # pkg-a has a test file that imports pkg-b; non-test files do NOT import pkg-b.
    _write_python_pkg(
        tmp_path,
        "pkg-a",
        {
            "src/pkg_a/foo.py": "x = 1\n",  # non-test file, no imports
            "tests/test_x.py": "from pkg_b import bar\n",  # test file imports pkg-b
        },
    )
    _write_python_pkg(
        tmp_path,
        "pkg-b",
        {
            "src/pkg_b/bar.py": "x = 1\n",
        },
    )
    conn = _setup(tmp_path)
    _emit_pipeline(conn, tmp_path)

    result = scan_package_imports(
        conn,
        tmp_path,
        "pkg-a",
        "packages/pkg-a",
        include_test_files=False,
    )
    assert ("pkg-b", "packages/pkg-b") not in result


# ---------- (f) Test files included when include_test_files=True ----------


def test_scan_includes_test_files_when_flag_set(tmp_path: Path) -> None:
    _write_python_pkg(
        tmp_path,
        "pkg-a",
        {
            "src/pkg_a/foo.py": "x = 1\n",
            "tests/test_x.py": "from pkg_b import bar\n",
        },
    )
    _write_python_pkg(
        tmp_path,
        "pkg-b",
        {
            "src/pkg_b/bar.py": "x = 1\n",
        },
    )
    conn = _setup(tmp_path)
    _emit_pipeline(conn, tmp_path)

    result = scan_package_imports(
        conn,
        tmp_path,
        "pkg-a",
        "packages/pkg-a",
        include_test_files=True,
    )
    assert ("pkg-b", "packages/pkg-b") in result


# ---------- (g) Unreadable files silently skipped ----------


def test_scan_unreadable_file_silently_skipped(tmp_path: Path) -> None:
    # scan_files_imports directly with a list including a non-existent path.
    _write_python_pkg(
        tmp_path,
        "pkg-a",
        {
            "src/pkg_a/foo.py": "from pkg_b import bar\n",
        },
    )
    _write_python_pkg(
        tmp_path,
        "pkg-b",
        {
            "src/pkg_b/bar.py": "x = 1\n",
        },
    )
    conn = _setup(tmp_path)
    _emit_pipeline(conn, tmp_path)
    pkg_rows = _pkg_rows(conn)

    file_paths = [
        "packages/pkg-a/src/pkg_a/foo.py",  # exists, imports pkg-b
        "packages/pkg-a/does_not_exist.py",  # does not exist on disk
    ]
    result = scan_files_imports(tmp_path, file_paths, pkg_rows)
    assert ("pkg-b", "packages/pkg-b") in result  # other file still processed


# ---------- (h) Stdlib / third-party imports ignored ----------


def test_scan_stdlib_imports_ignored(tmp_path: Path) -> None:
    _write_python_pkg(
        tmp_path,
        "pkg-a",
        {
            "src/pkg_a/foo.py": "import json\nimport typing\nfrom os import path\n",
        },
    )
    conn = _setup(tmp_path)
    _emit_pipeline(conn, tmp_path)

    result = scan_package_imports(
        conn,
        tmp_path,
        "pkg-a",
        "packages/pkg-a",
        include_test_files=False,
    )
    # Only pkg-a is a registered Package; stdlib names aren't in py_map.
    # pkg-a may appear (it imports itself transitively via __init__.py?) — assert
    # the foreign names are absent.
    matched_names = {name for name, _ in result}
    assert "json" not in matched_names
    assert "typing" not in matched_names
    assert "os" not in matched_names


# ---------- (i) resolve_js_import_file: relative specifier → repo-relative FILE ----------


def test_resolve_js_import_file_relative(tmp_path: Path) -> None:
    """A relative JS specifier resolving to a sibling file returns its repo-relative path."""
    _write_js_pkg(
        tmp_path,
        "jspkg-a",
        "jspkg-a",
        {
            "src/index.js": 'import { x } from "../../jspkg-b/src/foo";\n',
        },
    )
    _write_js_pkg(
        tmp_path,
        "jspkg-b",
        "jspkg-b",
        {
            "src/foo.js": "export const x = 1;\n",
        },
    )
    importing = tmp_path / "packages" / "jspkg-a" / "src" / "index.js"
    result = resolve_js_import_file(
        "../../jspkg-b/src/foo",
        importing,
        tmp_path,
    )
    assert result == "packages/jspkg-b/src/foo.js"


def test_resolve_js_import_file_index_suffix(tmp_path: Path) -> None:
    """A relative directory specifier resolves to its index.* file."""
    _write_js_pkg(
        tmp_path,
        "jspkg-a",
        "jspkg-a",
        {
            "src/index.js": 'import { x } from "./sub";\n',
            "src/sub/index.js": "export const x = 1;\n",
        },
    )
    importing = tmp_path / "packages" / "jspkg-a" / "src" / "index.js"
    result = resolve_js_import_file("./sub", importing, tmp_path)
    assert result == "packages/jspkg-a/src/sub/index.js"


def test_resolve_js_import_file_bare_returns_none(tmp_path: Path) -> None:
    """A bare/third-party specifier (no in-repo file) returns None."""
    _write_js_pkg(
        tmp_path,
        "jspkg-a",
        "jspkg-a",
        {
            "src/index.js": 'import x from "react";\n',
        },
    )
    importing = tmp_path / "packages" / "jspkg-a" / "src" / "index.js"
    assert resolve_js_import_file("react", importing, tmp_path) is None


def test_resolve_js_import_file_missing_relative_returns_none(tmp_path: Path) -> None:
    """A relative specifier pointing at a non-existent file returns None."""
    _write_js_pkg(
        tmp_path,
        "jspkg-a",
        "jspkg-a",
        {
            "src/index.js": 'import x from "./nope";\n',
        },
    )
    importing = tmp_path / "packages" / "jspkg-a" / "src" / "index.js"
    assert resolve_js_import_file("./nope", importing, tmp_path) is None


# ---------- (j) resolve_python_import_file: dotted module → repo-relative FILE ----------


def test_resolve_python_import_file_module(tmp_path: Path) -> None:
    """A first-party dotted module resolves to its module file."""
    _write_python_pkg(
        tmp_path,
        "pkg-b",
        {
            "src/pkg_b/bar.py": "x = 1\n",
        },
    )
    conn = _setup(tmp_path)
    _emit_pipeline(conn, tmp_path)
    pkg_rows = _pkg_rows(conn)

    result = resolve_python_import_file("pkg_b.bar", tmp_path, pkg_rows)
    assert result == "packages/pkg-b/src/pkg_b/bar.py"


def test_resolve_python_import_file_package_init(tmp_path: Path) -> None:
    """A first-party top-level package resolves to its __init__.py."""
    _write_python_pkg(
        tmp_path,
        "pkg-b",
        {
            "src/pkg_b/bar.py": "x = 1\n",
        },
    )
    conn = _setup(tmp_path)
    _emit_pipeline(conn, tmp_path)
    pkg_rows = _pkg_rows(conn)

    result = resolve_python_import_file("pkg_b", tmp_path, pkg_rows)
    assert result == "packages/pkg-b/src/pkg_b/__init__.py"


def test_resolve_python_import_file_third_party_returns_none(tmp_path: Path) -> None:
    """A stdlib/third-party module (not a first-party package) returns None."""
    _write_python_pkg(
        tmp_path,
        "pkg-b",
        {
            "src/pkg_b/bar.py": "x = 1\n",
        },
    )
    conn = _setup(tmp_path)
    _emit_pipeline(conn, tmp_path)
    pkg_rows = _pkg_rows(conn)

    assert resolve_python_import_file("json", tmp_path, pkg_rows) is None
    assert resolve_python_import_file("os.path", tmp_path, pkg_rows) is None
