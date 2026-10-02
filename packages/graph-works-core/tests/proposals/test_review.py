"""graph_works_core.proposals.review: the four mechanical proposal checks."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from graph_works_core.proposals import run_proposal_checks, run_proposal_preview
from graph_works_core.workspace.config import load_workspace_config
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_ext.bundle import SCHEMA_DIRNAME
from test_proposals_commands import MEMBER, TARGET, TODAY, _file, _layout, _snapshot

ADR = """\
---
type: Adr
title: X
status: stable
decisions:
  - id: D1
    claim: c
    about: [pkg:o/r/code]
    constrains: [src/a.py]
---

# X
"""


@pytest.fixture
def layout_with_proposal(tmp_path: Path) -> WorkspaceLayout:
    built = _layout(tmp_path / "a")
    _file(built)
    return built


@pytest.fixture
def code_layout_with_proposal(code_layout: WorkspaceLayout) -> WorkspaceLayout:
    _file(code_layout)
    return code_layout


def _by_id(layout: WorkspaceLayout):
    return {check.id: check for check in run_proposal_checks(layout, TARGET, today=TODAY).checks}


def test_checks_on_a_clean_proposal(layout_with_proposal: WorkspaceLayout) -> None:
    result = run_proposal_checks(layout_with_proposal, TARGET, today=TODAY)
    assert result.proposal == MEMBER and result.refusal is None
    assert [(c.id, c.status) for c in result.checks] == [
        ("schema", "pass"),
        ("citations", "fail"),
        ("code-drift", "skipped"),
        ("related-adrs", "skipped"),
    ]
    assert result.checks[1].findings == ("missing source: /sources/one.md",)


def test_citations_pass_when_every_source_resolves(layout_with_proposal: WorkspaceLayout) -> None:
    (layout_with_proposal.bundle_dir / "sources").mkdir(exist_ok=True)
    (layout_with_proposal.bundle_dir / "sources/one.md").write_text(
        "---\ntype: Source\ntitle: One\n---\n", encoding="utf-8"
    )
    assert _by_id(layout_with_proposal)["citations"].status == "pass"


def test_schema_fails_when_the_promoted_page_is_invalid(layout_with_proposal: WorkspaceLayout) -> None:
    path = layout_with_proposal.bundle_dir / MEMBER
    path.write_text(path.read_text(encoding="utf-8").replace("description: d\n", ""), encoding="utf-8")
    check = _by_id(layout_with_proposal)["schema"]
    assert check.status == "fail"
    assert check.findings == ("promoted page: at `description`: '' should be non-empty",)


def test_schema_is_skipped_without_a_schema_dir(layout_with_proposal: WorkspaceLayout) -> None:
    shutil.rmtree(load_workspace_config(layout_with_proposal).declarations_dir / SCHEMA_DIRNAME)
    assert _by_id(layout_with_proposal)["schema"].status == "skipped"


def test_code_drift_and_related_adrs(code_layout_with_proposal: WorkspaceLayout) -> None:
    layout = code_layout_with_proposal
    path = layout.bundle_dir / MEMBER
    path.write_text(path.read_text(encoding="utf-8") + "\nSee `src/a.py:1` and `src/gone.py:3`.\n", encoding="utf-8")
    (layout.bundle_dir / "adrs").mkdir(exist_ok=True)
    (layout.bundle_dir / "adrs/2026-01-01-x.md").write_text(ADR, encoding="utf-8")
    checks = _by_id(layout)
    assert checks["code-drift"].status == "fail"
    assert checks["code-drift"].findings == ("missing: src/gone.py:3 (line 31)",)
    assert checks["related-adrs"].status == "warn"
    assert checks["related-adrs"].findings == ("adrs/2026-01-01-x#D1: c",)


def test_related_adrs_pass_when_nothing_overlaps(code_layout_with_proposal: WorkspaceLayout) -> None:
    layout = code_layout_with_proposal
    path = layout.bundle_dir / MEMBER
    path.write_text(path.read_text(encoding="utf-8") + "\nSee `src/a.py:1`.\n", encoding="utf-8")
    checks = _by_id(layout)
    assert (checks["code-drift"].status, checks["related-adrs"].status) == ("pass", "pass")


def test_no_proposal(layout_with_proposal: WorkspaceLayout) -> None:
    result = run_proposal_checks(layout_with_proposal, "docs/nope.md", today=TODAY)
    assert result.refusal == "no-proposal" and result.checks == () and result.proposal is None


def test_preview_create_mode(layout_with_proposal: WorkspaceLayout) -> None:
    preview = run_proposal_preview(layout_with_proposal, TARGET, today=TODAY)
    assert preview.mode == "create" and preview.base is None and preview.diff is None
    assert preview.rendered is not None and preview.rendered.startswith("---\n")
    assert "type: Explanation" in preview.rendered


def test_preview_update_mode_diffs_against_the_target(layout_with_proposal: WorkspaceLayout) -> None:
    page = layout_with_proposal.bundle_dir / TARGET
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Explanation\ntitle: Old title\ndescription: d\n---\n\n## Context\n\nx\n",
        encoding="utf-8",
        newline="",
    )
    preview = run_proposal_preview(layout_with_proposal, TARGET, today=TODAY)
    assert preview.mode == "update"
    assert preview.base == page.read_text(encoding="utf-8")
    assert preview.diff is not None and preview.diff.startswith(f"--- a/{TARGET}\n+++ b/{TARGET}\n")
    assert page.read_text(encoding="utf-8") == preview.base


def test_preview_writes_nothing(layout_with_proposal: WorkspaceLayout) -> None:
    before = _snapshot(layout_with_proposal.bundle_dir)
    run_proposal_preview(layout_with_proposal, TARGET, today=TODAY)
    assert _snapshot(layout_with_proposal.bundle_dir) == before


def test_preview_no_proposal(layout_with_proposal: WorkspaceLayout) -> None:
    assert run_proposal_preview(layout_with_proposal, "docs/nope.md", today=TODAY).refusal == "no-proposal"


ADR_TITLE = "Amend typed CLI"
ADR_FILED = "adrs/amend-typed-cli.md"
ADR_PROMOTED = "adrs/2026-08-23-amend-typed-cli.md"


def _adr_layout(root: Path) -> WorkspaceLayout:
    """An ADR proposal filed under an undated target, so promotion writes the dated member instead."""
    built = _layout(root)
    _file(built, lane="adr", title=ADR_TITLE)
    return built


def _write_page(layout: WorkspaceLayout, member: str, text: str) -> str:
    path = layout.bundle_dir / member
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return text


def test_preview_create_when_the_filed_target_exists_but_promotion_writes_a_new_member(tmp_path: Path) -> None:
    built = _adr_layout(tmp_path / "create")
    _write_page(built, ADR_FILED, "---\ntype: Adr\ntitle: Old\ndescription: d\n---\n\nold\n")
    preview = run_proposal_preview(built, ADR_FILED, today=TODAY)
    assert preview.refusal is None and not preview.refusals
    assert (preview.mode, preview.member) == ("create", ADR_PROMOTED)
    assert preview.base is None and preview.diff is None and preview.rendered is not None


def test_preview_update_when_promotion_updates_a_page_the_filed_target_is_not(tmp_path: Path) -> None:
    built = _adr_layout(tmp_path / "update")
    base = _write_page(
        built, ADR_PROMOTED, "---\ntype: Adr\ntitle: Old\ndescription: d\nsources: []\n---\n\n## Context\n\nx\n"
    )
    preview = run_proposal_preview(built, ADR_FILED, today=TODAY)
    assert preview.refusal is None and not preview.refusals
    assert (preview.mode, preview.member) == ("update", ADR_PROMOTED)
    assert preview.base == base
    assert preview.diff is not None
    changed = [line for line in preview.diff.splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    assert any(line.startswith("-") for line in changed) and any(line.startswith("+") for line in changed)
