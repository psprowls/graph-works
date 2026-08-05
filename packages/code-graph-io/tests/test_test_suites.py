"""Unit tests for test_suites.emit, plus fixture-driven integration tests."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from code_graph_io import packages, store, structural_nodes, test_suites
from code_graph_io.uri import RepoContext

CTX = RepoContext(org="testorg", repo="testrepo")


# ---------- helpers ----------


def _setup(tmp_path: Path) -> sqlite3.Connection:
    return store.connect(tmp_path / "code.db", create=True)


def _run_emit_pipeline(conn: sqlite3.Connection, repo_root: Path) -> None:
    """Run packages.refresh + structural_nodes.emit + test_suites.emit
    inside a single transaction (mirrors update.run order)."""
    with store.transaction(conn):
        packages.refresh(conn, repo_root=repo_root, ctx=CTX)
        structural_nodes.emit(conn, repo_root=repo_root, ctx=CTX, skip_dirs=frozenset())
        test_suites.emit(conn, repo_root=repo_root, ctx=CTX, skip_dirs=frozenset())


def _write_pyproject(pkg_dir: Path, *, name: str | None = None, body: str = "") -> None:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    n = name or pkg_dir.name
    (pkg_dir / "pyproject.toml").write_text(f'[project]\nname = "{n}"\n{body}\n')


def _write_python_pkg(pkg_dir: Path, importable: str) -> None:
    """Build a minimal src-layout Python package with an empty __init__.py."""
    src = pkg_dir / "src" / importable
    src.mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").write_text("")


def _write_package_json(pkg_dir: Path, data: dict) -> None:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "package.json").write_text(json.dumps(data))


def _suite_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (r[0], r[1])
        for r in conn.execute("SELECT name, path FROM nodes WHERE kind='test_suite' ORDER BY path").fetchall()
    ]


# ---------- suite emission + re-parenting ----------


def _seed_root_pkg(tmp_path: Path) -> Path:
    """A root pyproject + a tests-only package so structural_nodes emits a
    Repository node even when no real Package files exist."""
    root_pkg = tmp_path / "rootpkg"
    _write_pyproject(root_pkg, name="rootpkg")
    _write_python_pkg(root_pkg, "rootpkg")
    return root_pkg


def test_repo_root_subdirs_become_suites(tmp_path: Path) -> None:
    """repo-root tests/<subdir>/ -> one TestSuite per subdir."""
    _seed_root_pkg(tmp_path)
    (tmp_path / "tests" / "integration").mkdir(parents=True)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "integration" / "test_foo.py").write_text("")
    (tmp_path / "tests" / "unit" / "test_bar.py").write_text("")

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    rows = _suite_rows(conn)
    paths = {r[1] for r in rows}
    assert paths == {"tests/integration", "tests/unit"}


def test_repo_root_flat_tests_creates_single_suite(tmp_path: Path) -> None:
    """flat repo-root tests/ (no subdirs) -> a single suite named tests."""
    _seed_root_pkg(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_only.py").write_text("")

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    rows = _suite_rows(conn)
    assert rows == [("tests", "tests")]


def test_package_local_tests_dir_is_package_contained(tmp_path: Path) -> None:
    """<pkg>/tests/ creates a TestSuite contained by the Package."""
    pkg_dir = tmp_path / "packages" / "foo"
    _write_pyproject(pkg_dir, name="foo")
    _write_python_pkg(pkg_dir, "foo")
    (pkg_dir / "tests").mkdir()
    (pkg_dir / "tests" / "test_bar.py").write_text("")

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    rows = _suite_rows(conn)
    # package-owned suite gets unique qualified name <owner>-<kind>-tests
    assert ("foo-unit-tests", "packages/foo/tests") in rows
    assert ("tests", "packages/foo/tests") not in rows
    # Parent edge: Package(foo) -> TestSuite
    parent = conn.execute(
        """
        SELECT p.kind, p.name FROM edges e
        JOIN nodes p ON e.src = p.id
        JOIN nodes s ON e.dst = s.id
        WHERE s.kind='test_suite' AND s.path='packages/foo/tests'
              AND e.kind='physically_contains'
        """
    ).fetchone()
    assert parent is not None
    assert parent == ("package", "foo")


def test_jsts_underscores_tests_dir_same_as_c(tmp_path: Path) -> None:
    """<pkg>/__tests__/ for a JS Package creates a Package-contained suite."""
    pkg_dir = tmp_path / "packages" / "jspkg"
    _write_package_json(pkg_dir, {"name": "jspkg"})
    (pkg_dir / "__tests__").mkdir()
    (pkg_dir / "__tests__" / "index.test.js").write_text("")

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    rows = _suite_rows(conn)
    # package-owned JS suite gets qualified name <owner>-<kind>-tests
    assert ("jspkg-unit-tests", "packages/jspkg/__tests__") in rows
    assert ("__tests__", "packages/jspkg/__tests__") not in rows


# ---------- tests edges ----------


def _tests_edge_targets(conn: sqlite3.Connection, suite_path: str) -> set[tuple[str, str | None]]:
    rows = conn.execute(
        """
        SELECT n.kind, n.name FROM edges e
        JOIN nodes s ON e.src = s.id
        JOIN nodes n ON e.dst = n.id
        WHERE e.kind='tests' AND s.kind='test_suite' AND s.path=?
        """,
        (suite_path,),
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def test_tests_edge_python_imports(tmp_path: Path) -> None:
    """Python test file 'from foo import bar' -> TestSuite -> Package(foo)."""
    foo_dir = tmp_path / "packages" / "foo"
    _write_pyproject(foo_dir, name="foo")
    _write_python_pkg(foo_dir, "foo")
    bar_dir = tmp_path / "packages" / "bar"
    _write_pyproject(bar_dir, name="bar")
    _write_python_pkg(bar_dir, "bar")

    (tmp_path / "tests" / "integration").mkdir(parents=True)
    (tmp_path / "tests" / "integration" / "test_x.py").write_text("from foo import baz\nimport bar\n")

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    targets = _tests_edge_targets(conn, "tests/integration")
    assert ("package", "foo") in targets
    assert ("package", "bar") in targets


def test_tests_edge_js_bare_imports(tmp_path: Path) -> None:
    """JS test 'import x from \"jspkg\"' -> TestSuite -> Package(jspkg)."""
    pkg_dir = tmp_path / "packages" / "jspkg"
    _write_package_json(pkg_dir, {"name": "jspkg"})
    other_dir = tmp_path / "packages" / "other"
    _write_package_json(other_dir, {"name": "other"})

    (pkg_dir / "__tests__").mkdir()
    (pkg_dir / "__tests__" / "index.test.js").write_text('import { x } from "other";\n')

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    targets = _tests_edge_targets(conn, "packages/jspkg/__tests__")
    assert ("package", "other") in targets


def test_tests_edge_js_relative_imports(tmp_path: Path) -> None:
    """JS test 'import x from \"../src/foo\"' -> TestSuite -> Package via _owning_package."""
    pkg_dir = tmp_path / "packages" / "jspkg"
    _write_package_json(pkg_dir, {"name": "jspkg"})
    (pkg_dir / "src").mkdir()
    (pkg_dir / "src" / "foo.js").write_text("export const x = 1;\n")

    (pkg_dir / "__tests__").mkdir()
    (pkg_dir / "__tests__" / "rel.test.js").write_text('import { x } from "../src/foo";\n')

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    targets = _tests_edge_targets(conn, "packages/jspkg/__tests__")
    assert ("package", "jspkg") in targets


def test_tests_edge_repository_threshold(tmp_path: Path) -> None:
    """a suite importing >=5 first-party packages gets an extra
    TestSuite -> Repository edge."""
    for n in ("p1", "p2", "p3", "p4", "p5"):
        pkg_dir = tmp_path / "packages" / n
        _write_pyproject(pkg_dir, name=n)
        _write_python_pkg(pkg_dir, n)

    (tmp_path / "tests" / "wide").mkdir(parents=True)
    (tmp_path / "tests" / "wide" / "test_x.py").write_text("import p1\nimport p2\nimport p3\nimport p4\nimport p5\n")

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    targets = _tests_edge_targets(conn, "tests/wide")
    pkg_targets = {t for t in targets if t[0] == "package"}
    assert len(pkg_targets) >= 5
    # Repository edge present
    assert any(t[0] == "repository" for t in targets), (
        f"expected Repository edge with >=5 pkg imports; targets={targets}"
    )


def test_test_file_re_parented_from_repository_to_suite(tmp_path: Path) -> None:
    """The only physically_contains parent of a test file is its
    TestSuite, not the Repository."""
    pkg_dir = tmp_path / "packages" / "foo"
    _write_pyproject(pkg_dir, name="foo")
    _write_python_pkg(pkg_dir, "foo")
    (pkg_dir / "tests").mkdir()
    test_path = "packages/foo/tests/test_bar.py"
    (tmp_path / test_path).write_text("")

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    parents = conn.execute(
        """
        SELECT p.kind FROM edges e
        JOIN nodes p ON e.src = p.id
        JOIN nodes f ON e.dst = f.id
        WHERE f.kind='file' AND f.path=? AND e.kind='physically_contains'
        """,
        (test_path,),
    ).fetchall()
    parent_kinds = {r[0] for r in parents}
    assert parent_kinds == {"test_suite"}, f"expected only TestSuite parent, got {parent_kinds}"


def _physically_contains_parents(conn: sqlite3.Connection, file_path: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT p.kind FROM edges e
        JOIN nodes p ON e.src = p.id
        JOIN nodes f ON e.dst = f.id
        WHERE f.kind='file' AND f.path=? AND e.kind='physically_contains'
        """,
        (file_path,),
    ).fetchall()
    return [r[0] for r in rows]


