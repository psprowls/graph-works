from __future__ import annotations

from datetime import date

import ext_helpers
from okf_ext.health import CODES, TOPIC, health_rule
from okf_io import Finding, load_bundle, validate

TODAY = date(2026, 8, 6)


def run(bundle, *, today=TODAY, severity="warn", log_gap_days=14, strict=False):
    report = validate(
        bundle,
        today=today,
        extra_rules=[health_rule(severity=severity, log_gap_days=log_gap_days)],
        strict=strict,
    )
    return [f for f in report.findings if f.code.startswith(f"{TOPIC}.")]


def build(tmp_path, **members):
    for name, text in members.items():
        (tmp_path / f"{name.replace('_', '-')}.md").write_text(text, encoding="utf-8")
    return load_bundle(tmp_path)


# --- The corpus walk --------------------------------------------------------


def test_the_corpus_reports_exactly_the_expected_set():
    found = {(f.code, f.path) for f in run(ext_helpers.unhealthy_bundle())}
    assert found == ext_helpers.HEALTH_EXPECTED


# --- health.uncited ---------------------------------------------------------


def test_an_uncited_concept_carries_no_line():
    """The finding is about the document's absence from other documents. No line
    in it is at fault, and a wrong line is worse than no line."""
    found = [f for f in run(ext_helpers.unhealthy_bundle()) if f.code == "health.uncited"]
    assert found
    assert all(f.line is None for f in found)


def test_a_concept_cited_only_by_an_index_is_still_uncited(tmp_path):
    """okf-io's `LinkGraph.backlinks` counts prose citations only: an §8 index
    enumerating its own directory is a table of contents, not a citation. That
    is a deliberate divergence from wiki-io's `orphans`, and it is why this code
    is named `uncited` rather than `orphan`."""
    (tmp_path / "index.md").write_text("---\ntype: Index\ntitle: I\n---\n\n- [Solo](solo.md)\n", encoding="utf-8")
    (tmp_path / "solo.md").write_text("---\ntype: Note\ntitle: Solo\n---\n\n# Solo\n", encoding="utf-8")
    found = [f for f in run(load_bundle(tmp_path)) if f.code == "health.uncited"]
    assert [f.path for f in found] == ["solo.md"]


# --- health.duplicate-title -------------------------------------------------


def test_each_participant_gets_its_own_finding_naming_the_others():
    found = [f for f in run(ext_helpers.unhealthy_bundle()) if f.code == "health.duplicate-title"]
    assert {f.path for f in found} == {"dup-a.md", "dup-b.md"}
    assert all(f.line == 3 for f in found)  # the `title:` key
    by_path = {f.path: f.message for f in found}
    assert "dup-b.md" in by_path["dup-a.md"]
    assert "dup-a.md" in by_path["dup-b.md"]


def test_untitled_concepts_never_form_a_group(tmp_path):
    """A group keyed on the empty string would collapse every untitled document
    into one false cluster. okf-io's `frontmatter.title-recommended` already
    reports the missing title."""
    bundle = build(
        tmp_path,
        a="---\ntype: Note\n---\n\n# A\n",
        b="---\ntype: Note\n---\n\n# B\n",
    )
    assert not [f for f in run(bundle) if f.code == "health.duplicate-title"]


def test_titles_are_compared_stripped(tmp_path):
    bundle = build(
        tmp_path,
        a="---\ntype: Note\ntitle: '  Same  '\n---\n\n# A\n",
        b="---\ntype: Note\ntitle: Same\n---\n\n# B\n",
    )
    assert len([f for f in run(bundle) if f.code == "health.duplicate-title"]) == 2


# --- health.log-gap ---------------------------------------------------------


def test_a_stale_log_names_the_date_and_the_gap():
    found = [f for f in run(ext_helpers.unhealthy_bundle()) if f.code == "health.log-gap"]
    assert len(found) == 1
    assert found[0].path == "log.md"
    assert "2026-01-01" in found[0].message
    assert "217" in found[0].message
    assert found[0].line == 8  # body line 3 + body_line_offset 5


def test_a_log_inside_the_threshold_is_silent():
    assert not [f for f in run(ext_helpers.unhealthy_bundle(), log_gap_days=400) if f.code == "health.log-gap"]


