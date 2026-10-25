from __future__ import annotations

from pathlib import Path

import gw_kernel

TEMPLATE = Path(gw_kernel.__file__).parent / "workspace" / "assets" / "AGENTS.md.template"


def test_agents_template_describes_computed_placement() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "`{{CONFIG_DIR}}/worktrees/` — one worktree per code-touching work item" in text
    assert "created, refreshed and removed only by `gw work place`" in text
    assert "`place`" in text
    assert "`phase`, `work_status`, `repo`, `start_sha`, `affects`, `depends_on`" in text
    for retired in ("record-placement", "`worktree`, `branch`", "feature-work worktrees"):
        assert retired not in text
