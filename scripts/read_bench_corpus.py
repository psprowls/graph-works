"""A deterministic synthetic graph-works workspace for read benchmarks.

Sizes the two things the epic's budgets care about independently: work items
(with epics, children, dependencies, archived items and reference artifacts) and
unrelated wiki content (pages that link to each other and to work, and cite a
real git repository). No randomness: the same arguments write the same bytes.

    corpus = generate(Path("/tmp/c"), items=1000, wiki_pages=1000)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.layout import WorkspaceLayout

GENERATOR_VERSION = 1
TODAY = date(2026, 10, 1)
SOURCE_FILES = 10
_KINDS = (("Feature", "feature"), ("Bug", "bug"), ("TechDebt", "tech-debt"), ("TestGap", "test-gap"))
_WIKI_LANES = (
    ("docs/explanations", "Explanation"),
    ("docs/reference", "Reference"),
    ("sources", "Source"),
    ("adrs", "Adr"),
)
_PLAN_TABLE = "## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n"


@dataclass(frozen=True, slots=True)
class Corpus:
    layout: WorkspaceLayout
    items: int
    active_items: int
    wiki_pages: int
    sample_item: str
    sample_page: str | None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _item_paths(items: int) -> list[tuple[str, str, bool]]:
    """(canonical path, type, archived) for every item, in index order."""
    out: list[tuple[str, str, bool]] = []
    epic = ""
    for i in range(items):
        slot = i % 10
        if slot == 0:
            epic = f"work/epic-{i:05d}"
            out.append((epic, "Epic", False))
        elif slot <= 3:
            out.append((f"{epic}/children/feature-{i:05d}", "Feature", False))
        elif slot == 9:
            out.append((f"work/_archive/bug-{i:05d}", "Bug", True))
        else:
            kind, slug = _KINDS[i % len(_KINDS)]
            out.append((f"work/{slug}-{i:05d}", kind, False))
    return out


def _item_page(i: int, kind: str, archived: bool, depends_on: str | None) -> str:
    status, phase = ("resolved", "done") if archived else ("open", "design")
    depends = f"depends_on:\n- path: {depends_on}\n  blocks: execute\n  needs: resolved\n" if depends_on else ""
    return (
        f"---\ntype: {kind}\ntitle: Item {i:05d}\ndescription: Synthetic item {i:05d}\nstatus: stable\n"
        f"work_status: {status}\nphase: {phase}\neffort: medium\nopened: 2026-08-01\nupdated: 2026-08-01\n"
        f"affects:\n- src\n{depends}---\n\n## Summary\n\nSynthetic item {i:05d}.\n\n{_PLAN_TABLE}"
    )


def _wiki_page(j: int, n: int, lane_type: str, page_ids: list[str], work_target: str) -> str:
    first = page_ids[(j + 1) % n]
    second = page_ids[(j * 7 + 3) % n]
    return (
        f"---\ntype: {lane_type}\ntitle: Page {j:05d}\ndescription: Synthetic page {j:05d}\n"
        f"tags: []\nupdated: 2026-10-01\n---\n\n# Page {j:05d}\n\n"
        f"Related: [next](/{first}.md), [jump](/{second}.md), [work](/{work_target}.md).\n\n"
        f"Implemented in `src/module_{j % SOURCE_FILES:02d}.py:1`.\n"
    )


def _git_host(host: Path) -> None:
    for k in range(SOURCE_FILES):
        _write(host / "src" / f"module_{k:02d}.py", f"VALUE = {k}\n")
    for args in (["init", "-q"], ["add", "src"]):
        subprocess.run(["git", *args], cwd=host, check=True, capture_output=True, text=True, encoding="utf-8")


def generate(root: Path, *, items: int, wiki_pages: int, malformed: bool = True) -> Corpus:
    """Write a workspace with *items* work items and *wiki_pages* wiki pages under *root*."""
    if items < 1:
        raise ValueError(f"items must be at least 1, got {items}")
    if wiki_pages < 0:
        raise ValueError(f"wiki_pages must not be negative, got {wiki_pages}")
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"corpus root is not empty: {root}")
    host = root / "host"
    host.mkdir(parents=True, exist_ok=True)
    _git_host(host)
    layout = apply_init(plan_init(host / ".works", today=TODAY, topic="Read bench")).layout
    bundle = layout.bundle_dir

    paths = _item_paths(items)
    for i, (path, kind, archived) in enumerate(paths):
        depends_on = paths[i - 1][0] if i > 0 and i % 7 == 3 and kind != "Epic" else None
        _write(bundle / f"{path}.md", _item_page(i, kind, archived, depends_on))
        _write(bundle / path / "references" / "01-design.md", f"# Design\n\nDesign for {path}.\n")
        _write(bundle / path / "references" / "02-plan.md", f"# Plan\n\nPlan for {path}.\n")

    page_ids = [f"{_WIKI_LANES[j % len(_WIKI_LANES)][0]}/page-{j:05d}" for j in range(wiki_pages)]
    for j, page_id in enumerate(page_ids):
        lane_type = _WIKI_LANES[j % len(_WIKI_LANES)][1]
        _write(bundle / f"{page_id}.md", _wiki_page(j, wiki_pages, lane_type, page_ids, paths[j % items][0]))
    if malformed:
        _write(bundle / "docs" / "explanations" / "malformed.md", "---\ntype: [unclosed\n---\n\nBroken.\n")

    archived_count = sum(1 for _, _, archived in paths if archived)
    return Corpus(
        layout=layout,
        items=items,
        active_items=items - archived_count,
        wiki_pages=wiki_pages,
        sample_item=paths[1][0] if items > 1 else paths[0][0],
        sample_page=page_ids[0] if page_ids else None,
    )


__all__ = ["GENERATOR_VERSION", "SOURCE_FILES", "TODAY", "Corpus", "generate"]
