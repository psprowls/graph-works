from datetime import date
from pathlib import Path

import pytest
import work_tracker_okf.compose as compose
from okf_ext.shape import load_sections
from okf_ext.tables import read_section
from okf_ext.tags import VOCABULARY_FILENAME, VocabularyError
from okf_io import load, load_bundle, validate
from work_helpers import CONFORMANT_TODAY, make_item, write_item
from work_tracker_okf import IGNORE, load_items
from work_tracker_okf.compose import (
    PLAN_HEADING,
    FilingApplication,
    FilingApplyError,
    advance_and_stamp,
    apply_file_and_reconcile,
    ensure_plan_row,
    plan_file_and_reconcile,
    plan_row_splice,
    stamp_for,
)
from work_tracker_okf.filing import FilingSeed
from work_tracker_okf.init import install_bundle
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref
from work_tracker_okf.resources import assets_root
from work_tracker_okf.rules import PLAN_TABLE_SPEC
from work_tracker_okf.vocabulary import PLAN_SOURCE_ID, SPEC_SOURCE_ID

TODAY = date(2026, 8, 22)
ITEM = "work/release-r1/children/epic-migration/children/feature-cutover"


def _snapshot_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    install_bundle(tmp_path, today=TODAY, dry_run=False)
    return tmp_path


@pytest.fixture
def section_set():
    return load_sections(assets_root() / "sections")


def _write_feature(root: Path, *, phase: str = "design", work_status: str = "open") -> Path:
    return write_item(
        root,
        ITEM,
        f"type: Feature\nwork_status: {work_status}\nphase: {phase}\nstatus: draft\n"
        "opened: 2026-08-22\nupdated: 2026-08-22\naffects: [packages/work-tracker-okf]\n",
    )


def test_stamp_for_uses_canonical_managed_names_and_reads_h1(root: Path) -> None:
    target = root / ITEM / "references" / "01-design.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Canonical design\n", encoding="utf-8")
    ref, title = stamp_for(root, make_item(ITEM, title="Cutover"), SPEC_SOURCE_ID)
    assert ref == artifact_ref(ITEM, MANAGED_ARTIFACTS["design"])
    assert ref.resource == f"/{ITEM}/references/01-design.md"
    assert title == "Canonical design"


def test_stamp_for_plan_and_fallback_title_are_canonical(tmp_path: Path) -> None:
    item = make_item(ITEM, title="Cutover")
    ref, title = stamp_for(tmp_path, item, PLAN_SOURCE_ID)
    assert ref.rel == f"{ITEM}/references/02-plan.md"
    assert title == "Plan — Cutover"


