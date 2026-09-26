"""The affects closure: four tiers over repo-qualified URIs, degrading with warnings."""

from __future__ import annotations

from pathlib import Path

import pytest
from code_graph_io import GraphReader
from code_graph_io import testing as gtesting
from graph_works_core.guidance import closure as cl
from graph_works_core.guidance.claims import ClaimRow

R, UI = "repo:o/r", "repo:o/ui"


def _seed(db: Path) -> GraphReader:
    store = gtesting.open_store(db, create=True)
    c = store._conn
    c.execute("BEGIN")
    nodes = [
        (1, "repository", "r", "", "repo:o/r", R),
        (2, "repository", "ui", "", "repo:o/ui", UI),
        (10, "package", "okf-io", "packages/okf-io", "pkg:o/r/okf-io", R),
        (11, "package", "okf-ext", "packages/okf-ext", "pkg:o/r/okf-ext", R),
        (12, "app", "okf-ext", "packages/okf-ext", "app:o/r/okf-ext", R),
        (13, "package", "okf-io-extra", "packages/okf-io-extra", "pkg:o/r/okf-io-extra", R),
        (14, "package", "okf-io", "packages/okf-io", "pkg:o/ui/okf-io", UI),  # same name, other repo
        (20, "file", "README.md", "README.md", "file:o/r/README.md", R),
        (21, "file", "README.md", "README.md", "file:o/ui/README.md", UI),
        (22, "file", "bundle.py", "packages/okf-io/src/bundle.py", "file:o/r/packages/okf-io/src/bundle.py", R),
        (23, "file", "ext.py", "packages/okf-ext/src/ext.py", "file:o/r/packages/okf-ext/src/ext.py", R),
        (24, "file", "x.py", "packages/okf-io-extra/x.py", "file:o/r/packages/okf-io-extra/x.py", R),
        (30, "dependency", "ruamel", None, "dependency:o/r/pypi/ruamel", R),
        # Same-named packages in two repos, only one of which has a dependency
        # edge: `pkg:o/r/core`'s tier-2 walk must not pick up `pkg:o/ui/core`'s
        # dependency via a name-keyed lookup.
        (40, "package", "core", "packages/core", "pkg:o/r/core", R),
        (41, "package", "proto", "packages/proto", "pkg:o/r/proto", R),
        (42, "package", "core", "packages/core", "pkg:o/ui/core", UI),
        (43, "package", "proto", "packages/proto", "pkg:o/ui/proto", UI),
        # An agent plugin with a test suite beneath it, and a test suite inside a package.
        (50, "agent_plugin", "gw", "plugins/gw", "agent_plugin:o/r/gw", R),
        (51, "test_suite", "gw-tests", "plugins/gw/tests", "test_suite:o/r/plugins/gw/tests", R),
        (52, "test_suite", "okf-io-tests", "packages/okf-io/tests", "test_suite:o/r/packages/okf-io/tests", R),
        (53, "agent_plugin", "gw", "plugins/gw", "agent_plugin:o/ui/gw", UI),  # other repo
        (60, "file", "SKILL.md", "plugins/gw/skills/SKILL.md", "file:o/r/plugins/gw/skills/SKILL.md", R),
        (61, "file", "t.sh", "plugins/gw/tests/t.sh", "file:o/r/plugins/gw/tests/t.sh", R),
        (
            62,
            "file",
            "test_bundle.py",
            "packages/okf-io/tests/test_bundle.py",
            "file:o/r/packages/okf-io/tests/test_bundle.py",
            R,
        ),
    ]
    c.executemany("INSERT INTO nodes (id, kind, name, path, uri, repo) VALUES (?,?,?,?,?,?)", nodes)
    c.executemany(
        "INSERT INTO edges (src, dst, kind) VALUES (?,?,?)",
        [
            (11, 10, "depends_on_package"),
            (10, 30, "used_by"),
            (42, 43, "depends_on_package"),  # only ui/core -> ui/proto; r/core has no dependencies
        ],
    )
    c.commit()
    return store


@pytest.fixture
def reader(tmp_path: Path):
    r = _seed(tmp_path / "code.db")
    yield r
    r.close()


def _tiers(closure: cl.Closure) -> dict[str, int]:
    return {entry.uri: entry.tier for entry in closure.entries}


