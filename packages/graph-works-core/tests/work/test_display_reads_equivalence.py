"""The six work display reads: index backend == bundle backend == the pre-change full load."""

from __future__ import annotations

import shutil
from pathlib import Path

import _display_oracle as oracle
import pytest
from _display_fixture import ITEM_PATHS, TODAY, disable_index, display_workspace
from graph_works_core import apply_init, plan_init
from graph_works_core.read_session import location, open_read_session
from graph_works_core.work import commands as work

READS = [
    ("status", work.run_status, oracle.oracle_status),
    ("list", work.run_work_list, oracle.oracle_work_list),
    ("queue", work.run_work_queue, oracle.oracle_work_queue),
    ("ingest", work.run_ingest_queue, oracle.oracle_ingest_queue),
    ("decisions", work.run_open_decisions, oracle.oracle_open_decisions),
]


@pytest.mark.parametrize(("name", "run", "expected"), READS, ids=[r[0] for r in READS])
def test_read_matches_oracle_on_both_backends(tmp_path: Path, name, run, expected) -> None:
    layout = display_workspace(tmp_path)
    want = expected(layout)
    with open_read_session(layout) as session:
        assert session.backend == "index"
    assert run(layout) == want
    disable_index(layout)
    with open_read_session(layout) as session:
        assert (session.backend, session.fallback) == ("bundle", "disabled")
    assert run(layout) == want


@pytest.mark.parametrize("path", ITEM_PATHS)
def test_item_read_matches_oracle_on_both_backends(tmp_path: Path, path: str) -> None:
    layout = display_workspace(tmp_path)
    want = oracle.oracle_item_read(layout, path)
    assert work.run_item_read(layout, path) == want
    disable_index(layout)
    assert work.run_item_read(layout, path) == want


def test_deleting_the_database_changes_no_work_read(tmp_path: Path) -> None:
    layout = display_workspace(tmp_path)
    before = [run(layout) for _, run, _ in READS]
    details = [work.run_item_read(layout, path) for path in ITEM_PATHS]
    for file in location.sidecar_paths(location.database_path(layout)):
        file.unlink(missing_ok=True)
    assert [run(layout) for _, run, _ in READS] == before
    assert [work.run_item_read(layout, path) for path in ITEM_PATHS] == details


def test_fixture_covers_the_interesting_cases(tmp_path: Path) -> None:
    layout = display_workspace(tmp_path)
    assert work.run_item_read(layout, "work/bug-unreadable").refusal == "unreadable"
    assert work.run_item_read(layout, "work/missing").refusal == "unknown-item"
    assert work.run_item_read(layout, "work/bug-malformed").parse_error is not None
    assert "work/_archive/bug-old" not in {item.path for item in work.run_work_list(layout)}
    pending = {entry.path for entry in work.run_ingest_queue(layout).pending}
    assert "work/bug-ingested" not in pending and "work/bug-resolved" in pending
    assert [d.decision.id for d in work.run_open_decisions(layout)] == ["D-001"]
    assert work.run_open_decisions(layout)[0].held == ("work/epic-e/children/bug-c1",)
    assert any(entry.item.path == "work/feature-plan" for entry in work.run_work_queue(layout))


def test_v01_body_citations_omit_row_sources_but_keep_item_detail(tmp_path: Path) -> None:
    """Epic D-008: rows carry no v0.1 body `# Citations` fallback."""
    layout = display_workspace(tmp_path)
    (layout.bundle_dir / "work/bug-legacy.md").write_text(
        "---\ntype: Bug\ntitle: L\nwork_status: resolved\nphase: done\n---\n\n# Citations\n\n"
        "- [Design](/work/bug-legacy/references/01-design.md)\n",
        encoding="utf-8",
        newline="\n",
    )
    rows_pending = {e.path for e in work.run_ingest_queue(layout).pending}
    full_pending = {e.path for e in oracle.oracle_ingest_queue(layout).pending}
    assert rows_pending == full_pending
    assert "work/bug-legacy" not in full_pending
    (row,) = [item for item in work.run_work_list(layout) if item.path == "work/bug-legacy"]
    (full,) = [item for item in oracle.oracle_work_list(layout) if item.path == "work/bug-legacy"]
    assert row.sources == ()
    assert [(source.id, source.resource) for source in full.sources] == [
        (None, "/work/bug-legacy/references/01-design.md")
    ]
    # item detail is unaffected: its sources come from the parse
    assert work.run_item_read(layout, "work/bug-legacy") == oracle.oracle_item_read(layout, "work/bug-legacy")