def test_stamp_for_refuses_an_unmanaged_transition_source(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        stamp_for(tmp_path, make_item(ITEM), "legacy-design-spec")


def test_plan_row_splice_is_pure_and_uses_root_absolute_resource(tmp_path: Path) -> None:
    page = _write_feature(tmp_path, phase="plan")
    document = load(page)
    ref = artifact_ref(ITEM, MANAGED_ARTIFACTS["plan"])
    before = document.body
    splice = plan_row_splice(document, ref)
    assert splice.changed and document.body == before
    assert f"Execute implementation plan: /{ITEM}/references/02-plan.md" in splice.after


def test_ensure_plan_row_is_idempotent_and_valid(tmp_path: Path) -> None:
    document = load(_write_feature(tmp_path, phase="plan"))
    ref = artifact_ref(ITEM, MANAGED_ARTIFACTS["plan"])
    assert ensure_plan_row(document, ref)
    once = document.body
    assert not ensure_plan_row(document, ref)
    assert document.body == once
    assert read_section(document.body, PLAN_HEADING, PLAN_TABLE_SPEC).state == "ok"


def test_advance_and_stamp_registers_the_canonical_design_source(root: Path) -> None:
    page = _write_feature(root)
    outcome = advance_and_stamp(
        load_bundle(root, ignore=IGNORE),
        ITEM,
        today=TODAY,
        effort="small",
        dry_run=False,
    )
    assert outcome.written and outcome.stamped is not None
    data = load(page).fm_data()
    assert data["phase"] == "plan"
    assert data["sources"][0]["id"] == "design"
    assert data["sources"][0]["resource"] == f"/{ITEM}/references/01-design.md"


def test_advance_and_stamp_defaults_to_a_write_free_dry_run(root: Path) -> None:
    page = _write_feature(root)
    before = page.read_bytes()
    outcome = advance_and_stamp(load_bundle(root, ignore=IGNORE), ITEM, today=TODAY, effort="small")
    assert not outcome.written
    assert page.read_bytes() == before


def test_plan_complete_stamps_plan_and_syncs_the_row(root: Path) -> None:
    page = _write_feature(root, phase="plan")
    outcome = advance_and_stamp(load_bundle(root, ignore=IGNORE), ITEM, today=TODAY, dry_run=False)
    data = load(page).fm_data()
    assert outcome.plan_row
    assert data["work_status"] == "accepted"
    assert data["sources"][0]["resource"] == f"/{ITEM}/references/02-plan.md"
    assert f"Execute implementation plan: /{ITEM}/references/02-plan.md" in load(page).body


def test_composed_filing_preflights_page_indexes_and_log_without_writing(root: Path, section_set) -> None:
    bundle = load_bundle(root, ignore=IGNORE)
    seed = FilingSeed(type="Epic", title="Migration", description="d", on=TODAY)
    outcome = plan_file_and_reconcile(bundle, load_items(bundle), seed, section_set)
    assert outcome.plan.refusal is None
    assert outcome.plan.filing.path == "work/epic-migration"
    assert {index.lane for index in outcome.plan.indexes} == {
        "work",
        "work/epic-migration/children",
    }
    assert any(index.lane == "work" and index.changed for index in outcome.plan.indexes)
    assert outcome.plan.log is not None and outcome.plan.log.changed
    assert outcome.application == FilingApplication()
    assert not outcome.plan.filing.target.exists()


def test_leaf_filing_does_not_plan_or_overwrite_unrelated_lane_indexes(root: Path, section_set) -> None:
    unrelated = root / "work" / "_archive" / "index.md"
    before = "human-owned concurrent bytes\n"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text(before, encoding="utf-8")
    bundle = load_bundle(root, ignore=IGNORE)
    outcome = plan_file_and_reconcile(
        bundle,
        load_items(bundle),
        FilingSeed(type="Bug", title="Narrow", description="d", on=TODAY),
        section_set,
    )

    assert [index.lane for index in outcome.plan.indexes] == ["work"]
    apply_file_and_reconcile(outcome.plan)
    assert unrelated.read_text(encoding="utf-8") == before


def test_composed_filing_applies_page_required_indexes_and_log(root: Path, section_set) -> None:
    bundle = load_bundle(root, ignore=IGNORE)
    seed = FilingSeed(type="Epic", title="Migration", description="d", on=TODAY)
    outcome = plan_file_and_reconcile(bundle, load_items(bundle), seed, section_set)
    application = apply_file_and_reconcile(outcome.plan)
    assert application.written and application.page == outcome.plan.filing.target
    assert (root / "work" / "index.md").read_text(encoding="utf-8").count("epic-migration.md") == 1
    assert (root / "work" / "epic-migration" / "children" / "index.md").is_file()
    assert "filed work/epic-migration (Epic)" in (root / "log.md").read_text(encoding="utf-8")


def test_composed_filing_rechecks_an_owned_directory_collision(root: Path, section_set) -> None:
    bundle = load_bundle(root, ignore=IGNORE)
    outcome = plan_file_and_reconcile(
        bundle,
        load_items(bundle),
        FilingSeed(type="Feature", title="Collision", description="d", on=TODAY),
        section_set,
    )
    outcome.plan.filing.owned_directory.mkdir(parents=True)
    with pytest.raises(FilingApplyError):
        apply_file_and_reconcile(outcome.plan)


def test_refused_filing_is_byte_for_byte_write_free(root: Path, section_set) -> None:
    seed = FilingSeed(type="Feature", title="Twice", description="d", on=TODAY)
    first_bundle = load_bundle(root, ignore=IGNORE)
    first = plan_file_and_reconcile(first_bundle, load_items(first_bundle), seed, section_set)
    assert apply_file_and_reconcile(first.plan).written
    before = _snapshot_bytes(root)

    second_bundle = load_bundle(root, ignore=IGNORE)
    second = plan_file_and_reconcile(second_bundle, load_items(second_bundle), seed, section_set)

    assert second.plan.refusal == "page-exists"
    assert apply_file_and_reconcile(second.plan) == FilingApplication()
    assert _snapshot_bytes(root) == before


def test_stale_log_preserves_concurrent_bytes_and_reports_partial_application(root: Path, section_set) -> None:
    bundle = load_bundle(root, ignore=IGNORE)
    outcome = plan_file_and_reconcile(
        bundle,
        load_items(bundle),
        FilingSeed(type="Feature", title="Stale log", description="d", on=TODAY),
        section_set,
    )
    assert outcome.plan.log is not None
    log_path = root / "log.md"
    concurrent = outcome.plan.log.before.encode("utf-8") + b"\nconcurrent append\n"
    log_path.write_bytes(concurrent)

    with pytest.raises(FilingApplyError) as raised:
        apply_file_and_reconcile(outcome.plan)

    assert isinstance(raised.value, compose.FilingPlanStaleError)
    assert raised.value.application.page == outcome.plan.filing.target
    assert raised.value.application.indexes == outcome.plan.indexes
    assert raised.value.application.log is None
    assert not raised.value.application.written
    assert raised.value.expected == outcome.plan.log.before.encode("utf-8")
    assert raised.value.actual == concurrent
    assert log_path.read_bytes() == concurrent


def test_rule_set_matches_the_conformant_vault_gate(conformant_root: Path) -> None:
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    report = validate(bundle, today=CONFORMANT_TODAY, extra_rules=compose.rule_set(conformant_root))
    assert [f"{finding.code} {finding.path}: {finding.message}" for finding in report.errors] == []


def test_rule_set_with_repo_root_adds_both_path_rules(conformant_root: Path) -> None:
    assert (
        len(compose.rule_set(conformant_root, repo_root=conformant_root)) == len(compose.rule_set(conformant_root)) + 2
    )


def test_rule_set_with_repo_roots_adds_both_path_rules(conformant_root: Path) -> None:
    assert (
        len(compose.rule_set(conformant_root, repo_roots=(conformant_root, conformant_root / "work")))
        == len(compose.rule_set(conformant_root)) + 2
    )


def test_rule_set_reports_a_tag_outside_the_vocabulary(root: Path) -> None:
    (root / VOCABULARY_FILENAME).write_text(
        "version: 1\ntags:\n  - name: graph-works\n",
        encoding="utf-8",
    )
    write_item(
        root,
        ITEM,
        "type: Feature\nwork_status: open\nstatus: draft\n"
        "opened: 2026-08-22\nupdated: 2026-08-22\naffects: [packages/work-tracker-okf]\n"
        "tags: [not-in-vocabulary]\n",
    )
    bundle = load_bundle(root, ignore=IGNORE)
    report = validate(bundle, today=TODAY, extra_rules=compose.rule_set(root))
    assert [f.code for f in report.errors if f.code == "tags.unknown"]


def test_rule_set_skips_the_vocabulary_rule_when_tags_yaml_is_absent(root: Path) -> None:
    (root / VOCABULARY_FILENAME).write_text(
        "version: 1\ntags:\n  - name: graph-works\n",
        encoding="utf-8",
    )
    with_vocab = compose.rule_set(root)
    (root / VOCABULARY_FILENAME).unlink()
    without_vocab = compose.rule_set(root)
    assert len(without_vocab) == len(with_vocab) - 1


def test_rule_set_propagates_a_malformed_vocabulary_as_value_error(root: Path) -> None:
    (root / VOCABULARY_FILENAME).write_text("version: 2\n", encoding="utf-8")
    with pytest.raises(VocabularyError):
        compose.rule_set(root)


def test_the_tag_vocabulary_composes_at_error(root: Path) -> None:
    (root / VOCABULARY_FILENAME).write_text(
        "version: 1\ntags:\n  - name: graph-works\n",
        encoding="utf-8",
    )
    write_item(
        root,
        ITEM,
        "type: Feature\nwork_status: open\nstatus: draft\n"
        "opened: 2026-08-22\nupdated: 2026-08-22\naffects: [packages/work-tracker-okf]\n"
        "tags: [not-in-vocabulary]\n",
    )
    bundle = load_bundle(root, ignore=IGNORE)
    report = validate(bundle, today=TODAY, extra_rules=compose.rule_set(root))
    assert {f.severity for f in report.by_code("tags.unknown")} == {"error"}
    assert not report.ok


def test_rule_set_vocabulary_findings_are_always_error(root: Path) -> None:
    (root / VOCABULARY_FILENAME).write_text(
        "version: 1\ntags:\n  - name: graph-works\n",
        encoding="utf-8",
    )
    write_item(
        root,
        ITEM,
        "type: Feature\nwork_status: open\nstatus: draft\n"
        "opened: 2026-08-22\nupdated: 2026-08-22\naffects: [packages/work-tracker-okf]\n"
        "tags: [not-in-vocabulary]\n",
    )
    bundle = load_bundle(root, ignore=IGNORE)
    report = validate(bundle, today=TODAY, extra_rules=compose.rule_set(root))
    tag_findings = [f for f in (*report.errors, *report.warnings) if f.code.startswith("tags.")]
    assert tag_findings
    assert all(f.severity == "error" for f in tag_findings)