def test_file_path_admits_file_package_neighbours_and_repo(reader: GraphReader) -> None:
    closure = cl.affects_closure(reader, repo="r", affects=["packages/okf-io/src/bundle.py"])
    assert _tiers(closure) == {
        "file:o/r/packages/okf-io/src/bundle.py": 0,
        "pkg:o/r/okf-io": 1,
        "dependency:o/r/pypi/ruamel": 2,
        "repo:o/r": 3,
    }
    assert closure.warnings == ()
    assert [e.tier for e in closure.entries] == sorted(e.tier for e in closure.entries)


def test_package_directory_admits_both_pkg_and_app_and_internal_deps(reader: GraphReader) -> None:
    closure = cl.affects_closure(reader, repo="r", affects=["packages/okf-ext"])
    tiers = _tiers(closure)
    assert tiers["file:o/r/packages/okf-ext/src/ext.py"] == 0
    assert tiers["pkg:o/r/okf-ext"] == 1
    assert tiers["app:o/r/okf-ext"] == 1
    assert tiers["pkg:o/r/okf-io"] == 2  # internal dependency, same repo only
    assert "pkg:o/ui/okf-io" not in tiers


@pytest.mark.parametrize("spelling", ["packages/okf-io/", "./packages/okf-io", "packages/okf-io"])
def test_path_spellings_are_normalised(reader: GraphReader, spelling: str) -> None:
    closure = cl.affects_closure(reader, repo="r", affects=[spelling])
    assert "file:o/r/packages/okf-io/src/bundle.py" in _tiers(closure)


def test_string_prefix_is_not_a_directory_prefix(reader: GraphReader) -> None:
    tiers = _tiers(cl.affects_closure(reader, repo="r", affects=["packages/okf-io"]))
    assert "file:o/r/packages/okf-io-extra/x.py" not in tiers
    assert "pkg:o/r/okf-io-extra" not in tiers


def test_shared_file_path_is_scoped_to_the_items_repository(reader: GraphReader) -> None:
    tiers = _tiers(cl.affects_closure(reader, repo="r", affects=["README.md"]))
    assert tiers["file:o/r/README.md"] == 0
    assert "file:o/ui/README.md" not in tiers


def test_most_specific_tier_wins(reader: GraphReader) -> None:
    # okf-io is tier 1 (its own file) and tier 2 (okf-ext's internal dep): tier 1 wins.
    closure = cl.affects_closure(reader, repo="r", affects=["packages/okf-io/src/bundle.py", "packages/okf-ext"])
    entry = closure.uris()["pkg:o/r/okf-io"]
    assert entry.tier == 1
    assert "packages/okf-io" in entry.why


def test_empty_affects_is_repo_only(reader: GraphReader) -> None:
    assert _tiers(cl.affects_closure(reader, repo="r", affects=[])) == {"repo:o/r": 3}


def test_unmatched_path_warns_and_contributes_nothing(reader: GraphReader) -> None:
    closure = cl.affects_closure(reader, repo="r", affects=["nope/"])
    assert _tiers(closure) == {"repo:o/r": 3}
    assert closure.warnings == (cl.WARN_UNMATCHED_PATH.format(path="nope/"),)  # the authored spelling


@pytest.mark.parametrize("spelling", ["./", "/"])
def test_empty_normalised_path_is_unmatched(tmp_path: Path, spelling: str) -> None:
    """Both spellings normalise to "" -- treated as unmatched, never "the whole repo".

    The graph carries a root-path package (`path=''`), which an empty affects
    path would match exactly (and own every file under) if it were admitted.
    """
    store = _seed(tmp_path / "code.db")
    try:
        store._conn.execute(
            "INSERT INTO nodes (id, kind, name, path, uri, repo) VALUES (90,'package','root','','pkg:o/r/root',?)",
            (R,),
        )
        store._conn.commit()
        closure = cl.affects_closure(store, repo="r", affects=[spelling])
    finally:
        store.close()
    assert _tiers(closure) == {"repo:o/r": 3}
    assert closure.warnings == (cl.WARN_UNMATCHED_PATH.format(path=spelling),)


def test_file_under_a_plugin_directory_admits_the_agent_plugin(reader: GraphReader) -> None:
    tiers = _tiers(cl.affects_closure(reader, repo="r", affects=["plugins/gw/skills/SKILL.md"]))
    assert tiers == {"file:o/r/plugins/gw/skills/SKILL.md": 0, "agent_plugin:o/r/gw": 1, "repo:o/r": 3}


