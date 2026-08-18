"""Constructors for tests that need items without a vault.

Named `work_helpers`, not `helpers`: the repo root puts
`packages/okf-io/tests` on `pythonpath`, and a second `helpers.py` on that
path resolves to whichever pytest imported first. The root `pyproject.toml`
records the rule; `okf-ext` follows it with `ext_helpers.py`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from okf_io import Finding, Report, load, load_bundle, validate
from work_tracker_okf.items import IGNORE, WorkItem
from work_tracker_okf.paths import item_page

#: Every `WorkItem` field but `slug` and `path`, at the value the projection
#: gives a page that does not carry the key.
_DEFAULTS: dict[str, Any] = {
    "archived": False,
    "type": "Feature",
    "title": "",
    "description": "",
    "status": "stable",
    "workflow_status": "open",
    "phase": None,
    "effort": None,
    "opened": "",
    "updated": "",
    "affects": (),
    "parent": None,
    "depends_on": (),
    "children": (),
    "owner": None,
    "resolved_in": None,
    "worktree": None,
    "branch": None,
    "superseded_by": None,
    "tags": (),
    "sources": (),
    "has_spec_doc": False,
    "has_plan_doc": False,
}


def make_item(slug: str, **overrides: Any) -> WorkItem:
    """A `WorkItem` for *slug*, with `**overrides` applied over the defaults.

    `children` is settable here even though `load_items` derives it — the
    graph tests want to construct a shape, not a vault.
    """
    fields = {**_DEFAULTS, "path": f"work/{slug}.md", **overrides}
    return WorkItem(slug=slug, **fields)


#: The conformant vault as committed. Read-only — tests use the `conformant_root`
#: fixture, which copies it and materializes the declarations.
CONFORMANT_ROOT = Path(__file__).parent / "fixtures" / "conformant"

#: The vault's own "today". Fixed, because `validate` takes a required `today=`
#: and a moving one would make `lifecycle.stale` a calendar-dependent test.
CONFORMANT_TODAY = date(2026, 3, 7)


def make_terminal(root: Path, slug: str, *, status: str = "resolved") -> None:
    """Flip an item page's `workflow_status` in a **copied** vault.

    The conformant vault's only working directory with real artifacts (a
    design spec, a plan, a transcript) belongs to an in-progress item — the
    epic's own working directory holds nothing but its decisions ledger — and
    its only terminal active item has no working directory. Every two-path
    assertion needs one item that is both, and manufacturing it on the copy is
    cheaper — and more honest — than a second fixture vault whose only
    difference is one word.
    """
    page = item_page(slug).path(root)
    document = load(page)
    document.set("workflow_status", status)
    document.save()


#: The whole-catalog vault, its synthetic repo, its reviewed golden, and its
#: fixed "today". Mirrors okf-io's `NONCONFORMANT` / `GOLDEN` pair.
NONCONFORMANT_ROOT = Path(__file__).parent / "fixtures" / "nonconformant"
NONCONFORMANT_REPO = Path(__file__).parent / "fixtures" / "nonconformant_repo"
NONCONFORMANT_GOLDEN = Path(__file__).parent / "fixtures" / "nonconformant.golden.txt"
NONCONFORMANT_TODAY = date(2026, 8, 3)

#: The empty plan table `assets/_sections/_fragments.work_tracker.yaml` seeds,
#: so a page written by `write_item` reads back as `empty` rather than
#: `missing`.
EMPTY_PLAN_BODY = "\n## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n"


def write_item(root: Path, slug: str, frontmatter: str, *, body: str = EMPTY_PLAN_BODY) -> Path:
    """One item page under `<root>/work/`, for a `tmp_path` vault.

    *frontmatter* is the item-specific block and must carry its own `type:`;
    `title` and `description` are supplied so no caller repeats OKF's required
    three. Mirrors okf-io's `test_rules_*.py` idiom, which writes the page it is
    about to make a claim on rather than reaching for a shared fixture.
    """
    page = root / "work" / f"{slug}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"---\ntitle: T\ndescription: D\n{frontmatter}---\n{body}", encoding="utf-8")
    return page


def lane_report(root: Path, *, today: date = NONCONFORMANT_TODAY, repo_root: Path | None = None) -> Report:
    """Validate *root* with the lane bundle added. Deliberately goes through
    `validate()` rather than calling rules directly: that is the only path that
    exercises the reserved-prefix guard the four topic names had to clear."""
    from work_tracker_okf.rules import lane_rules

    return validate(load_bundle(root, ignore=IGNORE), today=today, extra_rules=lane_rules(repo_root=repo_root))


def render_finding(finding: Finding) -> str:
    """One finding as one reviewable line, byte-identical in shape to okf-io's
    `helpers.render_finding`. Rendering lives in the tests, not in the package."""
    where = finding.path or "-"
    if finding.line is not None:
        where = f"{where}:{finding.line}"
    return f"{finding.severity}\t{finding.code}\t{where}\t{finding.spec}\t{finding.message}"
