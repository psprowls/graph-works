from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from okf_io import load
from work_helpers import lane_report, make_item, write_item
from work_tracker_okf._rules.graph import _phase_dependency_graph
from work_tracker_okf.dependencies import DependencyEdge

TODAY = date(2026, 8, 3)


def codes_for(tmp_path: Path) -> set[str]:
    return {f.code for f in lane_report(tmp_path, today=TODAY).findings if f.code.startswith("graph.")}


def slugs_for(tmp_path: Path, code: str) -> set[str]:
    return {f.path for f in lane_report(tmp_path, today=TODAY).by_code(code)}


# --- graph.parent-missing / parent-type-invalid -----------------------------


def test_a_parent_naming_no_item_is_an_error(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\nparent: 2026-08-01-epic-gone\n")
    finding = lane_report(tmp_path, today=TODAY).by_code("graph.parent-missing")[0]
    assert finding.severity == "error"


def test_a_parent_that_is_not_a_parent_type_is_an_error(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-spike-p", "type: Spike\nworkflow_status: open\n")
    write_item(tmp_path, "2026-08-02-feature-c", "type: Feature\nworkflow_status: open\nparent: 2026-08-01-spike-p\n")
    finding = lane_report(tmp_path, today=TODAY).by_code("graph.parent-type-invalid")[0]
    assert finding.severity == "error"
    assert finding.path == "work/2026-08-02-feature-c.md"


def test_an_epic_parent_is_silent(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "2026-08-01-epic-p",
        "type: Epic\nworkflow_status: open\nchildren:\n  - 2026-08-02-feature-c\n",
    )
    write_item(tmp_path, "2026-08-02-feature-c", "type: Feature\nworkflow_status: open\nparent: 2026-08-01-epic-p\n")
    codes = codes_for(tmp_path)
    assert "graph.parent-missing" not in codes
    assert "graph.parent-type-invalid" not in codes


def test_an_archived_parent_still_resolves(tmp_path: Path) -> None:
    """The bug work-io needed a second loader to avoid."""
    archived = tmp_path / "work" / "_archive" / "2026-08-01-epic-p.md"
    archived.parent.mkdir(parents=True, exist_ok=True)
    archived.write_text("---\ntitle: T\ndescription: D\ntype: Epic\nworkflow_status: resolved\n---\n", encoding="utf-8")
    write_item(tmp_path, "2026-08-02-feature-c", "type: Feature\nworkflow_status: open\nparent: 2026-08-01-epic-p\n")
    assert lane_report(tmp_path, today=TODAY).by_code("graph.parent-missing") == ()


# --- graph.depends-on-missing / depends-on-not-sibling ----------------------


def test_a_dependency_naming_no_item_is_an_error(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "2026-08-01-feature-x",
        "type: Feature\nworkflow_status: open\ndepends_on:\n  - 2026-08-01-bug-gone\n",
    )
    assert lane_report(tmp_path, today=TODAY).by_code("graph.depends-on-missing")[0].severity == "error"


def test_a_dependency_under_a_different_parent_is_a_warn(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "2026-08-01-epic-p",
        "type: Epic\nworkflow_status: open\nchildren:\n  - 2026-08-02-feature-a\n",
    )
    write_item(
        tmp_path,
        "2026-08-02-feature-a",
        "type: Feature\nworkflow_status: open\nparent: 2026-08-01-epic-p\ndepends_on:\n  - 2026-08-03-bug-b\n",
    )
    write_item(tmp_path, "2026-08-03-bug-b", "type: Bug\nworkflow_status: open\n")
    finding = lane_report(tmp_path, today=TODAY).by_code("graph.depends-on-not-sibling")[0]
    assert finding.severity == "warn"
    assert finding.path == "work/2026-08-02-feature-a.md"


def test_a_sibling_dependency_is_silent(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "2026-08-01-epic-p",
        "type: Epic\nworkflow_status: open\nchildren:\n  - 2026-08-02-feature-a\n  - 2026-08-03-feature-b\n",
    )
    write_item(
        tmp_path,
        "2026-08-02-feature-a",
        "type: Feature\nworkflow_status: open\nparent: 2026-08-01-epic-p\ndepends_on:\n  - 2026-08-03-feature-b\n",
    )
    write_item(tmp_path, "2026-08-03-feature-b", "type: Feature\nworkflow_status: open\nparent: 2026-08-01-epic-p\n")
    assert "graph.depends-on-not-sibling" not in codes_for(tmp_path)


def test_a_parentless_item_never_reports_not_sibling(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "2026-08-02-feature-a",
        "type: Feature\nworkflow_status: open\ndepends_on:\n  - 2026-08-03-bug-b\n",
    )
    write_item(tmp_path, "2026-08-03-bug-b", "type: Bug\nworkflow_status: open\n")
    assert "graph.depends-on-not-sibling" not in codes_for(tmp_path)


@pytest.mark.parametrize(
    ("depends_on", "needle"),
    [
        ([{"blocks": "plan"}], "missing slug"),
        ([{"slug": "a", "extra": "x"}], "unknown keys"),
        ([{"slug": "a", "blocks": "build"}], "invalid blocks"),
        ([{"slug": "a", "needs": "started"}], "invalid needs"),
        (["a", "a"], "duplicate edge"),
    ],
)
def test_dependency_edge_lint_reports_authored_problem(tmp_path: Path, depends_on, needle: str) -> None:
    write_item(tmp_path, "parent", "type: Epic\nworkflow_status: open\n")
    document = load(tmp_path / "work/parent.md")
    document.set("depends_on", depends_on)
    document.save()
    assert any(needle in finding.message for finding in lane_report(tmp_path).findings)


def test_dependency_naming_its_parent_is_invalid(tmp_path: Path) -> None:
    write_item(tmp_path, "parent", "type: Epic\nworkflow_status: open\n")
    write_item(
        tmp_path,
        "child",
        "type: Feature\nworkflow_status: open\nparent: parent\ndepends_on:\n  - parent\n",
    )
    findings = lane_report(tmp_path).by_code("graph.depends-on-invalid")
    assert any("names its parent" in finding.message for finding in findings)


def test_distinct_same_slug_gates_are_not_duplicates(tmp_path: Path) -> None:
    write_item(tmp_path, "a", "type: Feature\nworkflow_status: open\n")
    write_item(tmp_path, "b", "type: Feature\nworkflow_status: open\n")
    document = load(tmp_path / "work/b.md")
    document.set(
        "depends_on",
        [
            {"slug": "a", "blocks": "plan", "needs": "design"},
            {"slug": "a", "blocks": "execute", "needs": "resolved"},
        ],
    )
    document.save()
    findings = lane_report(tmp_path).findings
    assert not any("duplicate edge" in finding.message for finding in findings)


def test_phase_compatible_slug_cycle_is_not_reported(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "a",
        "type: Feature\nworkflow_status: open\ndepends_on:\n  - slug: b\n    blocks: execute\n    needs: design\n",
    )
    write_item(
        tmp_path,
        "b",
        "type: Feature\nworkflow_status: open\ndepends_on:\n  - slug: a\n    blocks: plan\n    needs: design\n",
    )
    findings = lane_report(tmp_path).findings
    assert not any(finding.code == "graph.depends-on-cycle" for finding in findings)


def test_completion_boundary_schedule_is_not_reported_as_a_dependency_cycle(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "a",
        "type: Feature\nworkflow_status: open\ndepends_on:\n  - slug: b\n    blocks: finish\n    needs: resolved\n",
    )
    write_item(
        tmp_path,
        "b",
        "type: Feature\nworkflow_status: open\ndepends_on:\n  - slug: a\n    blocks: execute\n    needs: execute\n",
    )

    findings = lane_report(tmp_path).by_code("graph.depends-on-cycle")

    assert findings == ()


def test_dependency_completion_gates_the_blocked_phase_entry() -> None:
    dependency = make_item("dependency")
    dependent = make_item(
        "dependent",
        depends_on=(DependencyEdge("dependency", blocks="execute", needs="plan"),),
    )

    graph = _phase_dependency_graph((dependency, dependent))

    assert "dependent#execute:entry" in graph["dependency#plan:complete"]
    assert "dependent#execute:complete" not in graph["dependency#plan:complete"]


# --- the two cycles ---------------------------------------------------------


def test_a_depends_on_cycle_names_every_participant(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-bug-a", "type: Bug\nworkflow_status: open\ndepends_on:\n  - 2026-08-02-bug-b\n")
    write_item(tmp_path, "2026-08-02-bug-b", "type: Bug\nworkflow_status: open\ndepends_on:\n  - 2026-08-01-bug-a\n")
    assert slugs_for(tmp_path, "graph.depends-on-cycle") == {
        "work/2026-08-01-bug-a.md",
        "work/2026-08-02-bug-b.md",
    }


def test_a_terminal_dependency_does_not_form_a_phase_cycle(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "a",
        "type: Feature\nworkflow_status: open\ndepends_on:\n  - b\n",
    )
    write_item(
        tmp_path,
        "b",
        "type: Feature\nworkflow_status: resolved\ndepends_on:\n  - a\n",
    )
    assert "graph.depends-on-cycle" not in codes_for(tmp_path)


def test_a_parent_cycle_names_every_participant(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "2026-08-01-feature-a",
        "type: Feature\nworkflow_status: open\nparent: 2026-08-02-feature-b\nchildren:\n  - 2026-08-02-feature-b\n",
    )
    write_item(
        tmp_path,
        "2026-08-02-feature-b",
        "type: Feature\nworkflow_status: open\nparent: 2026-08-01-feature-a\nchildren:\n  - 2026-08-01-feature-a\n",
    )
    assert slugs_for(tmp_path, "graph.parent-cycle") == {
        "work/2026-08-01-feature-a.md",
        "work/2026-08-02-feature-b.md",
    }


def test_an_acyclic_graph_reports_nothing(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-bug-a", "type: Bug\nworkflow_status: open\ndepends_on:\n  - 2026-08-02-bug-b\n")
    write_item(tmp_path, "2026-08-02-bug-b", "type: Bug\nworkflow_status: open\n")
    codes = codes_for(tmp_path)
    assert "graph.depends-on-cycle" not in codes
    assert "graph.parent-cycle" not in codes


def test_a_dangling_edge_is_not_a_cycle(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-bug-a", "type: Bug\nworkflow_status: open\ndepends_on:\n  - 2026-08-09-bug-gone\n")
    assert "graph.depends-on-cycle" not in codes_for(tmp_path)


# --- graph.epic-without-children --------------------------------------------


def test_an_epic_past_decomposition_with_no_children_is_a_warn(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-epic-x", "type: Epic\nworkflow_status: open\nphase: execute\n")
    assert lane_report(tmp_path, today=TODAY).by_code("graph.epic-without-children")[0].severity == "warn"


def test_an_epic_before_decomposition_is_silent(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-epic-x", "type: Epic\nworkflow_status: open\nphase: plan\n")
    assert "graph.epic-without-children" not in codes_for(tmp_path)


def test_an_epic_with_children_is_silent(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "2026-08-01-epic-x",
        "type: Epic\nworkflow_status: open\nphase: execute\nchildren:\n  - 2026-08-02-feature-c\n",
    )
    write_item(tmp_path, "2026-08-02-feature-c", "type: Feature\nworkflow_status: open\nparent: 2026-08-01-epic-x\n")
    assert "graph.epic-without-children" not in codes_for(tmp_path)


def test_a_non_epic_parent_type_is_exempt(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\nphase: execute\n")
    assert "graph.epic-without-children" not in codes_for(tmp_path)


# --- graph.children-stale ---------------------------------------------------


def test_an_unauthored_children_key_on_a_real_parent_is_a_warn(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-epic-x", "type: Epic\nworkflow_status: open\nphase: plan\n")
    write_item(tmp_path, "2026-08-02-feature-c", "type: Feature\nworkflow_status: open\nparent: 2026-08-01-epic-x\n")
    finding = lane_report(tmp_path, today=TODAY).by_code("graph.children-stale")[0]
    assert finding.severity == "warn"
    assert finding.path == "work/2026-08-01-epic-x.md"


def test_an_accurate_children_key_is_silent(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "2026-08-01-epic-x",
        "type: Epic\nworkflow_status: open\nphase: plan\nchildren:\n  - 2026-08-02-feature-c\n",
    )
    write_item(tmp_path, "2026-08-02-feature-c", "type: Feature\nworkflow_status: open\nparent: 2026-08-01-epic-x\n")
    assert lane_report(tmp_path, today=TODAY).by_code("graph.children-stale") == ()


def test_an_item_with_no_children_and_no_key_is_silent(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-bug-x", "type: Bug\nworkflow_status: open\n")
    assert lane_report(tmp_path, today=TODAY).by_code("graph.children-stale") == ()


# --- the module's shape -----------------------------------------------------


def test_the_module_declares_nine_codes_all_prefixed_graph() -> None:
    from work_tracker_okf._rules import graph

    assert len(graph.CODES) == 9
    assert all(code.startswith("graph.") for code in graph.CODES)
    assert "graph.depends-on-invalid" in graph.CODES


def test_the_renamed_code_names_type_not_kind() -> None:
    from work_tracker_okf._rules import graph

    assert "graph.parent-type-invalid" in graph.CODES
    assert not any("kind" in code for code in graph.CODES)
