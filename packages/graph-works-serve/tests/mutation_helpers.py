# packages/graph-works-serve/tests/mutation_helpers.py
"""Plain helpers (not fixtures) for the mutation-route suites."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_serve.app import build_app
from graph_works_serve.context import ServeContext
from graph_works_serve.routes import ROUTES, RouteSpec
from starlette.testclient import TestClient

TODAY = date(2026, 9, 18)
TOKEN = "t" * 43
PORT = 49152


def serve_workspace(tmp_path: Path) -> WorkspaceLayout:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/a").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Serve")).layout
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def write_item(
    layout: WorkspaceLayout,
    path: str,
    *,
    work_status: str,
    phase: str,
    status: str = "draft",
    effort: str | None = None,
) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    effort_line = f"effort: {effort}\n" if effort else ""
    page.write_text(
        f"---\ntype: Feature\ntitle: {path}\ndescription: d\nstatus: {status}\n"
        f"work_status: {work_status}\nphase: {phase}\n{effort_line}opened: 2026-09-01\n"
        "updated: 2026-09-01\naffects:\n- packages/a\n---\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
        newline="",
    )


def seed_plan_doc(layout: WorkspaceLayout, path: str) -> None:
    """The plan artifact a completed plan stage leaves behind at
    `<item>/references/02-plan.md`. An advance out of `phase: plan` stamps the
    `## Plan` table's "Execute implementation plan: ..." row pointing at it, in
    the root-absolute spelling every citation in this bundle uses;
    `plan.action-target-missing` now checks that against the bundle root
    (`vault_root` is wired everywhere the rule is composed), so a fixture that
    puts an item at `phase: plan` and then completes it needs this file to
    already exist, matching what a real plan-writing stage leaves behind."""
    references = layout.bundle_dir / path / "references"
    references.mkdir(parents=True, exist_ok=True)
    (references / "02-plan.md").write_text("# Plan\n", encoding="utf-8")


def write_proposal(layout: WorkspaceLayout, slug: str, *, target: str, page_status: str = "proposed") -> str:
    member = f"proposals/{slug}.md"
    page = layout.bundle_dir / member
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: Proposal\ntitle: {slug}\ndescription: d\ntarget: {target}\n"
        f"page_status: {page_status}\n---\n\nWhy.\n",
        encoding="utf-8",
        newline="",
    )
    return member


def snapshot(layout: WorkspaceLayout) -> dict[str, bytes]:
    root = layout.bundle_dir
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def make_client(layout: WorkspaceLayout, *, routes: Sequence[RouteSpec] = ROUTES) -> tuple[TestClient, dict[str, str]]:
    context = ServeContext(layout.root, layout.root, PORT, 4242, "0.0.0-test")
    app = build_app(context, token=TOKEN, routes=routes)
    client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    return client, {"Authorization": f"Bearer {TOKEN}"}