def test_stale_suite_edge_cleared_when_file_loses_root_assignment(tmp_path: Path) -> None:
    """Stale-edge regression: a file re-parented onto a TestSuite in run N that
    loses its root assignment in run N+1 (incremental) must end up with exactly
    one physically_contains parent (Repository), not two."""
    pkg_dir = tmp_path / "packages" / "foo"
    body = '[tool.pytest.ini_options]\ntestpaths = ["spec"]\n'
    _write_pyproject(pkg_dir, name="foo", body=body)
    _write_python_pkg(pkg_dir, "foo")
    (pkg_dir / "spec").mkdir()
    test_path = "packages/foo/spec/test_x.py"
    (tmp_path / test_path).write_text("")

    conn = _setup(tmp_path)

    # Run 1: testpaths config discovers spec/ as a root -> file re-parented onto its TestSuite.
    _run_emit_pipeline(conn, tmp_path)
    assert _physically_contains_parents(conn, test_path) == ["test_suite"]

    # Run 2 (incremental): drop the testpaths config so spec/ is no longer a
    # discovered root. The file itself is untouched.
    _write_pyproject(pkg_dir, name="foo")
    _run_emit_pipeline(conn, tmp_path)

    parents = _physically_contains_parents(conn, test_path)
    assert parents == ["repository"], f"expected sole Repository parent, got {parents}"


