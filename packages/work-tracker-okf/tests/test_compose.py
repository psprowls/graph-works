from datetime import date
from pathlib import Path
from typing import Literal, get_type_hints

import pytest
import work_tracker_okf.compose as compose
from okf_ext.shape import load_sections
from okf_ext.tables import read_section
from okf_io import Bundle, load, load_bundle, validate
from work_helpers import CONFORMANT_TODAY, make_item, write_item
from work_tracker_okf import IGNORE, load_items
from work_tracker_okf.compose import (
    PLAN_HEADING,
    AdvanceOutcome,
    FilingApplication,
    FilingApplyError,
    FilingCompositionPlan,
    advance_and_stamp,
    append_lane_log,
    apply_file_and_reconcile,
    ensure_plan_row,
    plan_file_and_reconcile,
    plan_row_splice,
    rule_set,
    stamp_for,
)
from work_tracker_okf.filing import FilingRefusal, FilingSeed
from work_tracker_okf.init import install_bundle
from work_tracker_okf.paths import artifact_path
from work_tracker_okf.resources import assets_root
from work_tracker_okf.rules import PLAN_TABLE_SPEC, lane_rules
from work_tracker_okf.vocabulary import PLAN_SOURCE_ID, SPEC_SOURCE_ID

_FEATURE = "2026-03-02-epic-feature-filing-writer"


def snapshot_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


@pytest.fixture
def bundle(tmp_path: Path) -> Bundle:
    root = tmp_path / "bundle"
    install_bundle(root, today=date(2026, 8, 18), dry_run=False)
    return load_bundle(root, ignore=IGNORE)


@pytest.fixture
def section_set():
    return load_sections(assets_root() / "_sections")


def seed() -> FilingSeed:
    return FilingSeed(
        type="Feature",
        title="Child",
        description="d",
        on=date(2026, 8, 18),
        affects=("packages/work-tracker-okf",),
    )


def break_log_order(path: Path) -> None:
    path.write_text(
        "# Log\n\n## 2026-12-01\n\n- newer\n\n## 2026-12-15\n\n- misplaced\n",
        encoding="utf-8",
    )


def test_composed_dry_run_preflights_page_index_and_log_without_writing(bundle, section_set) -> None:
    before = snapshot_bytes(bundle.root)
    outcome = plan_file_and_reconcile(bundle, load_items(bundle), seed(), section_set)
    assert outcome.plan.filing.refusal is None
    assert outcome.plan.index.changed
    assert outcome.plan.log is not None and outcome.plan.log.changed
    assert outcome.application == FilingApplication()
    assert snapshot_bytes(bundle.root) == before


def test_filing_composition_refusal_annotation_is_closed() -> None:
    expected = FilingRefusal | Literal["index-refused", "log-refused"] | None

    assert get_type_hints(FilingCompositionPlan)["refusal"] == expected


def test_expected_index_or_log_refusal_prevents_every_write(bundle, section_set) -> None:
    break_log_order(bundle.root / "log.md")
    before = snapshot_bytes(bundle.root)
    broken_bundle = load_bundle(bundle.root, ignore=IGNORE)
    outcome = plan_file_and_reconcile(broken_bundle, load_items(broken_bundle), seed(), section_set)
    assert outcome.plan.refusal == "log-refused"
    assert apply_file_and_reconcile(outcome.plan).written is False
    assert snapshot_bytes(bundle.root) == before


def test_composed_plan_uses_the_loaded_log_snapshot_if_the_file_disappears(bundle, section_set) -> None:
    log_path = bundle.root / "log.md"
    log_path.unlink()
    outcome = plan_file_and_reconcile(bundle, load_items(bundle), seed(), section_set)
    assert outcome.plan.refusal is None
    assert outcome.plan.log is not None and outcome.plan.log.changed
    assert not log_path.exists()


def test_apply_file_and_reconcile_commits_the_preflighted_effects(bundle, section_set) -> None:
    outcome = plan_file_and_reconcile(bundle, load_items(bundle), seed(), section_set)
    application = apply_file_and_reconcile(outcome.plan)
    assert application.page == outcome.plan.filing.target
    assert application.indexes == (outcome.plan.index,)
    assert application.log == outcome.plan.log
    assert application.written is True
    assert application.page.is_file()
    assert f"({outcome.plan.filing.slug}.md)" in (bundle.root / "work" / "index.md").read_text(encoding="utf-8")
    assert outcome.plan.log is not None
    assert outcome.plan.log.entry in (bundle.root / "log.md").read_text(encoding="utf-8")


