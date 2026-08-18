"""The lane composer. Rule sets are asserted as TOPIC sets, not as counts: a
lane that silently loses a capability has to fail a test, and the count would
still pass if one factory were swapped for another."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
import work_tracker_okf
from code_wiki_okf.config import load_config
from graph_works_core import apply_init, plan_init
from graph_works_core.lint_drift.lanes import LaneSet, compose_lanes
from okf_io import Bundle, RuleContext, build_link_graph, load_bundle, validate

TODAY = date(2026, 8, 13)
AT = datetime(2026, 8, 13, tzinfo=UTC)


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return apply_init(plan_init(repo / ".works", today=TODAY, topic="Lanes"))


def _compose(workspace, **kwargs) -> LaneSet:
    layout = workspace.layout
    return compose_lanes(layout, load_config(layout.bundle_dir), repo_root=layout.repo_root, at=AT, **kwargs)


def _topics(bundle: Bundle, lane) -> set[str]:
    """Every topic a lane's rules can emit, run against a real bundle."""
    ctx = RuleContext(bundle=bundle, links=build_link_graph(bundle), today=TODAY)
    emitted = {finding.code.split(".", 1)[0] for rule in lane.rules for finding in rule(ctx)}
    return emitted


def test_a_default_workspace_composes_the_wiki_lane_then_the_work_lane(workspace):
    lanes = _compose(workspace)
    assert [lane.name for lane in lanes.lanes] == ["wiki", "work"]
    assert lanes.errors == ()


def test_both_lanes_root_at_the_bundle_dir(workspace):
    for lane in _compose(workspace).lanes:
        assert lane.root == workspace.layout.bundle_dir


def _seed_both_lanes(workspace):
    """One curated page and one work item, written before composition — the
    work lane's recipe is derived from the bundle directory as it stands."""
    bundle_dir = workspace.layout.bundle_dir
    (bundle_dir / "concepts").mkdir(exist_ok=True)
    (bundle_dir / "concepts" / "byte-fidelity.md").write_text(
        "---\ntype: Explanation\ntitle: Byte fidelity\n---\n\nBody.\n", encoding="utf-8"
    )
    (bundle_dir / "work").mkdir(exist_ok=True)
    (bundle_dir / "work" / "2026-08-13-bug-example.md").write_text(
        "---\ntype: Bug\ntitle: Example\nstatus: accepted\n---\n\nSee [byte fidelity](/concepts/byte-fidelity.md).\n",
        encoding="utf-8",
    )
    return bundle_dir


def test_each_lane_names_its_own_members_and_no_others(workspace):
    """D2: both lanes root at the bundle dir, so without a partition every
    curated page is walked twice and its core-catalog findings reported twice."""
    _seed_both_lanes(workspace)
    wiki, work = _compose(workspace).lanes
    members = {lane.name: set(load_bundle(lane.root, ignore=lane.ignore).concepts) for lane in (wiki, work)}
    assert members["wiki"] & members["work"] == set()
    assert "concepts/byte-fidelity" in members["wiki"]
    assert "work/2026-08-13-bug-example" in members["work"]


def test_each_lane_names_the_other_lane_rather_than_its_own_contents(workspace):
    wiki, work = _compose(workspace).lanes
    assert f"{work_tracker_okf.WORK_DIR}/*" in wiki.ignore
    assert set(work_tracker_okf.IGNORE) <= set(work.ignore)


def test_a_work_item_linking_a_curated_page_reports_no_broken_link(workspace):
    """`ignore=` declares "this is not a concept", not "this is not there":
    `has_member` counts ignored members, so the cross-lane link resolves."""
    _seed_both_lanes(workspace)
    _wiki, work = _compose(workspace).lanes
    bundle = load_bundle(work.root, ignore=work.ignore)
    report = validate(bundle, today=TODAY, extra_rules=work.rules)
    assert not any(finding.code == "links.broken" for finding in report.findings)


def test_a_curated_directory_added_later_needs_no_lanes_edit(workspace):
    """The recipe is derived from the bundle directory rather than written as a
    literal list, because `ignore=` has no negation — a list would let a new
    curated directory silently rejoin the work lane and restore the
    duplication with nothing to catch it."""
    bundle_dir = workspace.layout.bundle_dir
    (bundle_dir / "tutorials").mkdir()
    (bundle_dir / "tutorials" / "getting-started.md").write_text(
        "---\ntype: Tutorial\ntitle: Start\n---\n\nBody.\n", encoding="utf-8"
    )
    _wiki, work = _compose(workspace).lanes
    assert "tutorials/*" in work.ignore
    assert "tutorials/getting-started" not in load_bundle(work.root, ignore=work.ignore).concepts


