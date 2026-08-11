from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from work_helpers import lane_report, write_item

TODAY = date(2026, 8, 3)


def with_artifact(root: Path, slug: str, filename: str) -> None:
    """A real member under the item's `references/` tree. `IGNORE` makes it not a
    concept; `has_member` still sees it, which is exactly the distinction
    `targets.artifact-missing` is asking about."""
    target = root / "work" / slug / "references" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# artifact\n", encoding="utf-8")


def source_block(source_id: str, resource: str) -> str:
    return f"sources:\n  - id: {source_id}\n    resource: {resource}\n"


# --- targets.artifact-missing -----------------------------------------------


def test_a_resource_naming_no_member_is_a_warn(tmp_path: Path) -> None:
    slug = "2026-08-01-feature-x"
    write_item(
        tmp_path,
        slug,
        "type: Feature\nworkflow_status: open\n"
        + source_block("design-spec", f"/work/{slug}/references/01-design-spec.md"),
    )
    finding = lane_report(tmp_path, today=TODAY).by_code("targets.artifact-missing")[0]
    assert finding.severity == "warn"
    assert finding.spec == "§5.1"


def test_a_resource_naming_a_real_member_is_silent(tmp_path: Path) -> None:
    slug = "2026-08-01-feature-x"
    with_artifact(tmp_path, slug, "01-design-spec.md")
    write_item(
        tmp_path,
        slug,
        "type: Feature\nworkflow_status: open\n"
        + source_block("design-spec", f"/work/{slug}/references/01-design-spec.md"),
    )
    assert lane_report(tmp_path, today=TODAY).by_code("targets.artifact-missing") == ()


def test_a_source_with_no_resource_is_skipped(tmp_path: Path) -> None:
    """`provenance.source-resource-missing` owns it."""
    write_item(tmp_path, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\nsources:\n  - id: plan\n")
    codes = {f.code for f in lane_report(tmp_path, today=TODAY).findings if f.code.startswith("targets.")}
    assert codes == set()


def test_a_broken_resource_is_not_also_reported_for_its_id(tmp_path: Path) -> None:
    slug = "2026-08-01-feature-x"
    write_item(
        tmp_path,
        slug,
        "type: Feature\nworkflow_status: open\n" + source_block("plan", f"/work/{slug}/references/01-design-spec.md"),
    )
    report = lane_report(tmp_path, today=TODAY)
    assert report.by_code("targets.artifact-missing") != ()
    assert report.by_code("targets.source-id-mismatch") == ()


# --- targets.source-id-mismatch ---------------------------------------------


def test_an_id_disagreeing_with_its_filename_is_a_warn(tmp_path: Path) -> None:
    slug = "2026-08-01-feature-x"
    with_artifact(tmp_path, slug, "01-design-spec.md")
    write_item(
        tmp_path,
        slug,
        "type: Feature\nworkflow_status: open\n" + source_block("plan", f"/work/{slug}/references/01-design-spec.md"),
    )
    finding = lane_report(tmp_path, today=TODAY).by_code("targets.source-id-mismatch")[0]
    assert finding.severity == "warn"
    assert finding.spec == "§5.1"


@pytest.mark.parametrize(
    ("source_id", "filename"),
    [
        ("design-spec", "01-design-spec.md"),
        ("plan", "02-plan-plan.md"),
        ("transcript-execute", "03-execute-transcript.jsonl"),
        ("guidance-plan", "02-plan-guidance.md"),
        ("transcript-plan-subagent-1", "02-plan-transcript-subagent-1.txt"),
        ("results-finish", "04-finish-results.md"),
    ],
)
def test_an_agreeing_id_is_silent(tmp_path: Path, source_id: str, filename: str) -> None:
    """The fifth row is the suffix rule: `SOURCE_ID_PATTERN` admits it and the
    harvest item needs it, so the comparison is prefix-with-hyphen, not equality."""
    slug = "2026-08-01-feature-x"
    with_artifact(tmp_path, slug, filename)
    write_item(
        tmp_path,
        slug,
        "type: Feature\nworkflow_status: open\n" + source_block(source_id, f"/work/{slug}/references/{filename}"),
    )
    assert lane_report(tmp_path, today=TODAY).by_code("targets.source-id-mismatch") == ()


@pytest.mark.parametrize("filename", ["notes.md", "01-design.md", "01-design-widget.md", "01-review-spec.md"])
def test_an_unrecognisable_filename_is_silent(tmp_path: Path, filename: str) -> None:
    """`source_id_for` is the module's one raising door; its `ValueError` must
    never escape as a lint failure."""
    slug = "2026-08-01-feature-x"
    with_artifact(tmp_path, slug, filename)
    write_item(
        tmp_path,
        slug,
        "type: Feature\nworkflow_status: open\n" + source_block("plan", f"/work/{slug}/references/{filename}"),
    )
    assert lane_report(tmp_path, today=TODAY).by_code("targets.source-id-mismatch") == ()


def test_a_spec_outside_design_is_silent_rather_than_raising(tmp_path: Path) -> None:
    """`source_id_for("plan", "spec")` raises; the rule swallows it."""
    slug = "2026-08-01-feature-x"
    with_artifact(tmp_path, slug, "02-plan-spec.md")
    write_item(
        tmp_path,
        slug,
        "type: Feature\nworkflow_status: open\n" + source_block("plan", f"/work/{slug}/references/02-plan-spec.md"),
    )
    assert lane_report(tmp_path, today=TODAY).by_code("targets.source-id-mismatch") == ()


# --- targets.affects-missing ------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "packages" / "example").mkdir(parents=True)
    (repo / "packages" / "example" / "module.py").write_text("x = 1\n", encoding="utf-8")
    return repo


def test_an_affects_entry_naming_no_repo_path_is_an_error(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_item(vault, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\naffects:\n  - packages/gone\n")
    finding = lane_report(vault, today=TODAY, repo_root=_repo(tmp_path)).by_code("targets.affects-missing")[0]
    assert finding.severity == "error"
    assert finding.spec == "work_tracker_okf._rules.targets"


def test_an_affects_entry_naming_a_real_repo_path_is_silent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_item(vault, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\naffects:\n  - packages/example\n")
    assert lane_report(vault, today=TODAY, repo_root=_repo(tmp_path)).by_code("targets.affects-missing") == ()


def test_no_repo_root_skips_the_code_entirely(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_item(vault, "2026-08-01-feature-x", "type: Feature\nworkflow_status: open\naffects:\n  - packages/gone\n")
    assert lane_report(vault, today=TODAY).by_code("targets.affects-missing") == ()


# --- the module's shape -----------------------------------------------------


def test_the_module_declares_three_codes_all_prefixed_targets() -> None:
    from work_tracker_okf._rules import targets

    assert len(targets.CODES) == 3
    assert all(code.startswith("targets.") for code in targets.CODES)


def test_the_factory_adds_exactly_one_rule_when_a_repo_root_is_injected(tmp_path: Path) -> None:
    from work_tracker_okf._rules import targets
    from work_tracker_okf._rules._common import LaneConfig

    assert len(targets.rules(LaneConfig())) == 1
    assert len(targets.rules(LaneConfig(repo_root=tmp_path))) == 2