def test_a_log_with_no_dated_section_reports_with_no_line(tmp_path):
    (tmp_path / "log.md").write_text(
        "---\ntype: Log\ntitle: L\n---\n\n# History\n\n## Not a date\n\n- Something.\n", encoding="utf-8"
    )
    found = [f for f in run(load_bundle(tmp_path)) if f.code == "health.log-gap"]
    assert len(found) == 1
    assert found[0].line is None
    assert "no dated section" in found[0].message


def test_a_bundle_with_no_logs_is_silent_not_an_error(tmp_path):
    bundle = build(tmp_path, a="---\ntype: Note\ntitle: A\n---\n\n# A\n")
    assert not [f for f in run(bundle) if f.code == "health.log-gap"]


def test_undated_sections_are_ignored_rather_than_reported(tmp_path):
    """`reserved.log-heading-not-date` already reports a log heading that is not
    a date. The newest *dated* section is what this rule reads."""
    (tmp_path / "log.md").write_text(
        "---\ntype: Log\ntitle: L\n---\n\n# History\n\n## Unreleased\n\n- x\n\n## 2026-08-01\n\n- y\n",
        encoding="utf-8",
    )
    assert not [f for f in run(load_bundle(tmp_path)) if f.code == "health.log-gap"]


def test_the_newest_dated_section_wins_regardless_of_document_order(tmp_path):
    """`parse_log` returns sections in document order, not sorted by date."""
    (tmp_path / "log.md").write_text(
        "---\ntype: Log\ntitle: L\n---\n\n# History\n\n## 2026-01-01\n\n- old\n\n## 2026-08-01\n\n- new\n",
        encoding="utf-8",
    )
    assert not [f for f in run(load_bundle(tmp_path)) if f.code == "health.log-gap"]


# --- No clock ---------------------------------------------------------------


def test_two_today_values_produce_different_log_gap_output():
    """The rule reads `context.today` and nothing else. okf-io never reads the
    clock and neither does this."""
    bundle = ext_helpers.unhealthy_bundle()
    near = [f for f in run(bundle, today=date(2026, 1, 5)) if f.code == "health.log-gap"]
    far = [f for f in run(bundle, today=date(2026, 8, 6)) if f.code == "health.log-gap"]
    assert near == []
    assert len(far) == 1


# --- Parse errors -----------------------------------------------------------


def test_a_parse_error_is_skipped_by_every_code(tmp_path):
    """One habit per module: okf-io already emitted `frontmatter.unparseable`,
    and a bundle with an unparseable log has a bigger problem than a stale one."""
    (tmp_path / "broken.md").write_text("---\ntype: [unclosed\n---\n\n# B\n", encoding="utf-8")
    (tmp_path / "log.md").write_text("---\ntitle: [unclosed\n---\n\n## 2020-01-01\n\n- old\n", encoding="utf-8")
    assert run(load_bundle(tmp_path)) == []


# --- Severity ---------------------------------------------------------------


def test_every_code_defaults_to_warn():
    assert {f.severity for f in run(ext_helpers.unhealthy_bundle())} == {"warn"}


def test_every_code_honours_the_severity_argument():
    found = run(ext_helpers.unhealthy_bundle(), severity="error")
    assert {f.severity for f in found} == {"error"}
    assert {f.code for f in found} == set(CODES)


def test_the_default_never_changes_conformance():
    bundle = ext_helpers.unhealthy_bundle()
    without = validate(bundle, today=TODAY)
    with_rule = validate(bundle, today=TODAY, extra_rules=[health_rule()])
    assert with_rule.ok == without.ok


def test_strict_promotes_the_house_rule_with_no_second_namespace():
    assert {f.severity for f in run(ext_helpers.unhealthy_bundle(), strict=True)} == {"error"}


# --- The topic contract -----------------------------------------------------


def test_every_code_starts_with_the_topic_prefix():
    assert CODES
    assert all(code.startswith(f"{TOPIC}.") for code in CODES)


def test_every_emitted_code_is_a_member_of_codes():
    assert {f.code for f in run(ext_helpers.unhealthy_bundle())} == set(CODES)


def test_findings_are_shape_identical_to_built_ins():
    report = validate(ext_helpers.unhealthy_bundle(), today=TODAY, extra_rules=[health_rule()])
    house = [f for f in report.findings if f.code.startswith("health.")]
    assert house
    assert all(isinstance(f, Finding) for f in house)
    assert all(f.path is not None and "." in f.code for f in house)
    assert report.findings == tuple(
        sorted(report.findings, key=lambda f: (f.path or "", f.line or 0, f.code, f.message))
    )