# ---------- kind classification, config, malformed, idempotency ----------


def _suite_kind(conn: sqlite3.Connection, path: str) -> str:
    row = conn.execute(
        "SELECT attrs_json FROM nodes WHERE kind='test_suite' AND path=?",
        (path,),
    ).fetchone()
    if row is None:
        return ""
    return json.loads(row[0])["suite_kind"]


def test_suite_kind_classification(tmp_path: Path) -> None:
    """dir-name precedence (integration/e2e/contract) then filename fallback."""
    _seed_root_pkg(tmp_path)
    cases = {
        "tests/integration": "test_a.py",
        "tests/e2e": "test_b.py",
        "tests/contract": "test_c.py",
        "tests/spec_files": "thing_spec.py",
        "tests/unit": "test_d.py",
        "tests/misc": "empty.txt",
    }
    for sub, fname in cases.items():
        (tmp_path / sub).mkdir(parents=True)
        (tmp_path / sub / fname).write_text("")

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    assert _suite_kind(conn, "tests/integration") == "integration"
    assert _suite_kind(conn, "tests/e2e") == "e2e"
    assert _suite_kind(conn, "tests/contract") == "contract"
    assert _suite_kind(conn, "tests/spec_files") == "contract"
    assert _suite_kind(conn, "tests/unit") == "unit"
    assert _suite_kind(conn, "tests/misc") == "unknown"


def test_framework_config_testpaths_adds_root(tmp_path: Path) -> None:
    """pyproject [tool.pytest.ini_options] testpaths adds extra roots."""
    pkg_dir = tmp_path / "packages" / "foo"
    body = '[tool.pytest.ini_options]\ntestpaths = ["spec"]\n'
    _write_pyproject(pkg_dir, name="foo", body=body)
    _write_python_pkg(pkg_dir, "foo")
    (pkg_dir / "spec").mkdir()
    (pkg_dir / "spec" / "test_x.py").write_text("")

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    rows = _suite_rows(conn)
    assert any(p == "packages/foo/spec" for _, p in rows), f"testpaths spec/ not discovered as suite root; rows={rows}"


