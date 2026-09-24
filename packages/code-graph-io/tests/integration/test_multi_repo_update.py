"""Multi-repo workspace update integration tests.

# integration-gate-allow
These tests do NOT touch any external network service (no Bedrock, no API) —
they build tmp_path git repos and drive code-graph-io's update pipeline against a
real sqlite DB. They are <2s and safe to run on every PR, so they carry the
`# integration-gate-allow` marker instead of the canonical
GRAPH_WIKI_RUN_INTEGRATION env gate. They keep the
`pytest.mark.integration` marker so the default `-m "not integration"` run
still skips them; opt in with `-m integration`.
"""

import subprocess
from pathlib import Path

import pytest
from code_graph_io import store, update
from code_graph_io.paths import graph_dir
from code_graph_io.uri import RepoContext, repo_uri

pytestmark = pytest.mark.integration


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _mk_py_repo(root: Path, name: str, pkg_name: str, dep: str | None = None):
    d = root / name
    (d / "src" / pkg_name).mkdir(parents=True)
    deps = f'dependencies = ["{dep}"]\n' if dep else "dependencies = []\n"
    (d / "pyproject.toml").write_text(f'[project]\nname = "{pkg_name}"\nversion = "0.1.0"\n{deps}')
    (d / "src" / pkg_name / "__init__.py").write_text("X = 1\n")
    _git(["init", "-q"], d)
    _git(["add", "-A"], d)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], d)
    return d


def test_two_members_two_repositories_and_scoped_repo_column(tmp_path):
    root = tmp_path / "mono"
    root.mkdir()
    ws = root / "workspace"
    ws.mkdir()
    (ws / ".agent-workspace.yaml").write_text("version: 2\nmulti-repo: true\n")
    a = _mk_py_repo(root, "alpha", "alpha")
    b = _mk_py_repo(root, "beta", "beta")

    update.run_workspace([a, b], graph_dir=graph_dir(ws), full=True)

    conn = store.read_only_connect(graph_dir(ws) / "code.db")
    repos = conn.execute("SELECT name FROM nodes WHERE kind='repository' ORDER BY name").fetchall()
    assert [r[0] for r in repos] == ["alpha", "beta"]
    nulls = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='file' AND repo IS NULL").fetchone()[0]
    assert nulls == 0
    a_uri = repo_uri(RepoContext(org="local", repo="alpha"))
    a_files = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='file' AND repo=?", (a_uri,)).fetchone()[0]
    assert a_files >= 1


def test_full_rebuild_of_one_member_keeps_other(tmp_path):
    root = tmp_path / "mono"
    root.mkdir()
    ws = root / "workspace"
    ws.mkdir()
    (ws / ".agent-workspace.yaml").write_text("version: 2\nmulti-repo: true\n")
    a = _mk_py_repo(root, "alpha", "alpha")
    b = _mk_py_repo(root, "beta", "beta")
    update.run_workspace([a, b], graph_dir=graph_dir(ws), full=True)

    update.run_workspace([a], graph_dir=graph_dir(ws), full=True)
    conn = store.read_only_connect(graph_dir(ws) / "code.db")
    beta = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='repository' AND name='beta'").fetchone()[0]
    assert beta == 1


