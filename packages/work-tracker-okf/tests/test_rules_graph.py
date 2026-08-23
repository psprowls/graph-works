from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from okf_io import load
from work_helpers import lane_report, make_item, write_item
from work_tracker_okf._rules.graph import _phase_dependency_graph
from work_tracker_okf.dependencies import DependencyEdge

TODAY = date(2026, 8, 3)


def codes_for(root: Path) -> set[str]:
    return {finding.code for finding in lane_report(root, today=TODAY).findings if finding.code.startswith("graph.")}


def paths_for(root: Path, code: str) -> set[str]:
    return {finding.path or "" for finding in lane_report(root, today=TODAY).by_code(code)}


def _edge(path: str, *, blocks: str = "execute", needs: str = "resolved") -> str:
    return f"depends_on:\n  - path: {path}\n    blocks: {blocks}\n    needs: {needs}\n"


def test_a_dependency_naming_no_item_is_an_error(tmp_path: Path) -> None:
    write_item(tmp_path, "work/feature-x", "type: Feature\nwork_status: open\n" + _edge("work/bug-gone"))
    assert lane_report(tmp_path, today=TODAY).by_code("graph.depends-on-missing")[0].severity == "error"


def test_a_dependency_under_a_different_parent_is_a_warn(tmp_path: Path) -> None:
    left = "work/release-left/children/feature-a"
    right = "work/release-right/children/bug-b"
    write_item(tmp_path, "work/release-left", "type: Release\nwork_status: open\n")
    write_item(tmp_path, "work/release-right", "type: Release\nwork_status: open\n")
    write_item(tmp_path, left, "type: Feature\nwork_status: open\n" + _edge(right))
    write_item(tmp_path, right, "type: Bug\nwork_status: open\n")

    finding = lane_report(tmp_path, today=TODAY).by_code("graph.depends-on-not-sibling")[0]

    assert finding.severity == "warn"
    assert finding.path == f"{left}.md"


def test_a_sibling_dependency_is_silent(tmp_path: Path) -> None:
    lane = "work/release/children"
    dependency = f"{lane}/feature-b"
    write_item(tmp_path, "work/release", "type: Release\nwork_status: open\n")
    write_item(tmp_path, f"{lane}/feature-a", "type: Feature\nwork_status: open\n" + _edge(dependency))
    write_item(tmp_path, dependency, "type: Feature\nwork_status: open\n")
    assert "graph.depends-on-not-sibling" not in codes_for(tmp_path)


@pytest.mark.parametrize(
    ("depends_on", "needle"),
    [
        ([{"blocks": "plan"}], "missing keys"),
        ([{"path": "work/a", "blocks": "plan", "needs": "design", "extra": "x"}], "unknown keys"),
        ([{"path": "work/a", "blocks": "build", "needs": "design"}], "invalid blocks"),
        ([{"path": "work/a", "blocks": "plan", "needs": "started"}], "invalid needs"),
    ],
)
def test_dependency_edge_lint_reports_authored_problem(tmp_path: Path, depends_on: object, needle: str) -> None:
    write_item(tmp_path, "work/feature", "type: Feature\nwork_status: open\n")
    document = load(tmp_path / "work" / "feature.md")
    document.set("depends_on", depends_on)
    document.save()
    assert any(needle in finding.message for finding in lane_report(tmp_path).findings)


def test_dependency_naming_itself_or_a_containing_parent_is_invalid(tmp_path: Path) -> None:
    release = "work/release"
    child = f"{release}/children/feature"
    write_item(tmp_path, release, "type: Release\nwork_status: open\n")
    write_item(
        tmp_path,
        child,
        "type: Feature\nwork_status: open\ndepends_on:\n"
        f"  - path: {release}\n    blocks: execute\n    needs: resolved\n"
        f"  - path: {child}\n    blocks: finish\n    needs: execute\n",
    )
    findings = lane_report(tmp_path).by_code("graph.depends-on-invalid")
    assert len([finding for finding in findings if "itself or a containing parent" in finding.message]) == 2


def test_phase_compatible_path_cycle_is_not_reported(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "work/feature-a",
        "type: Feature\nwork_status: open\n" + _edge("work/feature-b", blocks="execute", needs="design"),
    )
    write_item(
        tmp_path,
        "work/feature-b",
        "type: Feature\nwork_status: open\n" + _edge("work/feature-a", blocks="plan", needs="design"),
    )
    assert "graph.depends-on-cycle" not in codes_for(tmp_path)


def test_dependency_completion_gates_the_blocked_phase_entry() -> None:
    dependency = make_item("work/dependency")
    dependent = make_item(
        "work/dependent",
        dependency_edges=(DependencyEdge("work/dependency", blocks="execute", needs="plan"),),
    )
    graph = _phase_dependency_graph((dependency, dependent))
    assert "work/dependent#execute:entry" in graph["work/dependency#plan:complete"]
    assert "work/dependent#execute:complete" not in graph["work/dependency#plan:complete"]


def test_a_depends_on_cycle_names_every_participant(tmp_path: Path) -> None:
    write_item(tmp_path, "work/bug-a", "type: Bug\nwork_status: open\n" + _edge("work/bug-b"))
    write_item(tmp_path, "work/bug-b", "type: Bug\nwork_status: open\n" + _edge("work/bug-a"))
    assert paths_for(tmp_path, "graph.depends-on-cycle") == {"work/bug-a.md", "work/bug-b.md"}


def test_a_long_acyclic_dependency_chain_does_not_recurse(tmp_path: Path) -> None:
    count = 1080
    for index in range(count):
        path = f"work/feature-{index}"
        dependency = "" if index == 0 else _edge(f"work/feature-{index - 1}")
        write_item(tmp_path, path, "type: Feature\nwork_status: open\n" + dependency)
    assert "graph.depends-on-cycle" not in codes_for(tmp_path)


def test_a_terminal_dependency_does_not_form_a_phase_cycle(tmp_path: Path) -> None:
    write_item(tmp_path, "work/feature-a", "type: Feature\nwork_status: open\n" + _edge("work/feature-b"))
    write_item(tmp_path, "work/feature-b", "type: Feature\nwork_status: resolved\n" + _edge("work/feature-a"))
    assert "graph.depends-on-cycle" not in codes_for(tmp_path)


def test_an_epic_past_decomposition_with_no_children_is_a_warn(tmp_path: Path) -> None:
    write_item(tmp_path, "work/epic-x", "type: Epic\nwork_status: open\nphase: execute\n")
    assert lane_report(tmp_path, today=TODAY).by_code("graph.epic-without-children")[0].severity == "warn"


def test_an_epic_with_a_direct_archived_child_is_silent(tmp_path: Path) -> None:
    epic = "work/epic-x"
    write_item(tmp_path, epic, "type: Epic\nwork_status: open\nphase: execute\n")
    write_item(tmp_path, f"{epic}/children/_archive/bug-old", "type: Bug\nwork_status: resolved\n")
    assert "graph.epic-without-children" not in codes_for(tmp_path)


def test_the_module_declares_five_graph_codes() -> None:
    from work_tracker_okf._rules import graph

    assert len(graph.CODES) == 5
    assert all(code.startswith("graph.") for code in graph.CODES)