def test_stale_log_snapshot_preserves_concurrent_bytes_and_reports_partial_application(bundle, section_set) -> None:
    outcome = plan_file_and_reconcile(bundle, load_items(bundle), seed(), section_set)
    assert outcome.plan.log is not None
    log_path = bundle.root / "log.md"
    concurrent = outcome.plan.log.before.encode("utf-8") + b"\nconcurrent append\n"
    log_path.write_bytes(concurrent)

    with pytest.raises(FilingApplyError) as raised:
        apply_file_and_reconcile(outcome.plan)

    assert isinstance(raised.value, compose.FilingPlanStaleError)
    assert raised.value.application.page == outcome.plan.filing.target
    assert raised.value.application.indexes == (outcome.plan.index,)
    assert raised.value.application.log is None
    assert raised.value.application.written is False
    assert raised.value.expected == outcome.plan.log.before.encode("utf-8")
    assert raised.value.actual == concurrent
    assert log_path.read_bytes() == concurrent


@pytest.mark.parametrize("collision", ["page", "work-directory"])
def test_apply_rechecks_filing_collisions_created_after_planning(bundle, section_set, collision) -> None:
    outcome = plan_file_and_reconcile(bundle, load_items(bundle), seed(), section_set)
    if collision == "page":
        outcome.plan.filing.target.parent.mkdir(parents=True, exist_ok=True)
        outcome.plan.filing.target.write_bytes(b"foreign page")
    else:
        outcome.plan.filing.work_directory.mkdir(parents=True)
    before = snapshot_bytes(bundle.root)

    with pytest.raises(FilingApplyError) as raised:
        apply_file_and_reconcile(outcome.plan)

    assert raised.value.application == FilingApplication()
    assert isinstance(raised.value.__cause__, FileExistsError)
    assert snapshot_bytes(bundle.root) == before
    if collision == "page":
        assert outcome.plan.filing.target.read_bytes() == b"foreign page"
    else:
        assert not outcome.plan.filing.target.exists()


def test_rule_set_is_the_tuple_the_gate_is_asserted_against(conformant_root: Path) -> None:
    """C6-M: one rule set, shared by `lint` and `advance`. If this drifts from
    `test_conformant_vault.py`'s hand-built tuple, two commands mean two
    different things by 'clean'."""
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    report = validate(bundle, today=CONFORMANT_TODAY, extra_rules=rule_set(conformant_root))
    assert [f"{f.code} {f.path}: {f.message}" for f in report.errors] == []


def test_rule_set_raises_the_installed_house_rules_to_error(conformant_root: Path) -> None:
    """`section_rule` defaults to `warn`; a gate a malformed section can pass is
    not a gate."""
    (conformant_root / "work" / f"{_FEATURE}.md").write_text(
        "---\ntype: Feature\ntitle: T\ndescription: D\nworkflow_status: open\n"
        "opened: 2026-03-02\nupdated: 2026-03-02\nstatus: draft\n---\n\nno sections at all\n",
        encoding="utf-8",
    )
    bundle = load_bundle(conformant_root, ignore=IGNORE)
    report = validate(bundle, today=CONFORMANT_TODAY, extra_rules=rule_set(conformant_root))
    assert any(f.code.startswith("sections.") for f in report.errors)


def test_rule_set_takes_declarations_from_the_override(tmp_path: Path, conformant_root: Path) -> None:
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    try:
        rule_set(conformant_root, declarations_dir=empty)
    except (OSError, ValueError):
        return
    raise AssertionError("a declarations dir with no _schema/ must raise")


def test_rule_set_with_a_repo_root_adds_the_two_path_rules(conformant_root: Path) -> None:
    assert len(rule_set(conformant_root, repo_root=conformant_root)) == len(rule_set(conformant_root)) + 2


def test_stamp_for_reads_the_artifacts_own_h1(conformant_root: Path) -> None:
    """C6-F: the fixture authors `Design spec — the filing writer` for an item
    titled `The filing writer`. Deriving would give a different string, and the
    two would disagree cosmetically forever."""
    item = make_item(_FEATURE, title="The filing writer")
    ref, title = stamp_for(conformant_root, item, SPEC_SOURCE_ID)
    assert ref.source_id == SPEC_SOURCE_ID
    assert ref.resource == f"/work/{_FEATURE}/references/01-design-spec.md"
    assert title == "Design spec — the filing writer"