def test_colliding_relpaths_and_pkg_name_stay_distinct(tmp_path):
    """Two sibling repos sharing a package name AND relative file paths
    (`pyproject.toml`, `src/shared/__init__.py`) must NOT merge into one node.

    This is the regression guard for the connection-scoped `upsert` identity:
    without it the shared `(kind, name, path)` keys collide into single rows,
    giving e.g. `package:shared` two `physically_contains` parents and raising
    `StrictTreeInvariantError`. With per-member repo scoping each repo gets its
    own distinct nodes.
    """
    root = tmp_path / "mono"
    root.mkdir()
    ws = root / "workspace"
    ws.mkdir()
    (ws / ".agent-workspace.yaml").write_text("version: 2\nmulti-repo: true\n")
    # Same package name "shared" + identical relative paths in both repos; only
    # the repo dir name (-> repo URI) differs.
    a = _mk_py_repo(root, "alpha", "shared")
    b = _mk_py_repo(root, "beta", "shared")

    # Must not raise StrictTreeInvariantError.
    update.run_workspace([a, b], graph_dir=graph_dir(ws), full=True)

    conn = store.read_only_connect(graph_dir(ws) / "code.db")
    a_uri = repo_uri(RepoContext(org="local", repo="alpha"))
    b_uri = repo_uri(RepoContext(org="local", repo="beta"))

    # The shared package name is two DISTINCT rows, one per repo.
    pkg_repos = conn.execute("SELECT repo FROM nodes WHERE kind='package' AND name='shared' ORDER BY repo").fetchall()
    assert [r[0] for r in pkg_repos] == [a_uri, b_uri]

    # The colliding relative file path materializes once per repo.
    init_rel = "src/shared/__init__.py"
    init_repos = conn.execute(
        "SELECT repo FROM nodes WHERE kind='file' AND path=? ORDER BY repo",
        (init_rel,),
    ).fetchall()
    assert [r[0] for r in init_repos] == [a_uri, b_uri]

    # No file node leaked unstamped, and no physically_contains child has >1 parent
    # (the invariant the scoping protects — re-checked here explicitly).
    multi_parent = conn.execute(
        "SELECT dst, COUNT(*) FROM edges WHERE kind='physically_contains' GROUP BY dst HAVING COUNT(*) > 1"
    ).fetchall()
    assert multi_parent == []


def _mk_js_repo_with_main(root: Path, name: str, pkg_name: str):
    """A ROOT JS package (package.json at repo root) declaring a `main` entry
    point. Stays kind=package (no `bin` → not an app)."""
    import json as _json

    d = root / name
    (d / "src").mkdir(parents=True)
    (d / "package.json").write_text(_json.dumps({"name": pkg_name, "version": "0.1.0", "main": "src/index.js"}))
    (d / "src" / "index.js").write_text("module.exports = {};\n")
    _git(["init", "-q"], d)
    _git(["add", "-A"], d)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], d)
    return d


def test_root_package_entry_point_has_no_empty_uri_duplicate(tmp_path):
    """A ROOT package that declares an entry point must NOT spawn a duplicate,
    empty-uri package node.

    Regression guard for the entry_points emitter coercing a root package's
    rel-path `''` to `None`: the `declares_entry_point` edge's src key then
    mismatched `packages.refresh`'s canonical `path=''` node identity, stubbing
    a second uri-less `package` node. Benign in single-repo (index `_repo_for`
    falls back to repos[0]) but fatal in multi-repo (StickerGiant TS monorepo
    e2e gate). Uses two members so the empty-uri stub survives the global
    sweep, mirroring the real multi-repo failure.
    """
    root = tmp_path / "mono"
    root.mkdir()
    ws = root / "workspace"
    ws.mkdir()
    (ws / ".agent-workspace.yaml").write_text("version: 2\nmulti-repo: true\n")
    a = _mk_js_repo_with_main(root, "common", "@sg/frontend-common")
    _mk_js_repo_with_main(root, "other", "@sg/other")

    update.run_workspace([a, root / "other"], graph_dir=graph_dir(ws), full=True)

    conn = store.read_only_connect(graph_dir(ws) / "code.db")

    # Exactly ONE package node named '@sg/frontend-common' — no empty-uri stub.
    rows = conn.execute("SELECT uri FROM nodes WHERE kind='package' AND name='@sg/frontend-common'").fetchall()
    assert len(rows) == 1, f"expected 1 package node, got {len(rows)}: {rows}"
    (pkg_uri_val,) = rows[0]
    assert pkg_uri_val, f"package node has empty uri: {pkg_uri_val!r}"

    # The declares_entry_point edge resolves to that same uri-bearing node.
    src_rows = conn.execute(
        "SELECT s.uri FROM edges e "
        "JOIN nodes s ON s.id = e.src "
        "JOIN nodes ep ON ep.id = e.dst "
        "WHERE e.kind='declares_entry_point' AND s.kind='package' AND s.name='@sg/frontend-common'"
    ).fetchall()
    assert src_rows, "no declares_entry_point edge from package '@sg/frontend-common'"
    for (src_uri,) in src_rows:
        assert src_uri == pkg_uri_val, f"edge src uri {src_uri!r} != canonical {pkg_uri_val!r}"


