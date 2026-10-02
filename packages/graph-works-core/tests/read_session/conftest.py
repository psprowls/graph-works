"""Generated, aged bundle shared by the read-session backend suites."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
from graph_works_core.workspace.init import apply_init, plan_init
from graph_works_core.workspace.layout import WorkspaceLayout

OKF_IO_FIXTURES = Path(__file__).resolve().parents[3] / "okf-io" / "tests" / "fixtures"
FIXTURE_BUNDLES = [
    OKF_IO_FIXTURES / "bundles" / "acme_retail",
    OKF_IO_FIXTURES / "bundles" / "ga4",
    OKF_IO_FIXTURES / "edge",
    OKF_IO_FIXTURES / "nonconformant",
]
CLONE_ROOT = "repositories/clone/references/git"
FILES: dict[str, str] = {
    "work/epic-a.md": (
        "---\ntype: Epic\ntitle: A\nwork_status: open\nphase: plan\n---\n"
        "[d](/work/epic-a/references/01-design.md) [x](/docs/missing.md)\n"
    ),
    "work/epic-a/references/01-design.md": "---\ntitle: Design\n---\n[back](/work/epic-a.md)\n# Design\n",
    "work/epic-a/children/bug-b.md": "---\ntype: Bug\ntitle: B\nwork_status: open\n---\n[p](/work/epic-a.md)\n",
    "docs/explanations/p.md": (
        "---\ntype: Explanation\ntitle: P\ntags: [a]\n---\n"
        "[w](/work/epic-a.md) [r](/work/epic-a/references/01-design.md)\n## H\n"
    ),
    "docs/explanations/yaml.md": "---\ntitle: [unclosed\n---\n",
    "docs/explanations/café.md": "---\ntitle: NFC\n---\n",
    "index.md": "# Index\n[p](/docs/explanations/p.md)\n",
    "log.md": "## 2026-10-02\n",
    "pic.png": "png",
    "work/epic-a/.DS_Store": "x",
    f"{CLONE_ROOT}/README.md": "clone content",
}


def write_bundle(bundle_dir: Path) -> Path:
    for rel, text in FILES.items():
        path = bundle_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    (bundle_dir / "docs/explanations/bad.md").write_bytes(b"\xff\xfe bad")
    (bundle_dir / "work/epic-a/references/undecodable.md").write_bytes(b"\xff\xfe bad")
    old = 1_600_000_000_000_000_000
    for path in bundle_dir.rglob("*"):
        os.utime(path, ns=(old, old))
    return bundle_dir


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceLayout:
    layout = apply_init(plan_init(tmp_path / ".works", today=date(2026, 10, 2), topic="t")).layout
    write_bundle(layout.bundle_dir)
    return layout
