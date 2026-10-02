"""`run_section_write`: replace one existing, prose-owned `##` section of a wiki page (D-005)."""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from _transaction_helpers import _init_git
from graph_works_core import apply_init, plan_init
from graph_works_core.wiki_page import SectionWriteRun, run_section_write
from graph_works_core.wiki_page import section as section_module
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.transactions import _HELD_BUNDLE_LOCKS
from okf_ext.writing import ApplyResult, WriteFailure

TODAY = date(2026, 10, 2)
PAGE = "docs/explanations/x"
TEXT = (
    "---\ntype: Explanation\ntitle: X\ndescription: d\nupdated: 2026-01-01\n# keep this comment\n---\n\n"
    "## Context\n\nold context\n\n### Detail\n\nd\n\n## Trade-offs\n\nt\n"
)
PACKAGE = "code-graph/r/entities/packages/p"
PACKAGE_TEXT = "---\ntype: Package\ntitle: p\n---\n\n## Purpose\n\nx\n\n## Files\n\ngen\n\n## Notes\n\nn\n"


def _write(layout: WorkspaceLayout, page: str, text: str) -> Path:
    path = layout.bundle_dir / f"{page}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _layout(root: Path) -> WorkspaceLayout:
    built = apply_init(plan_init(root / "repo" / ".works", today=TODAY, topic="S")).layout
    _write(built, PAGE, TEXT)
    return built


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    return _layout(tmp_path)


def snapshot(layout: WorkspaceLayout) -> dict[str, bytes]:
    root = layout.bundle_dir
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_plan_writes_nothing_and_shows_before_and_after(layout: WorkspaceLayout) -> None:
    before = (layout.bundle_dir / f"{PAGE}.md").read_bytes()
    run = run_section_write(layout, PAGE, "Context", "new context", today=TODAY)
    assert run.refusal is None and run.applied is False
    assert run.heading == "Context"
    assert run.before == "\nold context\n\n### Detail\n\nd\n\n"
    assert run.after == "\nnew context\n\n"
    assert run.written == () and run.commit is None
    assert (layout.bundle_dir / f"{PAGE}.md").read_bytes() == before


def test_apply_keeps_frontmatter_bytes_and_bumps_updated(layout: WorkspaceLayout) -> None:
    run = run_section_write(layout, PAGE, "context", "new context", today=TODAY, dry_run=False)
    assert run.applied and run.written == (f"{PAGE}.md",) and run.failures == ()
    assert run.heading == "Context"
    text = (layout.bundle_dir / f"{PAGE}.md").read_text(encoding="utf-8")
    assert text == TEXT.replace("2026-01-01", "2026-10-02").replace(
        "old context\n\n### Detail\n\nd\n\n", "new context\n\n"
    )


@pytest.mark.parametrize(
    ("page", "heading", "body", "kind"),
    [
        ("docs/explanations/nope", "Context", "x", "unknown-page"),
        ("work/feature-x", "Summary", "x", "work-item"),
        ("work", "Summary", "x", "work-item"),
        (PAGE, "Missing", "x", "missing-section"),
        (PAGE, "Detail", "x", "missing-section"),
        (PAGE, "Context", "## Sneaky\n\nx", "heading-in-body"),
        (PAGE, "Context", "# Title\n\nx", "heading-in-body"),
        (PAGE, "Context", "Title\n=====\n\nx", "heading-in-body"),
        (PAGE, "Context", "```sh\ncode\n", "unbalanced-body"),
        (PAGE, "Context", "<!-- note\n", "unbalanced-body"),
        (PAGE, "Context", "~~~\ncode\n", "unbalanced-body"),
    ],
)
def test_refusals_write_nothing(layout: WorkspaceLayout, page: str, heading: str, body: str, kind: str) -> None:
    _write(layout, "work/feature-x", "---\ntype: Feature\ntitle: F\n---\n\n## Summary\n\ns\n")
    before = snapshot(layout)
    run = run_section_write(layout, page, heading, body, today=TODAY, dry_run=False)
    assert run.refusal == kind and run.applied is False
    assert run.written == () and run.commit is None
    assert snapshot(layout) == before


def test_a_heading_refusal_still_shows_the_current_section(layout: WorkspaceLayout) -> None:
    run = run_section_write(layout, PAGE, "Context", "## Sneaky", today=TODAY)
    assert run.refusal == "heading-in-body"
    assert run.before == run.after == "\nold context\n\n### Detail\n\nd\n\n"


def test_an_unclosed_fence_cannot_hide_a_generated_heading(layout: WorkspaceLayout) -> None:
    path = _write(layout, PACKAGE, PACKAGE_TEXT)
    before = path.read_bytes()
    run = run_section_write(layout, PACKAGE, "Purpose", "```sh\ncode\n", today=TODAY, dry_run=False)
    assert run.refusal == "unbalanced-body" and run.applied is False
    assert run.before == run.after == "\nx\n\n"
    assert path.read_bytes() == before


def test_subheadings_and_fenced_hashes_are_allowed(layout: WorkspaceLayout) -> None:
    body = "### Deeper\n\nx\n\n```sh\n# comment\n## not a heading\n```\n"
    run = run_section_write(layout, PAGE, "Context", body, today=TODAY, dry_run=False)
    assert run.refusal is None and run.applied
    text = (layout.bundle_dir / f"{PAGE}.md").read_text(encoding="utf-8")
    assert f"## Context\n\n{body}\n## Trade-offs\n" in text


def test_duplicate_heading_is_refused_case_insensitively(layout: WorkspaceLayout) -> None:
    _write(layout, PAGE, TEXT + "\n## CONTEXT\n\nagain\n")
    assert run_section_write(layout, PAGE, "context", "x", today=TODAY).refusal == "duplicate-section"