def test_shared_external_dependency_is_one_node_per_repository(tmp_path):
    """Two sibling repos that both declare the same external dependency get one
    repo-stamped, uri-bearing node each (D-004). No `used_by` edge crosses
    repositories, and no empty-uri stub leaks.
    """
    root = tmp_path / "mono"
    root.mkdir()
    ws = root / "workspace"
    ws.mkdir()
    (ws / ".agent-workspace.yaml").write_text("version: 2\nmulti-repo: true\n")
    # "requests" is NOT a workspace package -> a real external `dependency` node.
    a = _mk_py_repo(root, "alpha", "alpha", dep="requests")
    b = _mk_py_repo(root, "beta", "beta", dep="requests")

    update.run_workspace([a, b], graph_dir=graph_dir(ws), full=True)

    conn = store.read_only_connect(graph_dir(ws) / "code.db")

    dep_rows = conn.execute(
        "SELECT repo, uri, attrs_json FROM nodes WHERE kind='dependency' AND name='requests' ORDER BY repo"
    ).fetchall()
    assert [(repo, uri) for repo, uri, _attrs in dep_rows] == [
        ("repo:local/alpha", "dependency:local/alpha/pypi/requests"),
        ("repo:local/beta", "dependency:local/beta/pypi/requests"),
    ]
    empty = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='dependency' AND (uri IS NULL OR uri='')").fetchone()[0]
    assert empty == 0
    edges = conn.execute(
        "SELECT s.repo, d.repo FROM edges e JOIN nodes s ON s.id = e.src JOIN nodes d ON d.id = e.dst "
        "WHERE e.kind='used_by' AND d.kind='dependency'"
    ).fetchall()
    assert edges and all(src_repo == dst_repo for src_repo, dst_repo in edges)


def test_cross_repo_depends_on_package(tmp_path):
    root = tmp_path / "mono"
    root.mkdir()
    ws = root / "workspace"
    ws.mkdir()
    (ws / ".agent-workspace.yaml").write_text("version: 2\nmulti-repo: true\n")
    a = _mk_py_repo(root, "alpha", "alpha")
    b = _mk_py_repo(root, "beta", "beta", dep="alpha")
    update.run_workspace([a, b], graph_dir=graph_dir(ws), full=True)

    conn = store.read_only_connect(graph_dir(ws) / "code.db")
    ext = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE kind='dependency' AND uri='dependency:local/beta/pypi/alpha'"
    ).fetchone()[0]
    assert ext == 1
    rows = conn.execute(
        """
        SELECT s.name, d.name FROM edges e
        JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst
        WHERE e.kind='depends_on_package'
        """
    ).fetchall()
    assert ("beta", "alpha") in rows


def test_two_workspace_implementations_share_a_dependency_facet(tmp_path):
    """A consumer links to every repository implementation of its dependency."""
    root = tmp_path / "mono"
    root.mkdir()
    ws = root / "workspace"
    ws.mkdir()
    (ws / ".agent-workspace.yaml").write_text("version: 2\nmulti-repo: true\n")
    a = _mk_py_repo(root, "alpha", "shared")
    b = _mk_py_repo(root, "beta", "shared")
    c = _mk_py_repo(root, "consumer", "consumer", dep="shared")

    update.run_workspace([a, b, c], graph_dir=graph_dir(ws), full=True)

    conn = store.read_only_connect(graph_dir(ws) / "code.db")
    try:
        deps = conn.execute(
            "SELECT uri FROM nodes WHERE kind='dependency' AND uri LIKE 'dependency:%/pypi/shared' ORDER BY uri"
        ).fetchall()
        implementations = conn.execute(
            "SELECT dst.uri FROM edges JOIN nodes src ON src.id=edges.src JOIN nodes dst ON dst.id=edges.dst "
            "WHERE src.uri='dependency:local/consumer/pypi/shared' AND edges.kind='implemented_by' ORDER BY dst.uri"
        ).fetchall()
        direct = conn.execute(
            "SELECT dst.uri FROM edges JOIN nodes src ON src.id=edges.src JOIN nodes dst ON dst.id=edges.dst "
            "WHERE src.name='consumer' AND edges.kind='depends_on_package' ORDER BY dst.uri"
        ).fetchall()
        assert deps == [("dependency:local/consumer/pypi/shared",)]
        assert implementations == [("pkg:local/alpha/shared",), ("pkg:local/beta/shared",)]
        assert direct == [("pkg:local/alpha/shared",), ("pkg:local/beta/shared",)]
    finally:
        conn.close()
