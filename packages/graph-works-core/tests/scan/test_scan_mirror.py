"""The lane-choice regression: a `gw scan` must not leave the `## Files`
links it writes pointing at pages it never creates.

`entities/render.py` renders one root-absolute link per file into the mirror
lane, and `okf_io._rules.links::broken` existence-checks every internal link
including the absolute form (`adrs/0004-broken-links-are-warn`). Before this
fix `build_scan_worklist` ran only the entity lane, so a scan of the fixture
repo produced 3 `links.broken` findings and zero pages under
`code-graph/demo/`; the production run produced 1311.

**Assert on the finding count, never on link counts.** The fixture holds two
file nodes for `packages/widgets/src/a.py` under two repo URIs (`repo:acme/demo`
seeded and `repo:local/repo` derived by `graph.build`), so `packages/widgets`
lists that path twice. That is a fixture artifact with no production
analogue -- a real run has one repo URI -- and zero broken findings is robust
to it.
"""

from __future__ import annotations

from pathlib import Path

from code_wiki_okf.config import load_config
from graph_works_core.scan.commands import build_scan_worklist
from okf_io import load_bundle
from okf_io import validate as okf_validate
from scan_helpers import AT, REPO_NAME, TODAY, make_workspace, seed_graph


async def test_a_scan_leaves_no_broken_links_into_the_mirror_lane(tmp_path: Path) -> None:
    layout, repo = make_workspace(tmp_path)
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    seed_graph(config.graph_dir, repo)

    await build_scan_worklist(layout, config, today=TODAY, at=AT, dry_run=False)

    report = okf_validate(load_bundle(layout.bundle_dir), today=TODAY)
    broken = report.by_code("links.broken")
    assert broken == (), "\n".join(f"{finding.path}: {finding.message}" for finding in broken)


async def test_a_scan_writes_a_page_for_every_tracked_file(tmp_path: Path) -> None:
    layout, repo = make_workspace(tmp_path)
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    seed_graph(config.graph_dir, repo)

    await build_scan_worklist(layout, config, today=TODAY, at=AT, dry_run=False)

    mirror_root = layout.bundle_dir / "code-graph" / REPO_NAME / "file-system"
    for tracked in ("packages/widgets/src/a.py", "packages/foo.bar/src/b.py", "README.md"):
        assert (mirror_root / f"{tracked}.md").exists(), f"no mirror page for {tracked}"


async def test_a_dry_run_writes_no_mirror_page(tmp_path: Path) -> None:
    """`sync_mirror(dry_run=True)` plans and writes nothing, and
    `build_scan_worklist(dry_run=True)` writes nothing at all -- the mirror
    lane must not be the one thing that leaks a write through a preview.
    """
    layout, repo = make_workspace(tmp_path)
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    seed_graph(config.graph_dir, repo)

    _worklist, structural = await build_scan_worklist(layout, config, today=TODAY, at=AT, dry_run=True)

    assert structural.mirror.plans  # a real preview: it saw work to do
    assert structural.mirror.results == ()
    assert not (layout.bundle_dir / "code-graph" / REPO_NAME / "file-system").exists()
