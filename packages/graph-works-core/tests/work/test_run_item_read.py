"""`run_item_read`: one work item, whole, read-only; unknown targets are data."""

from __future__ import annotations

from datetime import date

from graph_works_core import apply_init, plan_init
from graph_works_core.work import ItemRead, ItemSource, run_item_read
from graph_works_core.work import commands as work

TODAY = date(2026, 9, 18)
PATH = "work/feature-a"
ITEM = """---
type: Feature
title: A
description: d
status: stable
sources:
  - id: design
    resource: /work/feature-a/references/01-design.md
    title: 'Design: A'
work_status: open
phase: plan
effort: medium
opened: 2026-09-01
updated: 2026-09-02
affects:
- packages/a
---

## Summary
d
"""


def _workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _write(layout, text=ITEM):
    (layout.bundle_dir / f"{PATH}.md").write_text(text, encoding="utf-8")
    refs = layout.bundle_dir / PATH / "references"
    refs.mkdir(parents=True)
    (refs / "01-design.md").write_text("# D\n", encoding="utf-8")
    (refs / "orca-placement").mkdir()
    (refs / "orca-placement" / "k.json").write_text("{}", encoding="utf-8")


def test_an_item_reads_whole(tmp_path):
    layout = _workspace(tmp_path)
    _write(layout)
    before = (layout.bundle_dir / f"{PATH}.md").read_bytes()

    result = work.run_item_read(layout, PATH)

    assert result.refusal is None and result.detail is None
    assert result.path == PATH
    assert result.frontmatter["title"] == "A"
    assert result.frontmatter["opened"] == "2026-09-01"
    assert result.body.startswith("## Summary")
    assert result.sources == (work.ItemSource("design", "/work/feature-a/references/01-design.md", "Design: A"),)
    assert result.references == (
        "work/feature-a/references/01-design.md",
        "work/feature-a/references/orca-placement/k.json",
    )
    assert result.parse_error is None and result.coercion_failures == ()
    assert (layout.bundle_dir / f"{PATH}.md").read_bytes() == before


def test_an_unknown_item_is_a_refusal_not_a_raise(tmp_path):
    layout = _workspace(tmp_path)
    result = work.run_item_read(layout, "work/nope")
    assert result.refusal == "unknown-item"
    assert result.frontmatter == {} and result.body == "" and result.references == ()


def test_a_non_work_concept_is_unknown(tmp_path):
    layout = _workspace(tmp_path)
    (layout.bundle_dir / "concepts").mkdir()
    (layout.bundle_dir / "concepts" / "x.md").write_text("---\ntype: Concept\ntitle: X\n---\n", encoding="utf-8")
    assert work.run_item_read(layout, "concepts/x").refusal == "unknown-item"


def test_an_item_with_no_references_dir_has_none(tmp_path):
    layout = _workspace(tmp_path)
    (layout.bundle_dir / f"{PATH}.md").write_text(ITEM, encoding="utf-8")
    assert work.run_item_read(layout, PATH).references == ()


def test_malformed_frontmatter_still_reads(tmp_path):
    layout = _workspace(tmp_path)
    _write(layout, ITEM.replace("title: A", "title: [unclosed"))
    result = work.run_item_read(layout, PATH)
    assert result.refusal is None
    assert result.parse_error is not None and ":" in result.parse_error


def test_item_read_is_available_from_work_facade(tmp_path):
    layout = _workspace(tmp_path)
    _write(layout)

    result = run_item_read(layout, PATH)

    assert isinstance(result, ItemRead)
    assert result.sources == (ItemSource("design", "/work/feature-a/references/01-design.md", "Design: A"),)
