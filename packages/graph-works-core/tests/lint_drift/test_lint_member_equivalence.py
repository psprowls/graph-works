"""Both lint entry points retain ignored-member identity across host filesystems."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unicodedata import normalize
from urllib.parse import quote

import pytest
from code_wiki_okf.config import load_config
from graph_works_core import apply_init, plan_init
from graph_works_core.lint_drift.lanes import compose_lanes
from graph_works_core.lint_drift.lint import _lane_reports
from graph_works_core.work.commands import run_lint
from graph_works_core.workspace.bundle import load_workspace_bundle
from okf_io import validate
from work_tracker_okf.items import IGNORE

TODAY = date(2026, 10, 2)
AT = datetime(2026, 10, 2, tzinfo=UTC)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _legacy_ignore(root):
    # Frozen pre-scope partition: use the actual ignored-member loader as oracle.
    return (
        *(f"{p.name}/*" if p.is_dir() else p.name for p in sorted(root.iterdir()) if p.name != "work"),
        *IGNORE,
    )


@pytest.mark.parametrize("entry_point", ["work", "lane"])
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("spelling", ["case", "nfd-file", "nfc-file"])
def test_lint_preserves_legacy_cross_lane_member_matching(tmp_path, monkeypatch, entry_point, strict, spelling):
    layout = apply_init(plan_init(tmp_path / "workspace", today=TODAY, topic="Membership")).layout
    root = layout.bundle_dir
    docs = root / "docs"
    raw = "target.md" if spelling == "case" else normalize("NFD" if spelling == "nfd-file" else "NFC", "café.md")
    query = "TARGET.md" if spelling == "case" else normalize("NFC" if spelling == "nfd-file" else "NFD", raw)
    _write(docs / raw, "---\ntype: Explanation\ntitle: Target\n---\n\nBody.\n")
    # Always include one exact working link and one missing link: equality cannot
    # pass just because no work page or no link findings reached validation.
    _write(
        root / "work/bug-links.md",
        "---\ntype: Bug\ntitle: Links\ndescription: d\nwork_status: open\nphase: design\n"
        "opened: 2026-10-02\nupdated: 2026-10-02\n---\n\n"
        f"See [exact](/docs/{quote(raw)}) and [variant](/docs/{quote(query)}) and [missing](/docs/missing.md).\n",
    )
    original_lstat = Path.lstat

    def modeled_lstat(path, *args, **kwargs):
        if path.parent == docs:
            if spelling == "case" and path.name == query:
                # A case-sensitive host must still reproduce a case-insensitive
                # lookup. Only the one mismatched leaf query is redirected.
                return original_lstat(docs / raw, *args, **kwargs)
            if spelling != "case" and path.name not in {p.name for p in docs.iterdir()}:
                # Real raw directory entries plus a narrowly scoped lstat model
                # reproduce normalization-sensitive lookup on any host.
                raise FileNotFoundError(path)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", modeled_lstat)
    config = load_config(
        root, config_path=layout.manifest_path, graph_dir=layout.cache_dir, declarations_dir=layout.config_dir
    )
    lanes = compose_lanes(layout, config, at=AT)
    assert lanes.errors == ()
    [lane] = [lane for lane in lanes.lanes if lane.name == "work"]
    legacy_bundle = load_workspace_bundle(layout, ignore=_legacy_ignore(root))
    legacy = validate(legacy_bundle, today=TODAY, extra_rules=lane.rules, strict=strict)
    assert "work/bug-links" in legacy_bundle.concepts
    raw_member = f"docs/{next(docs.iterdir()).name}"
    assert raw_member in legacy_bundle.ignored
    assert legacy_bundle.member_id(f"docs/{query}") == (None if spelling == "case" else raw_member)
    expected_broken = [f for f in legacy.findings if f.code == "links.broken"]
    assert len(expected_broken) == (2 if spelling == "case" else 1)
    assert any("missing.md" in f.message for f in expected_broken)
    assert all(f.path == "work/bug-links.md" for f in expected_broken)
    assert not any(quote(raw) in f.message for f in expected_broken)

    if entry_point == "work":
        actual = run_lint(layout, config, today=TODAY, strict=strict)
    else:
        reports, bundles, errors = _lane_reports((lane,), today=TODAY, strict=strict)
        assert errors == ()
        assert "work/bug-links" in bundles["work"].concepts
        [report] = reports
        actual = report.report
    assert actual.findings == legacy.findings