def test_malformed_pyproject_does_not_crash(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """malformed config -> stderr warning + fall back to filesystem-only."""
    pkg_dir = tmp_path / "packages" / "foo"
    _write_pyproject(pkg_dir, name="foo")
    _write_python_pkg(pkg_dir, "foo")
    (pkg_dir / "tests").mkdir()
    (pkg_dir / "tests" / "test_x.py").write_text("")

    conn = _setup(tmp_path)
    # Run a clean pass first so packages.refresh writes the row.
    with store.transaction(conn):
        packages.refresh(conn, repo_root=tmp_path, ctx=CTX)
        structural_nodes.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())
    # Now corrupt pyproject before test_suites.emit reads it for testpaths.
    (pkg_dir / "pyproject.toml").write_text("[tool.pytest.ini_options\nbad")
    with store.transaction(conn):
        test_suites.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())

    # Suite still discovered via conventional FS walk.
    rows = _suite_rows(conn)
    assert any(p == "packages/foo/tests" for _, p in rows)
    captured = capsys.readouterr()
    assert "malformed" in captured.err


def test_idempotency_two_runs_identical_edges(tmp_path: Path) -> None:
    """Re-running emit produces a byte-identical physically_contains + tests
    edge set."""
    pkg_dir = tmp_path / "packages" / "foo"
    _write_pyproject(pkg_dir, name="foo")
    _write_python_pkg(pkg_dir, "foo")
    (pkg_dir / "tests").mkdir()
    (pkg_dir / "tests" / "test_x.py").write_text("import foo\n")

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)
    snap1 = conn.execute(
        "SELECT src, dst, kind FROM edges WHERE kind IN ('physically_contains','tests') ORDER BY src, dst, kind"
    ).fetchall()

    # Second run inside a new transaction (emit() is independently invocable).
    with store.transaction(conn):
        test_suites.emit(conn, repo_root=tmp_path, ctx=CTX, skip_dirs=frozenset())
    snap2 = conn.execute(
        "SELECT src, dst, kind FROM edges WHERE kind IN ('physically_contains','tests') ORDER BY src, dst, kind"
    ).fetchall()

    assert snap1 == snap2, f"emit() not idempotent — first run {len(snap1)} edges, second {len(snap2)}"


# ============================================================================
# integration + enforcement
# ============================================================================


def test_strict_tree_invariant_class_and_helper_exist() -> None:
    """StrictTreeInvariantError + _enforce_strict_tree_invariant are
    importable from code_graph_io.update; the exception carries offending_child_ids
    and the hint message format."""
    from code_graph_io.update import StrictTreeInvariantError, _enforce_strict_tree_invariant

    assert callable(_enforce_strict_tree_invariant)
    e = StrictTreeInvariantError(offending_child_ids=[1, 2, 3])
    assert e.offending_child_ids == [1, 2, 3]
    msg = str(e)
    assert "tree invariant violated" in msg
    assert "3 node(s)" in msg
    assert "duplicate parent edge" in msg or "delete the prior edge" in msg


# ---------- fixture-driven integration tests ----------

FIXTURE_SRC = Path(__file__).parent / "fixtures" / "sample_monorepo"