def test_stamp_for_reads_the_plan_artifacts_h1(conformant_root: Path) -> None:
    item = make_item(_FEATURE, title="The filing writer")
    ref, title = stamp_for(conformant_root, item, PLAN_SOURCE_ID)
    assert ref.rel == f"work/{_FEATURE}/references/02-plan-plan.md"
    assert title == "Plan — the filing writer"


def test_stamp_for_derives_a_label_when_the_artifact_is_absent(tmp_path: Path) -> None:
    item = make_item("2026-08-11-bug-nothing-here", title="Nothing here")
    _, title = stamp_for(tmp_path, item, SPEC_SOURCE_ID)
    assert title == "Design spec — Nothing here"


def test_stamp_for_derives_a_label_when_the_artifact_has_no_h1(tmp_path: Path) -> None:
    slug = "2026-08-11-bug-headless"
    target = tmp_path / "work" / slug / "references" / "01-design-spec.md"
    target.parent.mkdir(parents=True)
    target.write_text("## Not a level one\n\nbody\n", encoding="utf-8")
    _, title = stamp_for(tmp_path, make_item(slug, title="Headless"), SPEC_SOURCE_ID)
    assert title == "Design spec — Headless"


def test_stamp_for_ignores_a_hash_inside_a_fence(tmp_path: Path) -> None:
    slug = "2026-08-11-bug-fenced"
    target = tmp_path / "work" / slug / "references" / "01-design-spec.md"
    target.parent.mkdir(parents=True)
    target.write_text("```sh\n# not a heading\n```\n\n# The real one\n", encoding="utf-8")
    _, title = stamp_for(tmp_path, make_item(slug, title="Fenced"), SPEC_SOURCE_ID)
    assert title == "The real one"


def test_stamp_for_follows_an_archived_item_into_the_archive(tmp_path: Path) -> None:
    item = make_item("2026-08-11-bug-gone", title="Gone", archived=True)
    ref, _ = stamp_for(tmp_path, item, SPEC_SOURCE_ID)
    assert ref.rel == "work/_archive/2026-08-11-bug-gone/references/01-design-spec.md"


def test_stamp_for_refuses_an_id_no_transition_carries(tmp_path: Path) -> None:
    """Caller error: `Transition.stamp_source` carries exactly two values."""
    try:
        stamp_for(tmp_path, make_item("s"), "results-execute")
    except KeyError:
        return
    raise AssertionError("an unknown stamp source must raise")


def test_no_clock_is_read(tmp_path: Path) -> None:
    """`compose` takes `today=` everywhere; only `cli.py` reads the clock."""
    source = (Path(__file__).parent.parent / "src" / "work_tracker_okf" / "compose.py").read_text(encoding="utf-8")
    assert "datetime.now" not in source
    assert "date.today" not in source
    assert str(date) or True  # `date` is imported for the signatures, not for a read


_ROW_SLUG = "2026-08-11-feature-row-target"
_ROW_FM = "type: Feature\nworkflow_status: open\nopened: 2026-08-11\nupdated: 2026-08-11\nstatus: draft\n"


def _page(tmp_path):
    write_item(tmp_path, _ROW_SLUG, _ROW_FM)
    return load(tmp_path / "work" / f"{_ROW_SLUG}.md")


def test_plan_row_splice_writes_nothing(tmp_path: Path) -> None:
    document = _page(tmp_path)
    before = document.body
    splice = plan_row_splice(document, artifact_path(_ROW_SLUG, "plan", "plan"))
    assert splice.changed
    assert document.body == before


def test_ensure_plan_row_appends_the_fixed_row(tmp_path: Path) -> None:
    document = _page(tmp_path)
    ref = artifact_path(_ROW_SLUG, "plan", "plan")
    assert ensure_plan_row(document, ref) is True
    assert f"Execute implementation plan: {ref.resource}" in document.body
    assert "Implementation lands and the item is resolved" in document.body
    assert "Workflow plan stage complete" in document.body


def test_ensure_plan_row_is_idempotent(tmp_path: Path) -> None:
    document = _page(tmp_path)
    ref = artifact_path(_ROW_SLUG, "plan", "plan")
    ensure_plan_row(document, ref)
    once = document.body
    assert ensure_plan_row(document, ref) is False
    assert document.body == once


def test_the_spliced_table_reads_ok(tmp_path: Path) -> None:
    document = _page(tmp_path)
    ensure_plan_row(document, artifact_path(_ROW_SLUG, "plan", "plan"))
    assert read_section(document.body, PLAN_HEADING, PLAN_TABLE_SPEC).state == "ok"


