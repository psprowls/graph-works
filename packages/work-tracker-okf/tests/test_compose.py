from datetime import date
from pathlib import Path

from okf_ext.shape import load_sections
from okf_ext.tables import read_section
from okf_io import load, load_bundle, validate
from work_helpers import CONFORMANT_TODAY, make_item, write_item
from work_tracker_okf import IGNORE
from work_tracker_okf.compose import (
    PLAN_HEADING,
    AdvanceOutcome,
    FilingOutcome,
    advance_and_stamp,
    append_lane_log,
    ensure_plan_row,
    file_and_reconcile,
    plan_row_splice,
    rule_set,
    stamp_for,
)
from work_tracker_okf.init import install_bundle
from work_tracker_okf.paths import artifact_path
from work_tracker_okf.resources import assets_root
from work_tracker_okf.rules import PLAN_TABLE_SPEC, lane_rules
from work_tracker_okf.vocabulary import PLAN_SOURCE_ID, SPEC_SOURCE_ID

_FEATURE = "2026-03-02-epic-feature-filing-writer"


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


def _installed(tmp_path: Path):
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    return load_sections(Path(str(assets_root() / "_sections")))


def test_file_and_reconcile_defaults_to_dry_run(tmp_path: Path) -> None:
    sections = _installed(tmp_path)
    outcome = file_and_reconcile(
        tmp_path,
        type="Feature",
        title="A dry filing",
        description="D",
        on=_TODAY,
        affects=("packages/work-tracker-okf",),
        section_set=sections,
    )
    assert isinstance(outcome, FilingOutcome)
    assert outcome.path is None
    assert outcome.logged is None
    assert not (tmp_path / "work").exists()


def test_file_and_reconcile_writes_page_index_and_log(tmp_path: Path) -> None:
    sections = _installed(tmp_path)
    outcome = file_and_reconcile(
        tmp_path,
        type="Feature",
        title="A real filing",
        description="D",
        on=_TODAY,
        affects=("packages/work-tracker-okf",),
        section_set=sections,
        dry_run=False,
    )
    assert outcome.path is not None and outcome.path.is_file()
    index = (tmp_path / "work" / "index.md").read_text(encoding="utf-8")
    assert f"({outcome.plan.slug}.md)" in index
    assert outcome.logged is not None
    assert outcome.logged in (tmp_path / "log.md").read_text(encoding="utf-8")


def test_a_refused_filing_touches_nothing(tmp_path: Path) -> None:
    sections = _installed(tmp_path)
    kwargs = {
        "type": "Feature",
        "title": "Twice filed",
        "description": "D",
        "on": _TODAY,
        "affects": ("packages/work-tracker-okf",),
        "section_set": sections,
        "dry_run": False,
    }
    file_and_reconcile(tmp_path, **kwargs)
    log_before = (tmp_path / "log.md").read_bytes()
    index_before = (tmp_path / "work" / "index.md").read_bytes()
    second = file_and_reconcile(tmp_path, **kwargs)
    assert second.plan.refusal == "page-exists"
    assert second.path is None
    assert second.logged is None
    assert (tmp_path / "log.md").read_bytes() == log_before
    assert (tmp_path / "work" / "index.md").read_bytes() == index_before


def test_two_filings_share_one_dated_section(tmp_path: Path) -> None:
    sections = _installed(tmp_path)
    for title in ("First thing", "Second thing"):
        file_and_reconcile(
            tmp_path,
            type="Feature",
            title=title,
            description="D",
            on=_TODAY,
            affects=("packages/work-tracker-okf",),
            section_set=sections,
            dry_run=False,
        )
    log_text = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert log_text.count(f"## {_TODAY.isoformat()}") == 1


def test_append_lane_log_is_silent_without_a_log(tmp_path: Path) -> None:
    (tmp_path / "index.md").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "index.md").write_text("# Empty\n", encoding="utf-8")
    assert append_lane_log(tmp_path, "nothing to record", on=_TODAY) is None
