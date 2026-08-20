from __future__ import annotations

import dataclasses
import random
from pathlib import Path

import pytest
from okf_io import load_bundle, validate
from okf_io.validate import Report
from work_helpers import (
    NONCONFORMANT_GOLDEN,
    NONCONFORMANT_REPO,
    NONCONFORMANT_ROOT,
    NONCONFORMANT_TODAY,
    render_finding,
)
from work_tracker_okf import IGNORE, _rules
from work_tracker_okf.rules import lane_rules

#: Hand-written from the design spec's §2 tables plus §4.3's, not derived from a
#: run. Regenerating a golden file cannot satisfy this.
ERROR_CODES = frozenset(
    {
        "state.in-progress-without-owner",
        "state.superseded-without-link",
        "state.mitigated-without-mitigation",
        "plan.accepted-without-plan",
        "plan.action-target-missing",
        "graph.parent-missing",
        "graph.parent-type-invalid",
        "graph.depends-on-missing",
        "graph.depends-on-invalid",
        "graph.parent-cycle",
        "graph.depends-on-cycle",
        "targets.affects-missing",
        "decisions.entry-invalid",
        "decisions.cite-missing",
        "decisions.open-at-finish",
        "decisions.supersedes-invalid",
    }
)

#: The eighteen prefixes the five lane topics had to clear. Written out rather
#: than imported: okf-io's `_rules.TOPICS` is private to okf-io, and okf-ext's
#: six live one per capability module. `validate()` raises on the first eight at
#: runtime; this test is what catches the other six, which are legal and still
#: wrong.
OKF_IO_TOPICS = frozenset(
    {"computation", "frontmatter", "legacy", "lifecycle", "links", "provenance", "reserved", "trust"}
)
OKF_EXT_TOPICS = frozenset({"schemas", "sections", "health", "render", "tags", "placement"})


def test_every_declared_code_carries_its_module_prefix() -> None:
    """The `_rules` module name *is* the code prefix, asserted mechanically.

    Static: it needs no fixture, so the catalog cannot drift from its own file
    layout even before a rule has a test."""
    for topic, codes in _rules.CODES_BY_TOPIC.items():
        for code in codes:
            assert code.startswith(f"{topic}."), (topic, code)


def test_the_catalog_is_thirty_one_codes_across_five_topics() -> None:
    assert len(_rules.CATALOG) == 31
    assert len(_rules.TOPICS) == 5
    assert sum(len(codes) for codes in _rules.CODES_BY_TOPIC.values()) == 31


def test_the_per_topic_counts_match_the_design_spec() -> None:
    assert {topic: len(codes) for topic, codes in _rules.CODES_BY_TOPIC.items()} == {
        "state": 10,
        "plan": 4,
        "graph": 9,
        "targets": 3,
        "decisions": 5,
    }


def test_the_severity_split_is_sixteen_errors_and_fifteen_warns() -> None:
    assert len(ERROR_CODES) == 16
    assert ERROR_CODES < _rules.CATALOG
    assert len(_rules.CATALOG - ERROR_CODES) == 15


def test_no_lane_topic_collides_with_a_built_in_or_an_okf_ext_prefix() -> None:
    assert _rules.TOPICS.isdisjoint(OKF_IO_TOPICS)
    assert _rules.TOPICS.isdisjoint(OKF_EXT_TOPICS)


def test_the_registry_is_homogeneous() -> None:
    """C5-E: every topic exports `rules(config)`, including the two that inject
    nothing. A registry of some tuples and some callables would make the
    catalog-completeness test special-case half its own subjects."""
    assert all(callable(factory) for factory in _rules.RULES_BY_TOPIC.values())
    assert set(_rules.RULES_BY_TOPIC) == set(_rules.CODES_BY_TOPIC)


def test_lane_rules_is_fourteen_functions_with_a_repo_root(tmp_path: Path) -> None:
    assert len(lane_rules(repo_root=tmp_path)) == 14


def test_lane_rules_drops_the_two_repo_rules_without_one() -> None:
    assert len(lane_rules()) == 12


def test_lane_rules_is_stable_across_calls(tmp_path: Path) -> None:
    """Topic order, not dict-literal order: a catalog that reorders when someone
    reformats it is a catalog whose output order is an accident."""
    assert [rule.__qualname__ for rule in lane_rules(repo_root=tmp_path)] == [
        rule.__qualname__ for rule in lane_rules(repo_root=tmp_path)
    ]