def test_the_row_action_is_invisible_to_action_target_missing(tmp_path: Path) -> None:
    """The root-absolute `resource` spelling is load-bearing: `_PATH_RE` cannot
    match a token with a leading slash, so a bundle path is never mistaken for
    a repo path. The bundle-relative spelling would report an error on every
    advanced item the moment anyone passes `--repo-root`."""
    document = _page(tmp_path)
    ensure_plan_row(document, artifact_path(_ROW_SLUG, "plan", "plan"))
    document.save()
    report = validate(
        load_bundle(tmp_path, ignore=IGNORE),
        today=date(2026, 8, 11),
        extra_rules=lane_rules(repo_root=tmp_path),
    )
    assert not [f for f in report.findings if f.code == "plan.action-target-missing"]


def test_ensure_plan_row_creates_a_missing_section(tmp_path: Path) -> None:
    """`splice_text`'s `create=True` default: a body with no `## Plan` gains
    heading, table and row rather than silently dropping the row."""
    write_item(tmp_path, "2026-08-11-feature-no-plan-heading", _ROW_FM, body="\n## Notes / log\n")
    document = load(tmp_path / "work" / "2026-08-11-feature-no-plan-heading.md")
    assert ensure_plan_row(document, artifact_path("2026-08-11-feature-no-plan-heading", "plan", "plan")) is True
    assert f"## {PLAN_HEADING}" in document.body


_TD_SLUG = "2026-08-11-tech-debt-compose-the-cli"
_TD_FM = (
    "type: TechDebt\nworkflow_status: open\nphase: design\nstatus: draft\n"
    "opened: 2026-08-11\nupdated: 2026-08-11\naffects:\n  - packages/work-tracker-okf\n"
)
_TODAY = date(2026, 8, 11)


def _vault(tmp_path: Path) -> Path:
    from work_tracker_okf.init import install_bundle

    write_item(tmp_path, _TD_SLUG, _TD_FM)
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    return tmp_path


