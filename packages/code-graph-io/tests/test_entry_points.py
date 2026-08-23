"""Unit tests for entry_points.emit."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from code_graph_io import entry_points, packages, store
from code_graph_io.uri import RepoContext

CTX = RepoContext(org="testorg", repo="testrepo")


# ---------- helpers ----------


def _setup_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "code.db"
    return store.connect(db_path, create=True)


def _write_pyproject(
    pkg_dir: Path,
    *,
    name: str | None = None,
    scripts: dict[str, str] | None = None,
    entry_points_dict: dict[str, dict[str, str]] | None = None,
) -> None:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    parts = ["[project]", f'name = "{name or pkg_dir.name}"']
    if scripts:
        parts.append("[project.scripts]")
        for k, v in scripts.items():
            parts.append(f'{k} = "{v}"')
    if entry_points_dict:
        for group, entries in entry_points_dict.items():
            parts.append(f'[project.entry-points."{group}"]')
            for k, v in entries.items():
                parts.append(f'{k} = "{v}"')
    (pkg_dir / "pyproject.toml").write_text("\n".join(parts) + "\n")


def _write_python_src(pkg_dir: Path, dotted: str, content: str = "def main(): pass\n") -> None:
    """Build src-layout file tree from a dotted module path.

    e.g. dotted='foo_pkg.cli' -> creates <pkg_dir>/src/foo_pkg/__init__.py
    and <pkg_dir>/src/foo_pkg/cli.py.
    """
    parts = dotted.split(".")
    src_root = pkg_dir / "src" / parts[0]
    src_root.mkdir(parents=True, exist_ok=True)
    (src_root / "__init__.py").write_text("")
    if len(parts) == 1:
        (src_root / "__init__.py").write_text(content)
        return
    inner = src_root
    for p in parts[1:-1]:
        inner = inner / p
        inner.mkdir(parents=True, exist_ok=True)
        (inner / "__init__.py").write_text("")
    (inner / f"{parts[-1]}.py").write_text(content)


def _write_package_json(pkg_dir: Path, data: dict) -> None:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "package.json").write_text(json.dumps(data))


# ---------- pyproject ----------


def _impl_target(conn: sqlite3.Connection, ep_name: str) -> str | None:
    row = conn.execute(
        """
        SELECT f.path FROM edges e
        JOIN nodes ep ON e.src = ep.id AND ep.kind='entry_point' AND ep.name=?
        JOIN nodes f  ON e.dst = f.id  AND f.kind='file'
        WHERE e.kind = 'implemented_by'
        """,
        (ep_name,),
    ).fetchone()
    return row[0] if row else None


def _declares_edge_exists(conn: sqlite3.Connection, pkg_name: str, ep_name: str) -> bool:
    # declares_entry_point edges originate from package OR app
    # nodes — apps declare entry points the same way packages do.
    row = conn.execute(
        """
        SELECT 1 FROM edges e
        JOIN nodes p ON e.src = p.id AND p.kind IN ('package', 'app') AND p.name=?
        JOIN nodes ep ON e.dst = ep.id AND ep.kind='entry_point' AND ep.name=?
        WHERE e.kind = 'declares_entry_point'
        """,
        (pkg_name, ep_name),
    ).fetchone()
    return row is not None


def test_pyproject_scripts_emits_entry_point(tmp_path: Path) -> None:
    """[project.scripts] foo-cli = 'foo_pkg.cli:main' resolves to a File."""
    pkg_dir = tmp_path / "packages" / "foo_pkg"
    _write_pyproject(pkg_dir, scripts={"foo-cli": "foo_pkg.cli:main"})
    _write_python_src(pkg_dir, "foo_pkg.cli")
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )
    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    row = conn.execute("SELECT name, attrs_json FROM nodes WHERE kind='entry_point' AND name='foo-cli'").fetchone()
    assert row is not None, "EntryPoint(name='foo-cli') not emitted"
    attrs = json.loads(row[1])
    assert attrs["entry_kind"] == "executable"
    assert attrs["source"] == "pyproject.scripts"
    assert attrs["callable"] == "main"
    assert _declares_edge_exists(conn, "foo_pkg", "foo-cli")
    impl_path = _impl_target(conn, "foo-cli")
    assert impl_path is not None
    assert impl_path.endswith("packages/foo_pkg/src/foo_pkg/cli.py")


def test_pyproject_entry_points_console_scripts(tmp_path: Path) -> None:
    """[project.entry-points.console_scripts] is executable."""
    pkg_dir = tmp_path / "packages" / "bar_pkg"
    _write_pyproject(
        pkg_dir,
        entry_points_dict={"console_scripts": {"bar": "bar_pkg.cli:run"}},
    )
    _write_python_src(pkg_dir, "bar_pkg.cli")
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )
    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='entry_point' AND name='bar'").fetchone()
    assert row is not None
    attrs = json.loads(row[0])
    assert attrs["entry_kind"] == "executable"
    assert attrs["source"] == "pyproject.entry-points.console_scripts"
    assert attrs["callable"] == "run"
    assert _impl_target(conn, "bar") is not None


def test_pyproject_entry_points_library_group(tmp_path: Path) -> None:
    """Non-console_scripts entry-points groups produce library kind."""
    pkg_dir = tmp_path / "packages" / "myapp"
    _write_pyproject(
        pkg_dir,
        entry_points_dict={"myapp.plugins": {"jsonfmt": "myapp.formatters:json_fmt"}},
    )
    _write_python_src(pkg_dir, "myapp.formatters")
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )
    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='entry_point' AND name='jsonfmt'").fetchone()
    assert row is not None
    attrs = json.loads(row[0])
    assert attrs["entry_kind"] == "library"
    assert attrs["source"] == "pyproject.entry-points.myapp.plugins"
    assert attrs["callable"] == "json_fmt"


def test_implemented_by_null_on_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """declared entry whose target file is missing -> no implemented_by edge +
    stderr warning. gw graph update still succeeds."""
    pkg_dir = tmp_path / "packages" / "ghost"
    _write_pyproject(pkg_dir, scripts={"ghost-cli": "ghost.cli:main"})
    # NOTE: do not write the source — leave the target missing.
    (pkg_dir / "src" / "ghost").mkdir(parents=True)
    (pkg_dir / "src" / "ghost" / "__init__.py").write_text("")
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )
    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    row = conn.execute("SELECT 1 FROM nodes WHERE kind='entry_point' AND name='ghost-cli'").fetchone()
    assert row is not None, "EntryPoint must still be emitted on miss"
    assert _impl_target(conn, "ghost-cli") is None
    captured = capsys.readouterr()
    assert "ghost-cli" in captured.err
    assert "cannot resolve implemented_by" in captured.err


# ---------- package.json ----------


def test_packagejson_bin_string_form(tmp_path: Path) -> None:
    """'bin' string form emits EntryPoint named after the package."""
    pkg_dir = tmp_path / "packages" / "jspkg"
    (pkg_dir / "src").mkdir(parents=True)
    (pkg_dir / "src" / "cli.js").write_text("#!/usr/bin/env node\n")
    _write_package_json(pkg_dir, {"name": "jspkg", "bin": "./src/cli.js"})
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )
    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='entry_point' AND name='jspkg'").fetchone()
    assert row is not None
    attrs = json.loads(row[0])
    assert attrs["entry_kind"] == "executable"
    assert attrs["source"] == "package.json.bin"
    assert attrs["callable"] is None
    assert _impl_target(conn, "jspkg") == "packages/jspkg/src/cli.js"


def test_packagejson_bin_object_form(tmp_path: Path) -> None:
    """'bin' object form emits one EntryPoint per key."""
    pkg_dir = tmp_path / "packages" / "multi"
    (pkg_dir / "src").mkdir(parents=True)
    (pkg_dir / "src" / "foo.js").write_text("")
    (pkg_dir / "src" / "bar.js").write_text("")
    _write_package_json(
        pkg_dir,
        {"name": "multi", "bin": {"foo-cli": "./src/foo.js", "bar-cli": "./src/bar.js"}},
    )
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )
    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    for name in ("foo-cli", "bar-cli"):
        row = conn.execute(
            "SELECT attrs_json FROM nodes WHERE kind='entry_point' AND name=?",
            (name,),
        ).fetchone()
        assert row is not None, f"{name} EntryPoint missing"
        attrs = json.loads(row[0])
        assert attrs["entry_kind"] == "executable"
        assert attrs["source"] == "package.json.bin"
    assert _impl_target(conn, "foo-cli") == "packages/multi/src/foo.js"
    assert _impl_target(conn, "bar-cli") == "packages/multi/src/bar.js"


def test_packagejson_main_and_module(tmp_path: Path) -> None:
    """03: 'main' and 'module' both produce one library EntryPoint."""
    pkg_dir = tmp_path / "packages" / "libpkg"
    (pkg_dir / "dist").mkdir(parents=True)
    (pkg_dir / "dist" / "index.cjs.js").write_text("")
    (pkg_dir / "dist" / "index.esm.js").write_text("")
    _write_package_json(
        pkg_dir,
        {
            "name": "libpkg",
            "main": "./dist/index.cjs.js",
            "module": "./dist/index.esm.js",
        },
    )
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )
    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    main_row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='entry_point' AND name='main'").fetchone()
    mod_row = conn.execute("SELECT attrs_json FROM nodes WHERE kind='entry_point' AND name='module'").fetchone()
    assert main_row is not None and mod_row is not None
    main_attrs = json.loads(main_row[0])
    mod_attrs = json.loads(mod_row[0])
    assert main_attrs["source"] == "package.json.main"
    assert main_attrs["entry_kind"] == "library"
    assert mod_attrs["source"] == "package.json.module"
    assert mod_attrs["entry_kind"] == "library"


def test_packagejson_exports_recursive_walk(tmp_path: Path) -> None:
    """exports recursion produces one EntryPoint per string leaf,
    distinguished by condition keys; wildcards mark is_wildcard + leave
    implemented_by NULL."""
    pkg_dir = tmp_path / "packages" / "exp"
    (pkg_dir / "dist").mkdir(parents=True)
    (pkg_dir / "dist" / "esm.js").write_text("")
    (pkg_dir / "dist" / "cjs.js").write_text("")
    _write_package_json(
        pkg_dir,
        {
            "name": "exp",
            "exports": {
                ".": {
                    "import": "./dist/esm.js",
                    "require": "./dist/cjs.js",
                },
                "./helpers/*": "./dist/helpers/*.js",
            },
        },
    )
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )
    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    rows = conn.execute("SELECT name, attrs_json FROM nodes WHERE kind='entry_point'").fetchall()
    by_name_cond: dict[tuple[str, str | None], dict] = {}
    for name, attrs_json in rows:
        attrs = json.loads(attrs_json)
        by_name_cond[(name, attrs.get("condition"))] = attrs

    # Conditional exports for "." produce two EntryPoints sharing name="."
    # but distinguished by condition.
    assert (".", "import") in by_name_cond
    assert (".", "require") in by_name_cond
    assert by_name_cond[(".", "import")]["source"] == "package.json.exports"
    assert by_name_cond[(".", "import")]["entry_kind"] == "library"
    assert by_name_cond[(".", "import")]["is_wildcard"] is False

    # Wildcard leaf
    wildcard_keys = [k for k in by_name_cond if "helpers" in k[0]]
    assert wildcard_keys, "wildcard export leaf not emitted"
    wkey = wildcard_keys[0]
    assert by_name_cond[wkey]["is_wildcard"] is True
    assert "*" in (by_name_cond[wkey]["path_pattern"] or "")
    assert _impl_target(conn, wkey[0]) is None  # wildcards never resolve to a file


def test_declares_entry_point_edge_present(tmp_path: Path) -> None:
    """Every EntryPoint has a declares_entry_point edge from its declaring Package."""
    pkg_dir = tmp_path / "packages" / "edgepkg"
    (pkg_dir / "src").mkdir(parents=True)
    (pkg_dir / "src" / "main.js").write_text("")
    _write_package_json(pkg_dir, {"name": "edgepkg", "main": "./src/main.js", "bin": "./src/main.js"})
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )
    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    ep_names = [r[0] for r in conn.execute("SELECT name FROM nodes WHERE kind='entry_point'").fetchall()]
    assert ep_names, "no EntryPoint emitted"
    for name in ep_names:
        assert _declares_edge_exists(conn, "edgepkg", name), f"declares_entry_point edge missing for {name}"


def test_shebang_script_does_not_emit_entry_point(tmp_path: Path) -> None:
    """shebang scripts are not declared entries; no EntryPoint emitted."""
    pkg_dir = tmp_path / "packages" / "shy"
    _write_pyproject(pkg_dir, name="shy")  # No scripts, no entry_points declared
    _write_python_src(pkg_dir, "shy")
    (pkg_dir / "scripts").mkdir()
    shebang = pkg_dir / "scripts" / "run.py"
    shebang.write_text("#!/usr/bin/env python3\nprint('hi')\n")
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )
    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    cnt = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='entry_point'").fetchone()[0]
    assert cnt == 0


def test_faceted_member_gets_exactly_one_declares_entry_point_edge(tmp_path: Path) -> None:
    """A member with both a Package and an App node (facet model) must not
    double-emit declares_entry_point — exactly one edge, from the Package."""
    pkg_dir = tmp_path / "myapp"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\nversion = "0.1.0"\n[project.scripts]\nmyapp = "myapp.cli:main"\n'
    )
    _write_python_src(pkg_dir, "myapp.cli")
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )

    # Sanity: this member is actually faceted (has both Package and App rows)
    # under Task 1's facet model — otherwise this test would not exercise the
    # bug at all.
    kinds = {r[0] for r in conn.execute("SELECT kind FROM nodes WHERE kind IN ('package', 'app') AND name='myapp'")}
    assert kinds == {"package", "app"}, f"expected faceted member, got kinds={kinds!r}"

    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    ep_row = conn.execute("SELECT id FROM nodes WHERE kind='entry_point' AND name='myapp'").fetchone()
    assert ep_row is not None
    inbound = conn.execute(
        "SELECT s.kind FROM edges e JOIN nodes s ON e.src = s.id WHERE e.kind='declares_entry_point' AND e.dst=?",
        (ep_row[0],),
    ).fetchall()
    assert [r[0] for r in inbound] == ["package"], f"expected exactly one edge, from Package; got {inbound!r}"


def test_malformed_pyproject_does_not_crash(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Defensive: malformed pyproject.toml inside a Package directory is skipped."""
    pkg_dir = tmp_path / "packages" / "okpkg"
    # Write a VALID manifest so packages.refresh sees this Package row first.
    _write_pyproject(pkg_dir, name="okpkg", scripts={"ok": "okpkg.cli:main"})
    _write_python_src(pkg_dir, "okpkg.cli")
    conn = _setup_db(tmp_path)
    packages.refresh(
        conn, repo_root=tmp_path, ctx=CTX, manifests=packages.discover_manifest_packages(tmp_path, ctx=CTX)
    )
    # Now corrupt the manifest before entry_points.emit reads it. We rewrite
    # in-place so the Package row remains but tomllib will fail.
    (pkg_dir / "pyproject.toml").write_text("not [valid toml [at all")
    entry_points.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    # No EntryPoint emitted from the corrupted manifest.
    cnt = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='entry_point'").fetchone()[0]
    assert cnt == 0
    captured = capsys.readouterr()
    assert "failed to parse" in captured.err
    assert "pyproject.toml" in captured.err
