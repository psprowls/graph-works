from __future__ import annotations

import ast
import shutil
import sys
from datetime import date
from pathlib import Path

from code_wiki_okf.config import Config, StateGateConfig
from graph_works_core import apply_init, plan_init
from graph_works_core.work import commands as work

TODAY = date(2026, 8, 23)
LEGACY_FIXTURE = Path(__file__).parents[3] / "work-tracker-okf/tests/fixtures/legacy_graph_wiki"


def _workspace(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "packages/work-tracker-okf").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=TODAY, topic="Work")).layout
    shutil.rmtree(layout.bundle_dir / "work", ignore_errors=True)
    shutil.copytree(LEGACY_FIXTURE / "work", layout.bundle_dir / "work")
    return layout


def _config(layout) -> Config:
    return Config(
        graph_dir=layout.cache_dir / "graph",
        declarations_dir=layout.config_dir,
        repos=(),
        state_gate=StateGateConfig(enabled=False, branches=("main",)),
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_run_migrate_requires_explicit_apply(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    before = _snapshot(layout.bundle_dir)

    preview = work.run_migrate_layout(layout, apply=False)

    assert preview.plan.ok
    assert preview.application is None
    assert _snapshot(layout.bundle_dir) == before

    applied = work.run_migrate_layout(layout, apply=True)

    assert applied.application is not None and applied.application.ok
    assert work.run_lint(layout, _config(layout), today=TODAY).ok
    assert (layout.bundle_dir / "work/epic-parent/children/feature-child.md").is_file()
    assert not (layout.bundle_dir / "work/2026-08-22-feature-child.md").exists()


def test_run_migrate_rejects_a_source_changed_after_planning(tmp_path: Path, monkeypatch) -> None:
    from work_tracker_okf import migration

    layout = _workspace(tmp_path)
    page = layout.bundle_dir / "work/2026-08-22-feature-child.md"
    original = migration.plan_migration
    external = page.read_bytes() + b"\nexternal owner\n"

    def inject_after_planning(bundle):
        plan = original(bundle)
        page.write_bytes(external)
        return plan

    monkeypatch.setattr(migration, "plan_migration", inject_after_planning)

    result = work.run_migrate_layout(layout, apply=True)

    assert result.application is not None and not result.application.ok
    assert page.read_bytes() == external
    assert not (layout.bundle_dir / "work/epic-parent/children/feature-child.md").exists()


def test_run_migrate_retains_the_domain_planners_frontmatter_preimage(tmp_path: Path, monkeypatch) -> None:
    from work_tracker_okf import migration

    layout = _workspace(tmp_path)
    page = layout.bundle_dir / "work/2026-08-22-feature-child.md"
    original = migration._plan_path_mutation
    external = page.read_text(encoding="utf-8").replace(
        "title: Child migration",
        "title: Externally retitled",
    )

    def inject_after_domain_planning(*args, **kwargs):
        plan = original(*args, **kwargs)
        page.write_text(external, encoding="utf-8")
        return plan

    monkeypatch.setattr(migration, "_plan_path_mutation", inject_after_domain_planning)

    result = work.run_migrate_layout(layout, apply=True)

    assert result.application is not None and not result.application.ok
    assert page.read_text(encoding="utf-8") == external
    assert not (layout.bundle_dir / "work/epic-parent/children/feature-child.md").exists()


def test_normal_work_commands_do_not_eagerly_import_legacy_migration() -> None:
    sys.modules.pop("work_tracker_okf.migration", None)

    assert "work_tracker_okf.migration" not in sys.modules
    assert "work_tracker_okf.migration" not in work.__dict__
    tree = ast.parse(Path(work.__file__).read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "work_tracker_okf.migration" for node in tree.body
    )
