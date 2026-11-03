"""A reference clone under `repositories/` is invisible to a real `gw scan` apply.

The clone is a foreign checkout. Its markdown may carry generated-looking frontmatter, a resource URI
that collides with a real entity, malformed frontmatter or dangling links. None of it may be read as
a bundle page: the scan neither reports it, repairs it, prunes it as an orphan, nor writes into the
clone directory.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from code_wiki_okf.config import load_config
from gw_kernel.workspace.bundle import load_workspace_bundle
from gw_wiki.scan import commands as scan
from scan_helpers import AT, PACKAGE_URI, TODAY, make_workspace, seed_graph

CLONE = Path("repositories") / "upstream" / "references" / "git"

COLLIDING = f"""---
type: Package
title: Impostor widgets
resource: {PACKAGE_URI}
generated:
  by: someone-else
  at: 2020-01-01T00:00:00Z
---

## Summary

Impostor prose that must never be read as the real widgets page.
"""

FILES = {
    "packages/widgets.md": COLLIDING,
    "docs/broken.md": "---\ntype: [unterminated\ntitle: x\n---\n\n[dead](/nowhere/at/all.md)\n",
    "README.md": "no frontmatter at all, and a [dead link](/missing.md)\n",
    "code-graph/demo/entities/packages/ghost.md": COLLIDING.replace("Impostor widgets", "Ghost"),
}


@pytest.fixture
def ready(tmp_path, scan_seed):
    layout, repo = make_workspace(tmp_path, scan_seed)
    config = load_config(
        layout.bundle_dir,
        config_path=layout.manifest_path,
        graph_dir=layout.cache_dir,
        declarations_dir=layout.config_dir,
    )
    seed_graph(config.graph_dir, repo)
    return layout, config


def _plant(layout) -> Path:
    clone = layout.bundle_dir / CLONE
    for rel, text in FILES.items():
        path = clone / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return clone


def _snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _text(result) -> str:
    return repr(result)


async def test_a_scan_apply_leaves_the_clone_byte_for_byte_untouched(ready):
    layout, config = ready
    clone = _plant(layout)
    before = _snapshot(clone)

    result = await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)

    assert _snapshot(clone) == before
    assert result.ok


async def test_a_scan_apply_never_mentions_a_clone_path(ready):
    layout, config = ready
    _plant(layout)

    result = await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)

    assert "references/git" not in _text(result)
    assert not result.errors


async def test_the_clone_does_not_change_what_the_scan_writes(ready, tmp_path_factory, scan_seed):
    layout, config = ready
    baseline_root = tmp_path_factory.mktemp("baseline")
    base_layout, base_repo = make_workspace(baseline_root, scan_seed)
    base_config = load_config(
        base_layout.bundle_dir,
        config_path=base_layout.manifest_path,
        graph_dir=base_layout.cache_dir,
        declarations_dir=base_layout.config_dir,
    )
    seed_graph(base_config.graph_dir, base_repo)

    _plant(layout)
    with_clone = await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)
    without = await scan.run_scan(base_layout, base_config, today=TODAY, at=AT, narrate=False, dry_run=False)

    assert with_clone.structural.entities.written == without.structural.entities.written
    assert with_clone.structural.entities.deleted == without.structural.entities.deleted
    assert with_clone.ok == without.ok

    def outside_clone(layout_) -> dict[str, str]:
        return {k: v for k, v in _snapshot(layout_.bundle_dir).items() if "references/git" not in k}

    assert outside_clone(layout) == outside_clone(base_layout)


async def test_the_colliding_clone_page_does_not_shadow_the_real_entity(ready):
    layout, config = ready
    _plant(layout)

    await scan.run_scan(layout, config, today=TODAY, at=AT, narrate=False, dry_run=False)

    real = (layout.bundle_dir / "code-graph" / "demo" / "entities" / "packages" / "widgets.md").read_text(
        encoding="utf-8"
    )
    assert "Impostor" not in real
    bundle = load_workspace_bundle(layout)
    assert not [cid for cid in bundle.concepts if "references/git" in cid]