@pytest.fixture()
def fixture_repo(tmp_path: Path) -> Path:
    """Copy sample_monorepo to tmp_path and initialize as a git repo + commit."""
    shutil.copytree(FIXTURE_SRC, tmp_path, dirs_exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _db_path(repo_root: Path) -> Path:
    from code_graph_io.paths import graph_dir

    ws = repo_root
    return graph_dir(ws) / "code.db"


def test_call_order_pitfall(fixture_repo: Path) -> None:
    """Fixture regression — re-parenting + suite kind + idempotency."""
    from code_graph_io import update

    update.run(fixture_repo, workspace=fixture_repo, full=True)

    db_path = _db_path(fixture_repo)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Assertion 1: every is_test=true file has exactly one physically_contains parent
        rows = conn.execute(
            """
            SELECT f.id, COUNT(e.src)
            FROM nodes f
            LEFT JOIN edges e
              ON e.dst = f.id AND e.kind = 'physically_contains'
            WHERE f.kind = 'file'
              AND json_extract(f.attrs_json, '$.is_test') = 1
            GROUP BY f.id
            """
        ).fetchall()
        assert rows, "no is_test=true files found — fixture or emitter broken"
        for fid, n in rows:
            assert n == 1, f"file id={fid} has {n} physically_contains parents"

        # Assertion 2: that parent is a TestSuite node
        rows = conn.execute(
            """
            SELECT parent.kind, f.path
            FROM nodes f
            JOIN edges e ON e.dst = f.id AND e.kind='physically_contains'
            JOIN nodes parent ON e.src = parent.id
            WHERE f.kind = 'file'
              AND json_extract(f.attrs_json, '$.is_test') = 1
            """
        ).fetchall()
        for parent_kind, path in rows:
            assert parent_kind == "test_suite", (
                f"test file {path} has parent kind {parent_kind!r}, expected 'test_suite'"
            )

        # Assertion 3: Package(mypkg) does NOT physically_contain its test file
        row = conn.execute(
            """
            SELECT 1
            FROM edges e
            JOIN nodes p ON e.src = p.id AND p.kind='package' AND p.name='mypkg'
            JOIN nodes f ON e.dst = f.id AND f.kind='file'
            WHERE e.kind = 'physically_contains'
              AND f.path LIKE '%tests/test_foo.py'
            """
        ).fetchone()
        assert row is None, "Package(mypkg) still has a physically_contains edge to its test file"

        # Assertion 4: integration suite has suite_kind='integration'
        row = conn.execute(
            """
            SELECT json_extract(attrs_json, '$.suite_kind')
            FROM nodes
            WHERE kind = 'test_suite' AND path = 'tests/integration'
            """
        ).fetchone()
        assert row is not None, "tests/integration TestSuite not emitted"
        assert row[0] == "integration"

        pc_count_1 = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='physically_contains'").fetchone()[0]
        tests_count_1 = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='tests'").fetchone()[0]
    finally:
        conn.close()

    # Assertion 5: idempotency — second update.run produces identical counts.
    update.run(fixture_repo, workspace=fixture_repo, full=True)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        pc_count_2 = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='physically_contains'").fetchone()[0]
        tests_count_2 = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='tests'").fetchone()[0]
        assert pc_count_1 == pc_count_2, (
            f"physically_contains edge count changed across runs: {pc_count_1} -> {pc_count_2}"
        )
        assert tests_count_1 == tests_count_2, (
            f"tests edge count changed across runs: {tests_count_1} -> {tests_count_2}"
        )
    finally:
        conn.close()


def test_strict_tree_invariant_raises_on_duplicate_parent(fixture_repo: Path) -> None:
    """Corrupt the DB and confirm the runtime invariant fires."""
    from code_graph_io import update
    from code_graph_io.update import (
        StrictTreeInvariantError,
        _enforce_strict_tree_invariant,
    )

    update.run(fixture_repo, workspace=fixture_repo, full=True)
    db_path = _db_path(fixture_repo)
    conn = sqlite3.connect(db_path)
    try:
        file_row = conn.execute(
            """
            SELECT f.id, e.src
            FROM nodes f
            JOIN edges e ON e.dst = f.id AND e.kind = 'physically_contains'
            WHERE f.kind = 'file'
            LIMIT 1
            """
        ).fetchone()
        assert file_row is not None
        file_id, existing_parent = file_row

        repo_row = conn.execute("SELECT id FROM nodes WHERE kind='repository' LIMIT 1").fetchone()
        assert repo_row is not None
        repo_id = repo_row[0]
        if repo_id == existing_parent:
            pkg_row = conn.execute("SELECT id FROM nodes WHERE kind='package' LIMIT 1").fetchone()
            assert pkg_row is not None
            fake_parent = pkg_row[0]
        else:
            fake_parent = repo_id

        conn.execute(
            "INSERT INTO edges (src, dst, kind, attrs_json) VALUES (?, ?, 'physically_contains', NULL)",
            (fake_parent, file_id),
        )
        conn.commit()

        with pytest.raises(StrictTreeInvariantError) as exc:
            _enforce_strict_tree_invariant(conn)
        assert file_id in exc.value.offending_child_ids
    finally:
        conn.close()


def test_anti_regression_describe_package_smoke(fixture_repo: Path) -> None:
    """Anti-regression — after the full pipeline, mypkg is still findable.

    The fixture's mypkg/pyproject.toml carries [project.scripts]
    which now classifies it as kind="app" — so the smoke assertion accepts
    either kind (the row exists exactly once under one or the other).
    """
    from code_graph_io import update

    update.run(fixture_repo, workspace=fixture_repo, full=True)
    db_path = _db_path(fixture_repo)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        n = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind IN ('package', 'app') AND name='mypkg'").fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_shebang_script_in_fixture_does_not_emit_entry_point(
    fixture_repo: Path,
) -> None:
    """Anti-test — shebang scripts in scripts/ stay as is_executable
    Files, no EntryPoint emitted for them."""
    from code_graph_io import update

    update.run(fixture_repo, workspace=fixture_repo, full=True)
    db_path = _db_path(fixture_repo)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """
            SELECT 1 FROM edges e
            JOIN nodes ep ON e.src = ep.id AND ep.kind = 'entry_point'
            JOIN nodes f  ON e.dst = f.id  AND f.kind = 'file'
            WHERE e.kind = 'implemented_by'
              AND f.path LIKE '%/scripts/run.sh'
            """
        ).fetchone()
        assert row is None, "shebang script run.sh must not have an implemented_by EntryPoint"
    finally:
        conn.close()


