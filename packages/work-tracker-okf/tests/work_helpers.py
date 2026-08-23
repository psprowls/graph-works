"""Small constructors and vault helpers for path-native work-item tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from okf_io import Finding, Report, load, load_bundle, validate
from work_tracker_okf.items import IGNORE, WorkItem
from work_tracker_okf.paths import item_page

CONFORMANT_ROOT = Path(__file__).parent / "fixtures" / "conformant"
CONFORMANT_TODAY = date(2026, 3, 7)
NONCONFORMANT_ROOT = Path(__file__).parent / "fixtures" / "nonconformant"
NONCONFORMANT_REPO = Path(__file__).parent / "fixtures" / "nonconformant_repo"
NONCONFORMANT_GOLDEN = Path(__file__).parent / "fixtures" / "nonconformant.golden.txt"
NONCONFORMANT_TODAY = date(2026, 8, 3)

EMPTY_PLAN_BODY = "\n## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n"


_DEFAULTS: dict[str, Any] = {
    "page_path": None,
    "basename": None,
    "archived": False,
    "type": "Feature",
    "title": "",
    "description": "",
    "status": "stable",
    "work_status": "open",
    "phase": None,
    "effort": None,
    "blast_radius": None,
    "target": None,
    "opened": "",
    "updated": "",
    "affects": (),
    "parent_path": None,
    "ancestor_paths": (),
    "active_child_paths": (),
    "archived_child_paths": (),
    "dependency_edges": (),
    "dependency_issues": (),
    "owner": None,
    "resolved_in": None,
    "worktree": None,
    "branch": None,
    "superseded_by": None,
    "tags": (),
    "sources": (),
    "has_design_artifact": False,
    "has_plan_artifact": False,
    "version": None,
    "target_date": None,
    "released_at": None,
}


def make_item(path: str, **overrides: Any) -> WorkItem:
    """Construct a path-keyed item without a filesystem bundle."""
    if not path.startswith("work/"):
        path = f"work/{path}"
    defaults = dict(_DEFAULTS)
    defaults["page_path"] = f"{path}.md"
    defaults["basename"] = path.rsplit("/", 1)[-1]
    return WorkItem(path=path, **{**defaults, **overrides})


def write_item(root: Path, path: str, frontmatter: str, *, body: str = EMPTY_PLAN_BODY) -> Path:
    """Write one page at a canonical extensionless item *path*."""
    if not path.startswith("work/"):
        path = f"work/{path}"
    page = root / f"{path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"---\ntitle: T\ndescription: D\n{frontmatter}---\n{body}", encoding="utf-8")
    return page


def load_written_items(root: Path) -> tuple[WorkItem, ...]:
    from work_tracker_okf.items import load_items

    return load_items(load_bundle(root, ignore=IGNORE))


def make_terminal(root: Path, path: str, *, status: str = "resolved") -> None:
    """Set one copied fixture page's path-native work status."""
    if not path.startswith("work/"):
        path = f"work/{path}"
    document = load(item_page(path).path(root))
    document.set("work_status", status)
    document.save()


def lane_report(root: Path, *, today: date = NONCONFORMANT_TODAY, repo_root: Path | None = None) -> Report:
    """Validate *root* with the complete work-tracker lane rule bundle."""
    from work_tracker_okf.rules import lane_rules

    return validate(load_bundle(root, ignore=IGNORE), today=today, extra_rules=lane_rules(repo_root=repo_root))


def render_finding(finding: Finding) -> str:
    """Render one finding as the catalog golden's reviewable line."""
    where = finding.path or "-"
    if finding.line is not None:
        where = f"{where}:{finding.line}"
    return f"{finding.severity}\t{finding.code}\t{where}\t{finding.spec}\t{finding.message}"