def test_rules_stays_a_submodule() -> None:
    """`lane_rules` reads better as `rules.lane_rules` than as a bare name at the
    package's front door -- the rule `vocabulary`, `workflow` and `filing` follow."""
    import work_tracker_okf

    assert "lane_rules" not in work_tracker_okf.__all__


def _lane_findings(report: Report) -> tuple:
    """The lane's own findings. The vault also trips okf-io's core catalog by
    construction -- a page with no `## Definition` heading is not this child's
    concern -- so the golden is deliberately the lane's slice, not the whole
    report. That keeps it stable against okf-io's catalog changing under it."""
    return tuple(f for f in report.findings if f.code.split(".")[0] in _rules.TOPICS)


def _fresh_report() -> Report:
    """A fresh, independent walk-and-validate. Never memoized: the determinism
    tests need two runs that do not share one `Report`, or the comparison is an
    object compared with itself."""
    return validate(
        load_bundle(NONCONFORMANT_ROOT, ignore=IGNORE),
        today=NONCONFORMANT_TODAY,
        extra_rules=lane_rules(repo_root=NONCONFORMANT_REPO),
    )


@pytest.fixture(scope="module")
def golden_report() -> Report:
    """One walk over the vault, shared by every test in this module. Pure and
    deterministic given the fixed `NONCONFORMANT_TODAY`."""
    return _fresh_report()


def test_the_vault_triggers_every_catalog_code(golden_report: Report) -> None:
    """One walk, all 31. This is what catches a rule that stops firing."""
    assert {f.code for f in _lane_findings(golden_report)} == _rules.CATALOG


def test_no_rule_emits_an_undeclared_code(golden_report: Report) -> None:
    assert {f.code for f in _lane_findings(golden_report)} <= _rules.CATALOG


def test_the_error_codes_are_exactly_the_sixteen(golden_report: Report) -> None:
    lane = _lane_findings(golden_report)
    assert {f.code for f in lane if f.severity == "error"} == ERROR_CODES


def test_the_warn_codes_are_the_catalog_remainder(golden_report: Report) -> None:
    lane = _lane_findings(golden_report)
    assert {f.code for f in lane if f.severity == "warn"} == _rules.CATALOG - ERROR_CODES


def test_the_report_matches_the_reviewed_golden_file(golden_report: Report) -> None:
    rendered = "\n".join(render_finding(f) for f in _lane_findings(golden_report)) + "\n"
    assert rendered == NONCONFORMANT_GOLDEN.read_text(encoding="utf-8")


def test_output_is_identical_across_runs() -> None:
    assert _lane_findings(_fresh_report()) == _lane_findings(_fresh_report())


def test_output_does_not_depend_on_mapping_order() -> None:
    """Findings are sorted after collection, not merely appended."""
    loaded = load_bundle(NONCONFORMANT_ROOT, ignore=IGNORE)
    concepts = list(loaded.concepts.items())
    random.Random(0).shuffle(concepts)
    shuffled = dataclasses.replace(loaded, concepts=dict(concepts))
    report = validate(shuffled, today=NONCONFORMANT_TODAY, extra_rules=lane_rules(repo_root=NONCONFORMANT_REPO))
    assert _lane_findings(report) == _lane_findings(_fresh_report())


def test_the_two_repo_codes_vanish_without_a_repo_root() -> None:
    """The same vault, no root: the two repo questions are skipped, not failed."""
    report = validate(
        load_bundle(NONCONFORMANT_ROOT, ignore=IGNORE),
        today=NONCONFORMANT_TODAY,
        extra_rules=lane_rules(repo_root=None),
    )
    seen = {f.code for f in _lane_findings(report)}
    assert seen == _rules.CATALOG - {"targets.affects-missing", "plan.action-target-missing"}


def test_the_synthetic_repo_is_a_sibling_of_the_vault() -> None:
    """C5-J: not inside it, so `IGNORE` never had to grow a pattern for it."""
    assert NONCONFORMANT_REPO.parent == NONCONFORMANT_ROOT.parent
    assert NONCONFORMANT_REPO.is_dir()
    assert not any(NONCONFORMANT_REPO.is_relative_to(p) for p in [NONCONFORMANT_ROOT])
