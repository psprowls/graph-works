"""Layout-level claims entry points: bundle loading, graph lifecycle, item resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from code_graph_io import open_writer
from graph_works_core.graph.commands import graph_target
from graph_works_core.guidance import closure as cl
from graph_works_core.guidance import commands as gc
from graph_works_core.workspace.errors import WorkspaceConfigError
from graph_works_core.workspace.layout import WorkspaceLayout, layout_for

CLAIM_PAGE = """\
---
type: Explanation
title: E
status: stable
claims:
  - id: C1
    claim: The repo claim.
    about: [repo:o/r]
---
"""

ITEM = """\
---
type: Feature
title: T
description: d
status: draft
work_status: open
opened: 2026-09-24
updated: 2026-09-24
repo: r
affects: [README.md]
---
"""


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    # Same constructor tests/events/test_rules.py uses. No workspace.yaml, so
    # graph_target falls back to code_graph_io.paths.graph_dir(layout.root).
    # commands.py calls okf_io.load_bundle directly, which raises for a
    # missing root -- the fixture creates the (empty) bundle directory so
    # every test runs against a bundle that exists, never one that doesn't.
    built = layout_for(tmp_path / "ws")
    built.bundle_dir.mkdir(parents=True, exist_ok=True)
    return built


def _graph(layout: WorkspaceLayout, *sql: str) -> None:
    store = open_writer(graph_dir=graph_target(layout).graph_dir, create=True)
    try:
        for statement in sql:
            store._conn.execute(statement)
        store._conn.commit()
    finally:
        store.close()


def _write(layout: WorkspaceLayout, concept_id: str, text: str) -> None:
    target = layout.bundle_dir / f"{concept_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def test_refresh_then_force(layout: WorkspaceLayout) -> None:
    _write(layout, "explanations/e", CLAIM_PAGE)
    assert gc.run_claims_refresh(layout).reason == "no-manifest"
    assert gc.run_claims_refresh(layout).reason is None
    assert gc.run_claims_refresh(layout, force=True).reason == "forced"
    assert (layout.cache_dir / "claims" / "manifest.json").is_file()


def test_show_filters_by_exact_uri(layout: WorkspaceLayout) -> None:
    _write(layout, "explanations/e", CLAIM_PAGE)
    assert [r.id for r in gc.run_claims_show(layout, "repo:o/r").rows] == ["C1"]
    assert gc.run_claims_show(layout, "repo:o/other").rows == ()


def test_closure_unknown_item_is_a_refusal(layout: WorkspaceLayout) -> None:
    run = gc.run_claims_closure(layout, "work/nope")
    assert run.refusal == "unknown-item"
    assert run.matched == ()


def test_closure_unreadable_item_is_a_refusal_with_detail(layout: WorkspaceLayout) -> None:
    target = layout.bundle_dir / "work/feature-bad.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"---\ntitle: \xff\xfe not valid utf-8\n---\n")
    run = gc.run_claims_closure(layout, "work/feature-bad")
    assert run.refusal == "unreadable"
    assert run.detail is not None
    assert run.matched == ()


def test_closure_without_a_graph_warns_and_matches_nothing(layout: WorkspaceLayout) -> None:
    _write(layout, "explanations/e", CLAIM_PAGE)
    _write(layout, "work/feature-t", ITEM)
    run = gc.run_claims_closure(layout, "work/feature-t")
    assert run.refusal is None
    assert (run.repo, run.affects) == ("r", ("README.md",))
    assert run.closure.warnings == (cl.WARN_NO_GRAPH,)
    assert (run.matched, run.total_tokens) == ((), 0)


def test_closure_with_a_graph_matches_repo_claims(layout: WorkspaceLayout) -> None:
    _graph(
        layout,
        "INSERT INTO nodes (id, kind, name, path, uri, repo) VALUES "
        "(1,'repository','r','','repo:o/r','repo:o/r'),"
        "(2,'file','README.md','README.md','file:o/r/README.md','repo:o/r')",
    )
    _write(layout, "explanations/e", CLAIM_PAGE)
    _write(layout, "work/feature-t", ITEM)
    run = gc.run_claims_closure(layout, "work/feature-t")
    assert [(m.tier, m.row.id) for m in run.matched] == [(3, "C1")]
    assert run.total_tokens == run.matched[0].row.tokens


def test_open_closure_treats_a_zero_node_graph_as_no_graph(layout: WorkspaceLayout) -> None:
    _graph(layout)
    assert gc.open_closure(layout, repo="r", affects=[]).warnings == (cl.WARN_NO_GRAPH,)


GRAPH_R = (
    "INSERT INTO nodes (id, kind, name, path, uri, repo) VALUES "
    "(1,'repository','r','','repo:o/r','repo:o/r'),"
    "(2,'file','README.md','README.md','file:o/r/README.md','repo:o/r')"
)

EPIC = """\
---
type: Epic
title: E
description: d
status: draft
work_status: open
opened: 2026-09-24
updated: 2026-09-24
repo: r
---
"""

CHILD = """\
---
type: Feature
title: C
description: d
status: draft
work_status: open
opened: 2026-09-24
updated: 2026-09-24
affects: [README.md]
---
"""


def _declare(layout: WorkspaceLayout, *names: str) -> None:
    body = "".join(f'  {name}:\n    path: "{layout.root.parent / name}"\n' for name in names)
    for name in names:
        (layout.root.parent / name).mkdir(parents=True, exist_ok=True)
    layout.manifest_path.write_text(f"version: 1\nrepositories:\n{body}", encoding="utf-8", newline="\n")


def test_closure_child_inherits_repo_from_its_parent_epic(layout: WorkspaceLayout) -> None:
    _graph(layout, GRAPH_R)
    _write(layout, "explanations/e", CLAIM_PAGE)
    _write(layout, "work/epic-e", EPIC)
    _write(layout, "work/epic-e/children/feature-c", CHILD)
    run = gc.run_claims_closure(layout, "work/epic-e/children/feature-c")
    assert run.repo == "r"
    assert run.closure.uris().keys() == {"file:o/r/README.md", "repo:o/r"}
    assert [(m.tier, m.row.id) for m in run.matched] == [(3, "C1")]


def test_closure_item_without_repo_resolves_the_sole_declared_repository(layout: WorkspaceLayout) -> None:
    _declare(layout, "r")
    _graph(layout, GRAPH_R)
    _write(layout, "work/feature-c", CHILD)
    run = gc.run_claims_closure(layout, "work/feature-c")
    assert run.repo == "r"
    assert run.closure.warnings == ()
    assert "file:o/r/README.md" in run.closure.uris()


def test_closure_item_without_repo_among_several_declared_degrades_to_a_warning(layout: WorkspaceLayout) -> None:
    _declare(layout, "r", "ui")
    _graph(layout, GRAPH_R)
    _write(layout, "work/feature-c", CHILD)
    run = gc.run_claims_closure(layout, "work/feature-c")
    assert run.repo is None
    assert run.closure.entries == ()
    assert run.closure.warnings == (gc.WARN_AMBIGUOUS_REPO.format(count=2, names="r, ui"), cl.WARN_NO_REPO)


def test_closure_item_without_repo_and_no_declared_repository_is_no_repo(layout: WorkspaceLayout) -> None:
    _graph(layout, GRAPH_R)
    _write(layout, "work/feature-c", CHILD)
    run = gc.run_claims_closure(layout, "work/feature-c")
    assert (run.repo, run.closure.warnings) == (None, (cl.WARN_NO_REPO,))


def test_closure_malformed_workspace_yaml_propagates_as_config_error(layout: WorkspaceLayout) -> None:
    layout.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    layout.manifest_path.write_text("version: 1\nrepositories: [nope\n", encoding="utf-8", newline="\n")
    _write(layout, "work/feature-c", CHILD)
    with pytest.raises(WorkspaceConfigError):
        gc.run_claims_closure(layout, "work/feature-c")
