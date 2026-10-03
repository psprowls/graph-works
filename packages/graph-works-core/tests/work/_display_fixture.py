"""One workspace exercising every input the six display reads see."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.layout import WorkspaceLayout

TODAY = date(2026, 10, 2)

_ITEM = """---
type: {type}
title: {title}
description: d
status: stable
work_status: {work_status}
phase: {phase}
effort: small
opened: 2026-09-01
updated: 2026-09-02
affects:
- packages/a
sources:
  - id: design
    resource: /{path}/references/01-design.md
    title: Design
---

## Summary
d

## Plan

| Action | Done when | Rationale |
| --- | --- | --- |
"""

ITEMS: dict[str, tuple[str, str, str]] = {
    # path: (type, work_status, phase)
    "work/feature-open": ("Feature", "open", "design"),
    "work/feature-plan": ("Feature", "open", "plan"),
    "work/bug-resolved": ("Bug", "resolved", "done"),
    "work/bug-ingested": ("Bug", "resolved", "done"),
    "work/epic-e": ("Epic", "accepted", "execute"),
    "work/epic-e/children/bug-c1": ("Bug", "open", "design"),
    "work/epic-e/children/bug-c2": ("Bug", "resolved", "done"),
    "work/_archive/bug-old": ("Bug", "resolved", "done"),
}
ITEM_PATHS: tuple[str, ...] = (*ITEMS, "work/bug-malformed", "work/bug-unreadable", "work/missing")

_LEDGER = """## D-001 — Open question?
status: open
affects: [work/epic-e/children/bug-c1]

## D-002 — Answered question?
status: answered
decided: 2026-09-02 by psprowls

**Answer:** yes.
"""


def display_workspace(tmp_path: Path) -> WorkspaceLayout:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    bundle = layout.bundle_dir
    for path, (type_, status, phase) in ITEMS.items():
        page = bundle / f"{path}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            _ITEM.format(type=type_, title=path.rsplit("/", 1)[-1], work_status=status, phase=phase, path=path),
            encoding="utf-8",
            newline="\n",
        )
        refs = bundle / path / "references"
        refs.mkdir(parents=True, exist_ok=True)
        (refs / "01-design.md").write_text("# D\n", encoding="utf-8", newline="\n")
    (bundle / "work/epic-e/references/00-decisions.md").write_text(_LEDGER, encoding="utf-8", newline="\n")
    (bundle / "work/bug-malformed.md").write_text("---\ntitle: [unclosed\n---\n", encoding="utf-8", newline="\n")
    (bundle / "work/bug-unreadable.md").write_bytes(b"\xff\xfe not utf-8")
    sources = bundle / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    (sources / "2026-09-ingested.md").write_text(
        "---\ntype: Source\ntitle: S\norigin: work/bug-ingested/references/01-design.md\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    (sources / "2026-09-no-origin.md").write_text("---\ntype: Source\ntitle: N\n---\n", encoding="utf-8", newline="\n")
    for page in bundle.rglob("*"):
        os.utime(page, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    return layout


def disable_index(layout: WorkspaceLayout) -> None:
    layout.local_manifest_path.write_text("read_index:\n  enabled: false\n", encoding="utf-8", newline="\n")
