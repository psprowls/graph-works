from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from work_helpers import lane_report, write_item

TODAY = date(2026, 8, 3)


def with_artifact(root: Path, path: str, filename: str) -> None:
    target = root / path / "references" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# artifact\n", encoding="utf-8")


def source_block(source_id: str, resource: str) -> str:
    return f"sources:\n  - id: {source_id}\n    resource: {resource}\n"


def test_a_source_with_no_resource_is_skipped(tmp_path: Path) -> None:
    """The built-in provenance rule owns a missing `resource` key."""
    write_item(tmp_path, "work/feature-x", "type: Feature\nwork_status: open\nsources:\n  - id: plan\n")
    codes = {
        finding.code for finding in lane_report(tmp_path, today=TODAY).findings if finding.code.startswith("targets.")
    }
    assert codes == set()


def test_an_id_disagreeing_with_its_filename_is_a_warn(tmp_path: Path) -> None:
    path = "work/feature-x"
    with_artifact(tmp_path, path, "01-design.md")
    write_item(
        tmp_path, path, "type: Feature\nwork_status: open\n" + source_block("plan", f"/{path}/references/01-design.md")
    )
    finding = lane_report(tmp_path, today=TODAY).by_code("targets.source-id-mismatch")[0]
    assert finding.severity == "warn"
    assert finding.spec == "§5.1"


@pytest.mark.parametrize(
    ("source_id", "filename"),
    [
        ("design", "01-design.md"),
        ("plan", "02-plan.md"),
        ("execute-transcript", "03-execute-transcript.jsonl"),
        ("plan-guidance", "02-plan-guidance.md"),
        ("plan-transcript-subagent-1", "02-plan-transcript-subagent-1.txt"),
        ("finish-results", "04-finish-results.md"),
        ("finish-receipt", "04-finish-receipt.md"),
    ],
)
def test_an_agreeing_id_is_silent(tmp_path: Path, source_id: str, filename: str) -> None:
    path = "work/feature-x"
    with_artifact(tmp_path, path, filename)
    write_item(
        tmp_path, path, "type: Feature\nwork_status: open\n" + source_block(source_id, f"/{path}/references/{filename}")
    )
    assert lane_report(tmp_path, today=TODAY).by_code("targets.source-id-mismatch") == ()


@pytest.mark.parametrize("filename", ["notes.md", "design.md", "01_design.md"])
def test_an_unrecognisable_filename_is_silent(tmp_path: Path, filename: str) -> None:
    path = "work/feature-x"
    with_artifact(tmp_path, path, filename)
    write_item(
        tmp_path, path, "type: Feature\nwork_status: open\n" + source_block("plan", f"/{path}/references/{filename}")
    )
    assert lane_report(tmp_path, today=TODAY).by_code("targets.source-id-mismatch") == ()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "packages" / "example").mkdir(parents=True)
    (repo / "packages" / "example" / "module.py").write_text("x = 1\n", encoding="utf-8")
    return repo


