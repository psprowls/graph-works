"""`epic_brief`: the per-epic carried-context brief for a child of an Epic/Release."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from code_graph_io.tokens import count_tokens
from graph_works_core.guidance.assembly import covered_epic
from graph_works_core.work import epic_brief as eb
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.landed import BaselineComparison
from okf_io import load_bundle
from test_run_next_carried import _layout
from work_tracker_okf.items import IGNORE, load_items

EPIC = "work/epic-a"
CHILD = f"{EPIC}/children/feature-a"
SIB = f"{EPIC}/children/feature-b"
OLD = f"{EPIC}/children/feature-c"
CODE = "a" * 40
WS = "b" * 40
SHA_B = "c" * 40
SHA_C = "d" * 40

PLAN = (
    "# Plan\n\n## Children\n\n| # | Path | Type | Summary |\n| --- | --- | --- | --- |\n"
    f"| 1 | [feature-a](/{CHILD}.md) | Feature | Does A \\| carefully. |\n"
    f"| 2 | [feature-b](/{SIB}.md) | Feature | Does B. |\n"
)
DESIGN = (
    "# Design\n\n## Child index\n\n| # | Title | Type | Summary |\n| --- | --- | --- | --- |\n"
    "| 1 | Feature A | Feature | Does A by design. |\n"
)


def _answered(n: int, q: str, answer: str | None = "Yes.", affects: str | None = None) -> str:
    meta = f"affects: [{affects}]\n" if affects else ""
    prose = f"\n**Answer:** {answer}\n" if answer else "\n**Rationale:** only.\n"
    return f"## D-{n:03d} — {q}\nstatus: answered\n{meta}{prose}\n"


LEDGER = (
    "# Decisions\n\n"
    + _answered(1, "Unscoped?")
    + _answered(2, "Other child?", affects="feature-b")
    + _answered(3, "This child by path?", affects=CHILD)
    + _answered(4, "This child by basename?", affects="feature-a")
    + "## D-005 — Open?\nstatus: open\n\n"
    + _answered(6, "No answer?", None)
)


def _page(layout, path: str, front: str) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"---\n{front}---\n\n## Plan\n", encoding="utf-8", newline="\n")


def _item_front(
    title: str, *, type: str = "Feature", affects: str = "packages/a", extra: str = "", landed: str | None = None
) -> str:
    state = (
        f"work_status: resolved\nphase: done\nresolved_in: {landed}\n" if landed else "work_status: open\nphase: plan\n"
    )
    return (
        f"type: {type}\ntitle: {title}\ndescription: {title} desc\nstatus: stable\n{state}"
        f"effort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\naffects:\n- {affects}\n{extra}"
    )


def _world(
    tmp_path: Path,
    *,
    plan: str | None = PLAN,
    design: str | None = DESIGN,
    baseline: bool = False,
    sib_affects: str = "packages/a",
    epic_type: str = "Epic",
):
    layout = _layout(tmp_path)
    root = layout.bundle_dir
    refs_dir = root / EPIC / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    refs = f"/{EPIC}/references"
    sources = f"sources:\n  - id: decisions\n    resource: {refs}/00-decisions.md\n"
    if design is not None:
        sources += f"  - id: design\n    resource: {refs}/01-design.md\n"
        (refs_dir / "01-design.md").write_text(design, encoding="utf-8", newline="\n")
    if plan is not None:
        sources += f"  - id: plan\n    resource: {refs}/02-plan.md\n"
        (refs_dir / "02-plan.md").write_text(plan, encoding="utf-8", newline="\n")
    _page(layout, EPIC, _item_front("The Epic", type=epic_type, extra=sources))
    (refs_dir / "00-decisions.md").write_text(LEDGER, encoding="utf-8", newline="\n")
    base = f"spec_baseline:\n  code: {CODE}\n  workspace: {WS}\n" if baseline else ""
    _page(layout, CHILD, _item_front("Feature A", extra=base))
    _page(layout, SIB, _item_front("Feature B", affects=sib_affects, landed=SHA_B))
    _page(layout, OLD, _item_front("Feature C", affects="packages/z", landed=SHA_C))
    items = tuple(load_items(load_bundle(root, ignore=IGNORE)))
    return layout, items, next(i for i in items if i.path == CHILD)


def _no_git(
    monkeypatch: pytest.MonkeyPatch, *, new: dict[str, BaselineComparison] | None = None, ws: str | None = "0"
) -> None:
    """Keep the producer off real git: repo resolution, per-ref probes and the workspace count."""
    monkeypatch.setattr(eb, "_code_repo", lambda layout, items, item: Path("/repo"))
    monkeypatch.setattr(
        eb, "compare_to_baseline", lambda repo, ref, base: (new or {}).get(ref, BaselineComparison(False))
    )
    monkeypatch.setattr(eb.provenance, "run_git", lambda cwd, *args: ws)


def test_covered_epic_is_the_nearest_epic_or_release_ancestor(tmp_path: Path) -> None:
    _, items, child = _world(tmp_path)
    assert covered_epic(items, child).path == EPIC  # type: ignore[union-attr]
    epic = next(i for i in items if i.path == EPIC)
    assert covered_epic(items, epic) is None


def test_a_release_parent_counts(tmp_path: Path) -> None:
    _, items, child = _world(tmp_path, epic_type="Release")
    assert covered_epic(items, child).path == EPIC  # type: ignore[union-attr]


def test_a_top_level_item_is_an_empty_brief(tmp_path: Path) -> None:
    layout, items, _ = _world(tmp_path)
    epic = next(i for i in items if i.path == EPIC)
    assert eb.epic_brief(layout, items, epic) == eb.EpicBrief()


def test_full_brief_at_design_without_a_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path)
    _no_git(monkeypatch)
    brief = eb.epic_brief(layout, items, child)
    refs = f"/{EPIC}/references"
    assert brief.lines[0] == (
        f"Epic: [The Epic](/{EPIC}.md) — design: [design]({refs}/01-design.md); "
        f"ledger: [ledger]({refs}/00-decisions.md)"
    )
    assert "Own index row:" in brief.lines
    assert "- Path: [feature-a](/work/epic-a/children/feature-a.md)" in brief.lines
    assert "- Summary: Does A \\| carefully." in brief.lines
    decision_lines = [line for line in brief.lines if line.startswith("- D-")]
    assert decision_lines == [
        "- D-001 — Unscoped?: Yes.",
        "- D-003 — This child by path?: Yes.",
        "- D-004 — This child by basename?: Yes.",
    ]
    assert brief.warnings == ("epic_brief: answered epic decisions with no **Answer:** paragraph, skipped: 1",)
    assert f"- [{SIB}](/{SIB}.md) — Feature B desc; resolved in {SHA_B[:12]}; affects overlap: yes" in brief.lines
    assert f"- [{OLD}](/{OLD}.md) — Feature C desc; resolved in {SHA_C[:12]}; affects overlap: no" in brief.lines
    assert (
        brief.lines[-1]
        == f"Read the full epic design before relying on this brief: overlapping landed sibling(s): {SIB}."
    )
    assert brief.data["epic"] == EPIC
    assert brief.data["siblings"] == [
        {"path": SIB, "resolved_in": SHA_B, "overlaps": True, "new_since_baseline": None},
        {"path": OLD, "resolved_in": SHA_C, "overlaps": False, "new_since_baseline": None},
    ]
    assert [d["id"] for d in brief.data["decisions"]] == ["D-001", "D-003", "D-004"]  # type: ignore[index,union-attr]


def test_no_flags_line_when_nothing_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path, sib_affects="packages/z")
    _no_git(monkeypatch)
    brief = eb.epic_brief(layout, items, child)
    assert brief.lines[-1] == eb.NO_FLAGS_LINE
    assert brief.data["flags"] == []


def test_with_a_baseline_only_new_overlapping_siblings_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path, baseline=True)
    _no_git(monkeypatch, new={SHA_B: BaselineComparison(False), SHA_C: BaselineComparison(True)})
    brief = eb.epic_brief(layout, items, child)
    assert any(line.endswith("affects overlap: yes") for line in brief.lines)  # old sibling: no marker
    assert any(line.endswith("affects overlap: no; new since your baseline") for line in brief.lines)
    assert brief.lines[-1] == eb.NO_FLAGS_LINE


def test_a_new_overlapping_sibling_flags_with_a_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path, baseline=True)
    _no_git(monkeypatch, new={SHA_B: BaselineComparison(True)})
    assert "overlapping landed sibling(s): " + SIB in eb.epic_brief(layout, items, child).lines[-1]


def test_a_failed_probe_omits_the_marker_warns_and_still_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path, baseline=True)
    _no_git(monkeypatch, new={SHA_B: BaselineComparison(None, cause="timeout")})
    brief = eb.epic_brief(layout, items, child)
    assert any(line.endswith("affects overlap: yes") for line in brief.lines)
    assert f"epic_brief: could not compare {SIB} resolved_in {SHA_B[:12]} with the baseline (timeout)" in brief.warnings
    assert SIB in brief.lines[-1]


def test_a_missing_sibling_commit_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path, baseline=True)
    _no_git(monkeypatch, new={SHA_B: BaselineComparison(None, missing=True)})
    brief = eb.epic_brief(layout, items, child)
    assert any(f"{SIB} resolved_in {SHA_B[:12]} is not a commit" in w for w in brief.warnings)


def test_an_unresolvable_repository_is_one_warning_and_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, items, child = _world(tmp_path, baseline=True)
    _no_git(monkeypatch)

    def boom(layout, items, item):
        raise WorkspaceError("x")

    monkeypatch.setattr(eb, "_code_repo", boom)
    brief = eb.epic_brief(layout, items, child)
    assert "epic_brief: x" in brief.warnings
    assert not any("new since your baseline" in line for line in brief.lines)


def test_no_code_repository_is_one_warning_and_no_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path, baseline=True)
    _no_git(monkeypatch)
    monkeypatch.setattr(eb, "_code_repo", lambda layout, items, item: None)
    brief = eb.epic_brief(layout, items, child)
    assert "epic_brief: no code repository resolved; baseline markers omitted" in brief.warnings
    assert not any("new since your baseline" in line for line in brief.lines)


def test_workspace_commits_on_the_epic_design_or_ledger_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path, baseline=True, sib_affects="packages/z")
    _no_git(monkeypatch, ws="2")
    assert eb.epic_brief(layout, items, child).lines[-1] == (
        "Read the full epic design before relying on this brief: "
        f"2 workspace commit(s) touched the epic design or ledger since {WS[:12]}."
    )


def test_an_unreadable_workspace_count_is_a_warning_not_a_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path, baseline=True, sib_affects="packages/z")
    _no_git(monkeypatch, ws=None)
    brief = eb.epic_brief(layout, items, child)
    assert brief.lines[-1] == eb.NO_FLAGS_LINE
    assert f"epic_brief: could not count workspace commits since {WS[:12]}" in brief.warnings


def test_index_row_falls_back_to_the_design_child_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path, plan=None, sib_affects="packages/z")
    _no_git(monkeypatch)
    brief = eb.epic_brief(layout, items, child)
    assert "- Title: Feature A" in brief.lines and "- Summary: Does A by design." in brief.lines
    assert brief.lines[-1] == eb.NO_FLAGS_LINE


@pytest.mark.parametrize("plan,design", [(None, None), ("# Plan\n", "# Design\n")])
def test_no_index_row_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan: str | None, design: str | None
) -> None:
    layout, items, child = _world(tmp_path, plan=plan, design=design, sib_affects="packages/z")
    _no_git(monkeypatch)
    brief = eb.epic_brief(layout, items, child)
    assert "Own index row: not found." in brief.lines
    assert brief.data["index_row"] is None
    assert brief.lines[-1] == "Read the full epic design before relying on this brief: own index row not found."


def test_a_missing_referenced_file_reads_as_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path, sib_affects="packages/z")
    (layout.bundle_dir / EPIC / "references" / "02-plan.md").unlink()
    (layout.bundle_dir / EPIC / "references" / "01-design.md").unlink()
    _no_git(monkeypatch)
    assert "Own index row: not found." in eb.epic_brief(layout, items, child).lines


def test_a_nine_child_thirteen_decision_epic_fits_under_3000_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout, items, child = _world(tmp_path)
    answer = "A one-paragraph answer of realistic length that names a module, a flag and a reason for the choice."
    (layout.bundle_dir / EPIC / "references" / "00-decisions.md").write_text(
        "# Decisions\n\n"
        + "".join(_answered(n, f"Question {n} about the pipeline wait floor?", answer) for n in range(1, 14)),
        encoding="utf-8",
        newline="\n",
    )
    _no_git(monkeypatch)
    brief = eb.epic_brief(layout, items, child)
    assert count_tokens("\n".join(brief.lines)) < 3000
    assert not any("soft cap" in w for w in brief.warnings)


def test_an_oversized_brief_warns_without_truncating(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path)
    long = "word " * 400
    (layout.bundle_dir / EPIC / "references" / "00-decisions.md").write_text(
        "# Decisions\n\n" + "".join(_answered(n, f"Q{n}?", long) for n in range(1, 13)), encoding="utf-8", newline="\n"
    )
    _no_git(monkeypatch)
    brief = eb.epic_brief(layout, items, child)
    assert len([line for line in brief.lines if line.startswith("- D-")]) == 12
    assert any(
        w.startswith("epic_brief: ") and w.endswith("tokens, over the 4000-token soft cap") for w in brief.warnings
    )


def test_a_tokenizer_failure_is_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path)
    _no_git(monkeypatch)

    def fail(text: str) -> int:
        raise ValueError("no encoding")

    monkeypatch.setattr(eb, "count_tokens", fail)
    assert "epic_brief: token count skipped: no encoding" in eb.epic_brief(layout, items, child).warnings


def test_data_is_json_serialisable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout, items, child = _world(tmp_path, baseline=True)
    _no_git(monkeypatch)
    json.dumps(dict(eb.epic_brief(layout, items, child).data))