def test_advance_and_stamp_defaults_to_dry_run(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    page = root / "work" / f"{_TD_SLUG}.md"
    before = page.read_bytes()
    outcome = advance_and_stamp(load_bundle(root, ignore=IGNORE), _TD_SLUG, today=_TODAY, effort="small")
    assert isinstance(outcome, AdvanceOutcome)
    assert outcome.written is False
    assert outcome.stamped is not None
    assert page.read_bytes() == before


def test_advance_and_stamp_writes_all_three_effects_once(tmp_path: Path) -> None:
    """C6-E: `advance.apply` sets the frontmatter, `sources.upsert` merges the
    stamp, `ensure_plan_row` splices the body, then one `Document.save()`."""
    root = _vault(tmp_path)
    outcome = advance_and_stamp(load_bundle(root, ignore=IGNORE), _TD_SLUG, today=_TODAY, effort="small", dry_run=False)
    assert outcome.written is True
    text = (root / "work" / f"{_TD_SLUG}.md").read_text(encoding="utf-8")
    assert "phase: execute" in text
    assert "effort: small" in text
    assert "id: design-spec" in text
    assert f"/work/{_TD_SLUG}/references/01-design-spec.md" in text


def test_the_stamp_is_unconditional(tmp_path: Path) -> None:
    """C6-G: no existence check. The pointer is written, and the caller's
    post-write lint reports `targets.artifact-missing` immediately."""
    root = _vault(tmp_path)
    advance_and_stamp(load_bundle(root, ignore=IGNORE), _TD_SLUG, today=_TODAY, effort="small", dry_run=False)
    assert not (root / "work" / _TD_SLUG / "references" / "01-design-spec.md").exists()
    report = validate(load_bundle(root, ignore=IGNORE), today=_TODAY, extra_rules=lane_rules(repo_root=None))
    assert any(f.code == "targets.artifact-missing" for f in report.findings)


def test_a_refusal_writes_nothing(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    page = root / "work" / f"{_TD_SLUG}.md"
    before = page.read_bytes()
    outcome = advance_and_stamp(load_bundle(root, ignore=IGNORE), _TD_SLUG, today=_TODAY, dry_run=False)
    assert outcome.plan.refusal == "effort-required"
    assert outcome.written is False
    assert outcome.stamped is None
    assert page.read_bytes() == before


def test_an_unknown_slug_refuses(tmp_path: Path) -> None:
    outcome = advance_and_stamp(load_bundle(_vault(tmp_path), ignore=IGNORE), "nope", today=_TODAY, dry_run=False)
    assert outcome.plan.refusal == "unknown-slug"
    assert outcome.written is False


def test_the_plan_row_lands_on_the_plan_complete_transition(tmp_path: Path) -> None:
    """`sync_plan_table` is True on exactly one transition -- plan-complete ->
    execute -- where `stamp_source` is `plan`. Both fire in one pass."""
    from work_tracker_okf.init import install_bundle

    slug = "2026-08-11-feature-planned-thing"
    write_item(
        tmp_path,
        slug,
        "type: Feature\nworkflow_status: open\nphase: plan\nstatus: stable\neffort: medium\n"
        "opened: 2026-08-11\nupdated: 2026-08-11\naffects:\n  - packages/work-tracker-okf\n",
    )
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    outcome = advance_and_stamp(load_bundle(tmp_path, ignore=IGNORE), slug, today=_TODAY, dry_run=False)
    assert outcome.plan_row is True
    text = (tmp_path / "work" / f"{slug}.md").read_text(encoding="utf-8")
    assert f"Execute implementation plan: /work/{slug}/references/02-plan-plan.md" in text
    assert "id: plan" in text


def _plan_at(root: Path, filing_seed: FilingSeed):
    loaded = load_bundle(root, ignore=IGNORE)
    return plan_file_and_reconcile(
        loaded,
        load_items(loaded),
        filing_seed,
        load_sections(assets_root() / "_sections"),
    )


def test_a_refused_filing_touches_nothing(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    filing_seed = FilingSeed(type="Feature", title="Twice filed", description="D", on=_TODAY)
    apply_file_and_reconcile(_plan_at(tmp_path, filing_seed).plan)
    before = snapshot_bytes(tmp_path)
    second = _plan_at(tmp_path, filing_seed)
    assert second.plan.filing.refusal == "page-exists"
    assert second.plan.refusal == "page-exists"
    assert apply_file_and_reconcile(second.plan) == FilingApplication()
    assert snapshot_bytes(tmp_path) == before


def test_two_filings_share_one_dated_section(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    for title in ("First thing", "Second thing"):
        outcome = _plan_at(tmp_path, FilingSeed(type="Feature", title=title, description="D", on=_TODAY))
        assert apply_file_and_reconcile(outcome.plan).written
    log_text = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert log_text.count(f"## {_TODAY.isoformat()}") == 1


def test_an_io_failure_carries_the_completed_effects_and_original_cause(bundle, section_set, monkeypatch) -> None:
    outcome = plan_file_and_reconcile(bundle, load_items(bundle), seed(), section_set)
    log_path = bundle.root / "log.md"
    original = Path.replace

    def fail_log(path: Path, target: Path) -> Path:
        if target == log_path:
            raise OSError("log unavailable")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", fail_log)
    with pytest.raises(FilingApplyError) as raised:
        apply_file_and_reconcile(outcome.plan)
    assert raised.value.application.page == outcome.plan.filing.target
    assert raised.value.application.indexes == (outcome.plan.index,)
    assert raised.value.application.log is None
    assert raised.value.application.written is False
    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "log unavailable"
    assert not tuple(bundle.root.glob(".log.md.*.tmp"))


def test_append_lane_log_is_silent_without_a_log(tmp_path: Path) -> None:
    (tmp_path / "index.md").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "index.md").write_text("# Empty\n", encoding="utf-8")
    assert append_lane_log(tmp_path, "nothing to record", on=_TODAY) is None


def test_append_lane_log_forwards_to_the_shared_appender(tmp_path, monkeypatch):
    """The lane wrapper owns *which* commands log; the append itself is
    `okf_ext.logs`'. Pinning the delegation here means a future edit to one
    cannot silently fork the other."""
    seen = {}

    def fake(root, entry, *, on):
        seen.update(root=root, entry=entry, on=on)
        return entry

    monkeypatch.setattr("work_tracker_okf.compose.append_entry", fake)

    assert append_lane_log(tmp_path, "recorded", on=_TODAY) == "recorded"
    assert seen == {"root": tmp_path, "entry": "recorded", "on": _TODAY}


def test_compose_keeps_no_private_copy_of_the_appender():
    """The move exists to stop a second implementation drifting from the
    first. A reintroduced private helper is that drift, one commit early."""
    import work_tracker_okf.compose as compose

    for name in ("_log_lock_path", "_locked_log", "_atomic_replace"):
        assert not hasattr(compose, name), name