def test_an_affects_entry_naming_no_repo_path_is_an_error(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_item(vault, "work/feature-x", "type: Feature\nwork_status: open\naffects:\n  - packages/gone\n")
    finding = lane_report(vault, today=TODAY, repo_root=_repo(tmp_path)).by_code("targets.affects-missing")[0]
    assert finding.severity == "error"
    assert finding.spec == "work_tracker_okf._rules.targets"


def test_an_affects_entry_naming_a_real_repo_path_is_silent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_item(vault, "work/feature-x", "type: Feature\nwork_status: open\naffects:\n  - packages/example\n")
    assert lane_report(vault, today=TODAY, repo_root=_repo(tmp_path)).by_code("targets.affects-missing") == ()


def test_the_reserved_workspace_value_is_never_reported_missing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_item(vault, "work/feature-x", "type: Feature\nwork_status: open\naffects:\n  - gw:workspace\n")
    assert lane_report(vault, today=TODAY, repo_root=_repo(tmp_path)).by_code("targets.affects-missing") == ()


def test_a_misspelled_reserved_value_is_still_reported(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_item(vault, "work/feature-x", "type: Feature\nwork_status: open\naffects:\n  - gw:worksapce\n")
    findings = lane_report(vault, today=TODAY, repo_root=_repo(tmp_path)).by_code("targets.affects-missing")
    assert [finding.message for finding in findings] == [
        "`affects` entry 'gw:worksapce' does not exist under the repo root"
    ]


def _second_repo(tmp_path: Path) -> Path:
    other = tmp_path / "other"
    (other / "apps" / "ui").mkdir(parents=True)
    return other


def _several_roots_report(vault: Path, roots: tuple[Path, ...]):
    from okf_io import load_bundle, validate
    from work_tracker_okf.items import IGNORE
    from work_tracker_okf.rules import lane_rules

    return validate(load_bundle(vault, ignore=IGNORE), today=TODAY, extra_rules=lane_rules(repo_roots=roots))


def test_an_affects_entry_under_any_of_several_repo_roots_is_silent(tmp_path: Path) -> None:
    """A multi-repository workspace: each entry need resolve under one declared root."""
    vault = tmp_path / "vault"
    write_item(
        vault,
        "work/feature-x",
        "type: Feature\nwork_status: open\naffects:\n  - packages/example\n  - apps/ui\n",
    )
    roots = (_repo(tmp_path), _second_repo(tmp_path))
    assert _several_roots_report(vault, roots).by_code("targets.affects-missing") == ()


def test_an_affects_entry_under_none_of_several_repo_roots_is_an_error(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_item(vault, "work/feature-x", "type: Feature\nwork_status: open\naffects:\n  - apps/gone\n")
    roots = (_repo(tmp_path), _second_repo(tmp_path))
    findings = _several_roots_report(vault, roots).by_code("targets.affects-missing")
    assert [finding.severity for finding in findings] == ["error"]
    assert findings[0].message == "`affects` entry 'apps/gone' does not exist under any repo root"


def test_no_repo_root_skips_the_code_entirely(tmp_path: Path) -> None:
    write_item(tmp_path, "work/feature-x", "type: Feature\nwork_status: open\naffects:\n  - packages/gone\n")
    assert lane_report(tmp_path, today=TODAY).by_code("targets.affects-missing") == ()


def test_the_module_declares_three_codes_all_prefixed_targets() -> None:
    from work_tracker_okf._rules import targets

    assert len(targets.CODES) == 3
    assert all(code.startswith("targets.") for code in targets.CODES)


def test_the_factory_adds_exactly_one_rule_when_a_repo_root_is_injected(tmp_path: Path) -> None:
    from work_tracker_okf._rules import targets
    from work_tracker_okf._rules._common import LaneConfig

    assert len(targets.rules(LaneConfig())) == 2
    assert len(targets.rules(LaneConfig(repo_root=tmp_path))) == 3


def test_the_factory_adds_the_same_one_rule_for_repo_roots(tmp_path: Path) -> None:
    from work_tracker_okf._rules import targets
    from work_tracker_okf._rules._common import LaneConfig

    assert len(targets.rules(LaneConfig(repo_roots=(tmp_path, tmp_path / "other")))) == 3


_EPIC = "type: Epic\nwork_status: open\naffects: []\n"


@pytest.mark.parametrize("type_", ["Feature", "Bug", "TechDebt", "TestGap", "Spike"])
def test_a_nested_leaf_with_empty_affects_warns(tmp_path: Path, type_: str) -> None:
    write_item(tmp_path, "work/epic-e", _EPIC)
    prefix = {"TechDebt": "tech-debt", "TestGap": "test-gap"}.get(type_, type_.lower())
    write_item(tmp_path, f"work/epic-e/children/{prefix}-x", f"type: {type_}\nwork_status: open\naffects: []\n")
    findings = lane_report(tmp_path, today=TODAY).by_code("targets.affects-empty")
    assert [(f.severity, f.path) for f in findings] == [("warn", f"work/epic-e/children/{prefix}-x.md")]
    assert "gw:workspace" in findings[0].message


@pytest.mark.parametrize(
    ("path", "frontmatter"),
    [
        ("work/feature-root", "type: Feature\nwork_status: open\naffects: []\n"),
        ("work/release-r/children/epic-n", "type: Epic\nwork_status: open\naffects: []\n"),
        ("work/epic-e/children/feature-ws", "type: Feature\nwork_status: open\naffects:\n  - gw:workspace\n"),
        ("work/epic-e/children/feature-done", "type: Feature\nwork_status: resolved\nphase: done\naffects: []\n"),
    ],
)
def test_affects_empty_is_silent_for(tmp_path: Path, path: str, frontmatter: str) -> None:
    write_item(tmp_path, "work/epic-e", _EPIC)
    write_item(tmp_path, "work/release-r", "type: Release\nwork_status: open\naffects: []\n")
    write_item(tmp_path, path, frontmatter)
    assert lane_report(tmp_path, today=TODAY).by_code("targets.affects-empty") == ()


def test_a_nested_item_with_children_is_silent(tmp_path: Path) -> None:
    write_item(tmp_path, "work/epic-e", _EPIC)
    write_item(tmp_path, "work/epic-e/children/feature-p", "type: Feature\nwork_status: open\naffects: []\n")
    write_item(
        tmp_path, "work/epic-e/children/feature-p/children/bug-c", "type: Bug\nwork_status: open\naffects:\n  - x\n"
    )
    paths = {f.path for f in lane_report(tmp_path, today=TODAY).by_code("targets.affects-empty")}
    assert "work/epic-e/children/feature-p.md" not in paths
