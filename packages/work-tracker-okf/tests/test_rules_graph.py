from __future__ import annotations

from datetime import date
from pathlib import Path

from work_helpers import lane_report, write_item

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


# --- the two cycles ---------------------------------------------------------


def test_a_depends_on_cycle_names_every_participant(tmp_path: Path) -> None:
    write_item(tmp_path, "2026-08-01-bug-a", "type: Bug\nworkflow_status: open\ndepends_on:\n  - 2026-08-02-bug-b\n")
    write_item(tmp_path, "2026-08-02-bug-b", "type: Bug\nworkflow_status: open\ndepends_on:\n  - 2026-08-01-bug-a\n")
    assert slugs_for(tmp_path, "graph.depends-on-cycle") == {
        "work/2026-08-01-bug-a.md",
        "work/2026-08-02-bug-b.md",
    }


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


def test_the_module_declares_eight_codes_all_prefixed_graph() -> None:
    from work_tracker_okf._rules import graph

    assert len(graph.CODES) == 8
    assert all(code.startswith("graph.") for code in graph.CODES)


def test_the_renamed_code_names_type_not_kind() -> None:
    from work_tracker_okf._rules import graph

    assert "graph.parent-type-invalid" in graph.CODES
    assert not any("kind" in code for code in graph.CODES)
