from __future__ import annotations

from pathlib import Path

from code_wiki_okf.entities.catalog import lane_page_names, render_contents
from okf_io import load_bundle


def test_render_contents_opens_with_the_lane_page_link() -> None:
    body = render_contents({}, lane_page="demo")
    assert body.startswith("Lane page: [demo](/repositories/demo.md)\n\n")
    assert body.endswith("_(none)_")
    assert not render_contents({}).startswith("Lane page:")


def test_lane_page_names_are_direct_lane_members(tmp_path: Path) -> None:
    for rel in (
        "repositories/demo.md",
        "repositories/other.md",
        "repositories/demo/nested.md",
        "concepts/x.md",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\ntitle: x\n---\n\nx\n", encoding="utf-8", newline="")
    assert lane_page_names(load_bundle(tmp_path)) == frozenset({"demo", "other"})