def test_code_graph_page_writes_declared_prose_sections_only(layout: WorkspaceLayout) -> None:
    _write(layout, PACKAGE, PACKAGE_TEXT)
    assert run_section_write(layout, PACKAGE, "Files", "x", today=TODAY).refusal == "generated-section"
    # Undeclared on a code-graph page: nothing says a person owns it.
    assert run_section_write(layout, PACKAGE, "Notes", "x", today=TODAY).refusal == "generated-section"
    assert run_section_write(layout, PACKAGE, "Purpose", "x", today=TODAY).refusal is None


def test_curated_page_writes_undeclared_sections(layout: WorkspaceLayout) -> None:
    _write(layout, PAGE, TEXT + "\n## Extra\n\ne\n")
    assert run_section_write(layout, PAGE, "Extra", "x", today=TODAY).refusal is None


def test_curated_page_refuses_a_declared_non_prose_section(layout: WorkspaceLayout) -> None:
    sections = layout.config_dir / "sections" / "Explanation.yaml"
    sections.write_text(
        sections.read_text(encoding="utf-8").replace(
            "  - heading: Trade-offs\n", "  - heading: Trade-offs\n    ownership: generated\n"
        ),
        encoding="utf-8",
        newline="",
    )
    assert run_section_write(layout, PAGE, "Trade-offs", "x", today=TODAY).refusal == "generated-section"


def test_package_page_does_not_gain_updated(layout: WorkspaceLayout) -> None:
    path = _write(layout, PACKAGE, PACKAGE_TEXT)
    run = run_section_write(layout, PACKAGE, "Purpose", "y", today=TODAY, dry_run=False)
    assert run.applied
    assert "updated:" not in path.read_text(encoding="utf-8")


def test_missing_schema_directory_means_no_updated_bump(layout: WorkspaceLayout) -> None:
    shutil.rmtree(layout.config_dir / "schema")
    run = run_section_write(layout, PAGE, "Context", "y", today=TODAY, dry_run=False)
    assert run.applied
    assert "updated: 2026-01-01\n" in (layout.bundle_dir / f"{PAGE}.md").read_text(encoding="utf-8")


def test_broken_section_declarations_are_a_workspace_error(layout: WorkspaceLayout) -> None:
    (layout.config_dir / "sections" / "Explanation.yaml").write_text("sections: [", encoding="utf-8", newline="")
    with pytest.raises(WorkspaceError):
        run_section_write(layout, PAGE, "Context", "y", today=TODAY)


def test_before_apply_sees_the_plan_before_the_write_under_the_bundle_lock(layout: WorkspaceLayout) -> None:
    seen: list[tuple[SectionWriteRun, bytes, bool]] = []
    path = layout.bundle_dir / f"{PAGE}.md"
    original = path.read_bytes()
    dry = run_section_write(layout, PAGE, "Context", "y", today=TODAY)
    held = str(layout.bundle_dir.resolve())
    run = run_section_write(
        layout,
        PAGE,
        "Context",
        "y",
        today=TODAY,
        dry_run=False,
        before_apply=lambda candidate: seen.append((candidate, path.read_bytes(), held in _HELD_BUNDLE_LOCKS.get())),
    )
    ((candidate, at_call, locked),) = seen
    assert candidate == dry
    assert at_call == original
    assert locked
    assert run.applied and path.read_bytes() != original


def test_before_apply_raising_aborts_the_write(layout: WorkspaceLayout) -> None:
    before = snapshot(layout)

    def refuse(_candidate: SectionWriteRun) -> None:
        raise RuntimeError("stale")

    with pytest.raises(RuntimeError, match="stale"):
        run_section_write(layout, PAGE, "Context", "y", today=TODAY, dry_run=False, before_apply=refuse)
    assert snapshot(layout) == before


def test_dry_run_and_refusals_never_call_before_apply_on_a_plan(layout: WorkspaceLayout) -> None:
    calls: list[SectionWriteRun] = []
    run_section_write(layout, PAGE, "Context", "y", today=TODAY, before_apply=calls.append)
    assert calls == []
    refused = run_section_write(layout, PAGE, "Missing", "y", today=TODAY, dry_run=False, before_apply=calls.append)
    assert calls == [refused]


def test_apply_commits_the_page_when_the_workspace_is_its_own_repo(tmp_path: Path) -> None:
    built = _layout(tmp_path)
    _init_git(built.root)
    run = run_section_write(built, PAGE, "Context", "new context", today=TODAY, dry_run=False)
    assert run.commit is not None and run.commit.status == "committed"
    member = f"{built.bundle_dir.relative_to(built.root).as_posix()}/{PAGE}.md"
    assert run.commit.paths == (member,)
    assert run.commit.subject == "workspace: write docs/explanations/x section Context"
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=built.root, capture_output=True, text=True, check=True
    ).stdout
    assert status == ""


def test_a_malformed_schema_set_is_a_workspace_error(layout: WorkspaceLayout) -> None:
    shutil.rmtree(layout.config_dir / "schema")
    (layout.config_dir / "schema").mkdir()  # empty: `load_schemas` refuses it
    with pytest.raises(WorkspaceError):
        run_section_write(layout, PAGE, "Context", "y", today=TODAY)


def test_a_failed_write_reports_failures_and_commits_nothing(
    layout: WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = WriteFailure(path=f"{PAGE}.md", error="denied", kind="unwritable")
    monkeypatch.setattr(section_module, "write_all", lambda _pending: ApplyResult((), (failure,), ()))
    run = run_section_write(layout, PAGE, "Context", "y", today=TODAY, dry_run=False)
    assert run.applied and run.written == () and run.commit is None
    assert run.failures == (f"{PAGE}.md: unwritable -- denied",)
