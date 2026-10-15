"""A workspace that renames stage artifacts is read and stamped under its names (design §3.2, §5)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from gw_kernel import apply_init, plan_init
from gw_kernel.workspace.anchor import spec_ref
from gw_kernel.workspace.dispatch_artifacts import missing_design_source
from gw_kernel.workspace.dispatch_config import load_dispatch_config
from gw_kernel.workspace.layout import layout_for
from gw_work import commands as work
from gw_work.reconcile import run_reconcile_context
from okf_io import load_bundle
from ruamel.yaml import YAML
from work_tracker_okf.items import IGNORE, load_items


def _set_pipeline(layout, **blocks: object) -> None:
    yaml = YAML()
    target = layout.root / "workflow.yaml"
    document = yaml.load(target.read_text(encoding="utf-8")) or {}
    document.setdefault("pipeline", {}).update(blocks)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.dump(document, handle)


TODAY = date(2026, 8, 23)


def _workspace(tmp_path: Path, manifest: str = "version: 1\n"):
    apply_init(plan_init(tmp_path, today=TODAY, topic="Artifacts"))
    manifest += "workflow_config: workflow.yaml\n"
    (tmp_path / "workflow.yaml").write_text("pipeline:\n  rules: []\n", encoding="utf-8")
    (tmp_path / "workspace.yaml").write_text(manifest, encoding="utf-8")
    layout = layout_for(tmp_path)
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    layout.cache_dir.mkdir(parents=True, exist_ok=True)
    return layout


def _write(
    layout,
    path: str,
    *,
    type: str = "Feature",
    phase: str | None = "plan",
    work_status: str = "open",
    affects: tuple[str, ...] = ("packages/a",),
    extra: str = "",
) -> None:
    page = layout.bundle_dir / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    rendered = "affects: []" if not affects else "affects:\n" + "".join(f"- {entry}\n" for entry in affects).rstrip()
    phase_line = f"phase: {phase}\n" if phase is not None else ""
    page.write_text(
        f"---\ntype: {type}\ntitle: {path}\ndescription: d\nstatus: stable\n"
        f"work_status: {work_status}\n{phase_line}effort: medium\nopened: 2026-08-01\n"
        f"updated: 2026-08-01\n{extra}{rendered}\n---\n\n## Summary\nd\n\n## Plan\n\n"
        "| Action | Done when | Rationale |\n| --- | --- | --- |\n",
        encoding="utf-8",
    )


RENAMED = {"design": {"file": "spec.md"}, "execute": {"file": "coverage.md", "required": False}}


def test_spec_ref_and_missing_design_source_read_the_renamed_file(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _set_pipeline(layout, artifacts=RENAMED)
    _write(layout, "work/feature-x", phase="design")
    (layout.bundle_dir / "work/feature-x/references").mkdir(parents=True)
    (layout.bundle_dir / "work/feature-x/references/spec.md").write_text("# Spec\n", encoding="utf-8")
    definition = load_dispatch_config(layout).definition
    item = next(i for i in load_items(load_bundle(layout.bundle_dir, ignore=IGNORE)) if i.path == "work/feature-x")
    assert spec_ref(item, definition=definition) == "work/feature-x/references/spec.md"
    missing = missing_design_source(layout.bundle_dir, item, definition=definition)
    assert missing is not None and missing[0].rel.endswith("references/spec.md")
    assert "no design spec" not in " ".join(run_reconcile_context(layout, "work/feature-x").warnings)


def test_next_artifact_names_the_renamed_design(tmp_path: Path) -> None:
    layout = _workspace(tmp_path)
    _set_pipeline(layout, artifacts=RENAMED)
    _write(layout, "work/feature-x", phase="design")
    result = work.run_next(layout, "work/feature-x")
    assert result.artifact is not None and result.artifact.rel.endswith("references/spec.md")
