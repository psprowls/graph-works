"""On a warm, unchanged index the work display reads parse nothing and never load the bundle."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _display_fixture import display_workspace
from graph_works_core.read_session import open_read_session
from graph_works_core.work import commands as work

NO_PARSE = [work.run_status, work.run_work_list, work.run_work_queue, work.run_ingest_queue, work.run_open_decisions]


def _refuse(*args, **kwargs):
    raise AssertionError("full bundle load on the index path")


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from okf_ext.readindex import sync

    seen: list[str] = []
    real_sync, real_work = sync.read_member, work.read_member
    monkeypatch.setattr(sync, "read_member", lambda root, mid: seen.append(mid) or real_sync(root, mid))
    monkeypatch.setattr(work, "read_member", lambda root, mid: seen.append(mid) or real_work(root, mid))
    return seen


@pytest.fixture
def warm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parses: list[str]):
    layout = display_workspace(tmp_path)
    with open_read_session(layout):
        pass
    parses.clear()
    monkeypatch.setattr(work, "load_workspace_bundle", _refuse)
    monkeypatch.setattr(work, "load_work_bundle", _refuse)
    from graph_works_core.read_session import bundle_backend

    monkeypatch.setattr(bundle_backend, "load_bundle_at", _refuse)
    return layout


@pytest.mark.parametrize("run", NO_PARSE, ids=lambda r: r.__name__)
def test_warm_read_parses_nothing(warm, parses, run) -> None:
    run(warm)
    assert parses == []


def test_item_read_parses_one_file(warm, parses) -> None:
    work.run_item_read(warm, "work/feature-open")
    assert parses == ["work/feature-open.md"]


def test_unrelated_wiki_pages_do_not_change_work_read_cost(warm, parses) -> None:
    docs = warm.bundle_dir / "docs" / "explanations"
    docs.mkdir(parents=True, exist_ok=True)
    for n in range(1000):
        (docs / f"page-{n:04}.md").write_text(
            f"---\ntype: Explanation\ntitle: P{n}\n---\nbody\n", encoding="utf-8", newline="\n"
        )
    for page in docs.iterdir():
        os.utime(page, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    with open_read_session(warm):
        pass
    parses.clear()
    for run in NO_PARSE:
        run(warm)
    assert parses == []