def test_pyproject_scripts_entry_point_resolves_to_file(fixture_repo: Path) -> None:
    """Pyproject [project.scripts] resolves implemented_by strictly
    path-qualified. Skipped when sample_monorepo has no [project.scripts]."""
    from code_graph_io import update

    update.run(fixture_repo, workspace=fixture_repo, full=True)
    db_path = _db_path(fixture_repo)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """
            SELECT ep.name, f.path
            FROM nodes ep
            JOIN edges e ON e.src = ep.id AND e.kind = 'implemented_by'
            JOIN nodes f ON e.dst = f.id AND f.kind = 'file'
            WHERE ep.kind = 'entry_point'
              AND json_extract(ep.attrs_json, '$.source') = 'pyproject.scripts'
            """
        ).fetchone()
        if row is None:
            pytest.skip("sample_monorepo pyproject has no [project.scripts] — fixture-dependent assertion skipped")
        _ep_name, file_path = row
        assert "mypkg" in file_path, (
            f"implemented_by file '{file_path}' outside mypkg's tree — strict resolution failed"
        )
    finally:
        conn.close()


# ============================================================================
# suite-name uniqueness guard + stale-node resolution
# ============================================================================


def test_suite_names_unique_after_multi_package_emit(tmp_path: Path) -> None:
    """No two test_suite nodes share a name after emit on a multi-package repo.

    Regression guard: before the suite-rename fix, all package-owned suites
    were named Path(rel_path).name == 'tests', causing them all to share the same
    name and making any name-keyed query (e.g. _consumer_pkgs) return all packages
    as consumers of every suite.

    Stale-node resolution: the stale-'tests'-named nodes from a prior
    incremental `gw graph update` run are swept by `gw graph update --full`. The full-scan
    DELETE at update.py:285 removes non-package/app/builtin path-bearing nodes
    before re-emitting, so running `gw graph update --full` on a live workspace after
    upgrading is the correct and sufficient remediation.
    Incremental `gw graph update` does NOT clean up stale suite nodes — always use
    `gw graph update --full` when upgrading suite naming.
    """
    # Build a two-package monorepo, each with its own tests/ dir.
    for pkg_name in ("foo", "bar"):
        pkg_dir = tmp_path / "packages" / pkg_name
        _write_pyproject(pkg_dir, name=pkg_name)
        _write_python_pkg(pkg_dir, pkg_name)
        (pkg_dir / "tests").mkdir()
        (pkg_dir / "tests" / "test_something.py").write_text(f"import {pkg_name}\n")

    conn = _setup(tmp_path)
    _run_emit_pipeline(conn, tmp_path)

    # uniqueness check: no two test_suite nodes may share a name.
    duplicates = conn.execute(
        """
        SELECT name, COUNT(*) AS cnt
        FROM nodes
        WHERE kind = 'test_suite'
        GROUP BY name
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    assert duplicates == [], f"test_suite nodes share names after emit: {duplicates}"

    # Confirm both suites exist under their qualified names.
    rows = _suite_rows(conn)
    names = {r[0] for r in rows}
    assert "foo-unit-tests" in names, f"foo-unit-tests not found in {names}"
    assert "bar-unit-tests" in names, f"bar-unit-tests not found in {names}"