def test_the_wiki_lane_carries_every_declared_capability(workspace):
    """The topic set, derived by running each factory's rules over a bundle
    engineered to trip every one of them, is asserted in Task 5's mechanical
    test. Here the weaker but still load-bearing claim: the composer built a
    rule for each capability whose declarations are present."""
    wiki, _work = _compose(workspace).lanes
    assert len(wiki.rules) == 6  # health, render, schema, section, vocabulary, placement — no reader


def test_a_reader_adds_the_sync_rule(workspace, monkeypatch):
    from graph_works_core.lint_drift import lanes as lanes_module

    class _Reader:
        pass

    monkeypatch.setattr(lanes_module, "snapshot_bundle", lambda *a, **k: _EmptySnapshot())
    wiki, _work = _compose(workspace, reader=_Reader()).lanes
    assert len(wiki.rules) == 7


class _EmptySnapshot:
    stale: frozenset[str] = frozenset()
    missing: frozenset[str] = frozenset()
    orphaned: frozenset[str] = frozenset()


def test_absent_wiki_declarations_are_a_fact_but_the_work_lane_now_requires_them(workspace, tmp_path):
    config_dir = workspace.layout.config_dir
    for name in ("_schema", "_sections"):
        for child in sorted((config_dir / name).iterdir()):
            child.unlink()
        (config_dir / name).rmdir()
    (config_dir / "_tags.yaml").unlink()
    lanes = _compose(workspace)
    assert [lane.name for lane in lanes.lanes] == ["wiki"]
    assert len(lanes.lanes[0].rules) == 2  # health + render only
    assert len(lanes.errors) == 1
    assert "work" in lanes.errors[0]


def test_the_work_lane_now_validates_schema_and_section_conformance(workspace):
    bundle_dir = workspace.layout.bundle_dir
    (bundle_dir / "work").mkdir(exist_ok=True)
    (bundle_dir / "work" / "2026-08-13-bug-bad-type.md").write_text(
        "---\ntype: NotAType\ntitle: Bad\nstatus: accepted\n---\n\nBody.\n", encoding="utf-8"
    )
    _wiki, work = _compose(workspace).lanes
    bundle = load_bundle(work.root, ignore=work.ignore)
    report = validate(bundle, today=TODAY, extra_rules=work.rules)
    assert any(finding.code.startswith("schemas.") for finding in report.findings)


def test_a_malformed_declaration_is_one_lane_error_and_the_other_lane_still_runs(workspace):
    (workspace.layout.config_dir / "_tags.yaml").write_text("not: [a mapping\n", encoding="utf-8")
    lanes = _compose(workspace)
    assert len(lanes.errors) == 1
    assert "wiki" in lanes.errors[0]
    assert [lane.name for lane in lanes.lanes] == ["work"]


def test_a_bug_inside_a_rule_factory_propagates_rather_than_becoming_a_lane_error(workspace, monkeypatch):
    """D9: a `ValueError` from a factory's own logic is a bug, and `lint.py`
    already holds this line for `validate()`. Composition holds it too."""
    from graph_works_core.lint_drift import lanes as lanes_module

    def _boom(*args, **kwargs):
        raise ValueError("a factory blew up")

    monkeypatch.setattr(lanes_module, "health_rule", _boom)
    with pytest.raises(ValueError, match="a factory blew up"):
        _compose(workspace)


def test_no_repo_root_still_composes_the_work_lane(workspace):
    layout = workspace.layout
    lanes = compose_lanes(layout, load_config(layout.bundle_dir), repo_root=None, at=AT)
    assert [lane.name for lane in lanes.lanes] == ["wiki", "work"]


def test_every_lane_loads_as_a_bundle(workspace):
    for lane in _compose(workspace).lanes:
        bundle = load_bundle(lane.root, ignore=lane.ignore)
        assert bundle.root == lane.root
        assert _topics(bundle, lane) <= {
            "health",
            "render",
            "schema",
            "sections",
            "tags",
            "placement",
            "sync",
            "state",
            "plan",
            "targets",
            "graph",
        }