def test_native_yaml_date_tags_project_as_iso_strings(tmp_path: Path) -> None:
    """Epic D-010: a YAML-date tag is an ISO string on rows."""
    layout = display_workspace(tmp_path)
    page = layout.bundle_dir / "work/feature-open.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("effort: small\n", "effort: small\ntags: [2026-09-01]\n"),
        encoding="utf-8",
        newline="\n",
    )
    (item,) = [i for i in work.run_work_list(layout) if i.path == "work/feature-open"]
    (want,) = [i for i in oracle.oracle_work_list(layout) if i.path == "work/feature-open"]
    assert item.tags == ("2026-09-01",)
    assert want.tags == ()
    disable_index(layout)
    assert work.run_work_list(layout) == oracle.oracle_work_list(layout)


OKF_FIXTURES = Path(__file__).resolve().parents[3] / "okf-io" / "tests" / "fixtures"
FIXTURE_BUNDLES = ("bundles/acme_retail", "bundles/ga4", "edge", "nonconformant")


def _copy_edge_member(source, destination):
    """Skip a fixture member whose filename the host cannot create."""
    try:
        return shutil.copy2(source, destination)
    except OSError:
        return str(destination)


@pytest.mark.parametrize("bundle", FIXTURE_BUNDLES)
def test_okf_fixture_bundles_match_oracle_on_both_backends(tmp_path: Path, bundle: str) -> None:
    layout = apply_init(plan_init(tmp_path / ".works", today=TODAY, topic="Fixtures")).layout
    options = {"copy_function": _copy_edge_member} if bundle == "edge" else {}
    shutil.copytree(OKF_FIXTURES / bundle, layout.bundle_dir, dirs_exist_ok=True, **options)
    wants = [expected(layout) for _, _, expected in READS]
    with open_read_session(layout) as session:
        assert session.backend == "index"
    assert [run(layout) for _, run, _ in READS] == wants
    disable_index(layout)
    with open_read_session(layout) as session:
        assert (session.backend, session.fallback) == ("bundle", "disabled")
    assert [run(layout) for _, run, _ in READS] == wants
    assert work.run_item_read(layout, "work/missing") == oracle.oracle_item_read(layout, "work/missing")


def test_native_yaml_date_origin_matches_iso_design_resource_on_both_backends(tmp_path: Path) -> None:
    """Epic D-010 applies to Source rows even on the disabled-index backend."""
    layout = display_workspace(tmp_path)
    page = layout.bundle_dir / "work/bug-resolved.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("/work/bug-resolved/references/01-design.md", "/2026-09-01"),
        encoding="utf-8",
        newline="\n",
    )
    (layout.bundle_dir / "sources/date.md").write_text(
        "---\ntype: Source\ntitle: Date\norigin: 2026-09-01\n---\n", encoding="utf-8", newline="\n"
    )
    full_pending = {entry.path for entry in oracle.oracle_ingest_queue(layout).pending}
    assert "work/bug-resolved" in full_pending
    for disabled in (False, True):
        if disabled:
            disable_index(layout)
        rows_pending = {entry.path for entry in work.run_ingest_queue(layout).pending}
        assert full_pending - rows_pending == {"work/bug-resolved"}
        assert rows_pending == full_pending - {"work/bug-resolved"}