def test_plugin_directory_path_admits_the_plugin_and_its_test_suite(reader: GraphReader) -> None:
    tiers = _tiers(cl.affects_closure(reader, repo="r", affects=["plugins/gw"]))
    assert tiers["agent_plugin:o/r/gw"] == 1
    assert tiers["test_suite:o/r/plugins/gw/tests"] == 1  # owns plugins/gw/tests/t.sh
    assert "agent_plugin:o/ui/gw" not in tiers


def test_exact_owner_directory_admits_a_node_that_owns_no_file(tmp_path: Path) -> None:
    store = _seed(tmp_path / "code.db")
    try:
        store._conn.execute(
            "INSERT INTO nodes (id, kind, name, path, uri, repo) VALUES "
            "(91,'test_suite','empty','packages/empty/tests','test_suite:o/r/packages/empty/tests',?)",
            (R,),
        )
        store._conn.commit()
        closure = cl.affects_closure(store, repo="r", affects=["packages/empty/tests/"])
    finally:
        store.close()
    entry = closure.uris()["test_suite:o/r/packages/empty/tests"]
    assert (entry.tier, closure.warnings) == (1, ())
    assert "packages/empty/tests" in entry.why


def test_file_in_a_test_suite_inside_a_package_admits_both_at_tier_1(reader: GraphReader) -> None:
    """Owner kinds contest the longest prefix separately: the suite does not steal the package."""
    tiers = _tiers(cl.affects_closure(reader, repo="r", affects=["packages/okf-io/tests/test_bundle.py"]))
    assert tiers["file:o/r/packages/okf-io/tests/test_bundle.py"] == 0
    assert tiers["pkg:o/r/okf-io"] == 1
    assert tiers["test_suite:o/r/packages/okf-io/tests"] == 1
    assert tiers["dependency:o/r/pypi/ruamel"] == 2  # tier-2 neighbours still come from the package


def test_tier2_internal_dependency_does_not_leak_across_repos(reader: GraphReader) -> None:
    """Regression: a same-named sibling package in another repo (`ui/core` ->
    `ui/proto`) has a dependency edge that `r/core` lacks of its own. A
    name-keyed lookup of `r/core`'s internal dependencies would incorrectly
    pick up `ui/proto`'s name-mate `r/proto`. It must not.
    """
    closure = cl.affects_closure(reader, repo="r", affects=["packages/core"])
    tiers = _tiers(closure)
    assert tiers["pkg:o/r/core"] == 1
    assert "pkg:o/r/proto" not in tiers


@pytest.mark.parametrize(
    ("repo", "warning"),
    [(None, cl.WARN_NO_REPO), ("ghost", cl.WARN_UNKNOWN_REPO.format(repo="ghost"))],
)
def test_repo_degradation_is_an_empty_closure(reader: GraphReader, repo: str | None, warning: str) -> None:
    closure = cl.affects_closure(reader, repo=repo, affects=["README.md"])
    assert (closure.entries, closure.warnings) == ((), (warning,))


def test_no_graph_is_an_empty_closure() -> None:
    closure = cl.affects_closure(None, repo="r", affects=["README.md"])
    assert (closure.entries, closure.warnings) == ((), (cl.WARN_NO_GRAPH,))


def _row(page: str, row_id: str, about: tuple[str, ...], superseded: bool = False) -> ClaimRow:
    return ClaimRow(page, row_id, "claim", "x", about, (), ("plan",), "authored", None, superseded, None, 1)


def test_match_claims_orders_by_tier_page_id_and_drops_superseded(reader: GraphReader) -> None:
    closure = cl.affects_closure(reader, repo="r", affects=["packages/okf-io/src/bundle.py"])
    rows = [
        _row("z/page", "C1", ("repo:o/r",)),
        _row("b/page", "C2", ("pkg:o/r/okf-io", "repo:o/r")),
        _row("a/page", "C1", ("pkg:o/r/okf-io",)),
        _row("a/page", "C0", ("file:o/r/packages/okf-io/src/bundle.py",)),
        _row("a/old", "C1", ("pkg:o/r/okf-io",), superseded=True),
        _row("a/other", "C1", ("pkg:o/ui/okf-io",)),
    ]
    matched = cl.match_claims(rows, closure)
    assert [(m.tier, m.row.page, m.row.id) for m in matched] == [
        (0, "a/page", "C0"),
        (1, "a/page", "C1"),
        (1, "b/page", "C2"),
        (3, "z/page", "C1"),
    ]
    assert matched[2].uri == "pkg:o/r/okf-io"
    with_old = cl.match_claims(rows, closure, include_superseded=True)
    assert ("a/old", "C1") in [(m.row.page, m.row.id) for m in with_old]
