from __future__ import annotations

from datetime import date
from pathlib import Path

from code_wiki_okf.init import init_bundle
from code_wiki_okf.sync.rule import CODES, TOPIC, sync_rule
from code_wiki_okf.sync.snapshot import SyncSnapshot
from okf_io import load_bundle, validate

_TODAY = date(2026, 1, 1)


def _write_concept(root: Path, relative: str, resource: str, type_: str = "Package") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {type_}\ntitle: {relative}\nresource: {resource}\n---\n\n## Purpose\n\nA test page.\n",
        encoding="utf-8",
    )


def test_topic_is_sync() -> None:
    assert TOPIC == "sync"
    assert CODES == ("sync.stale-page", "sync.missing-page", "sync.orphan-page")


def test_empty_snapshot_yields_nothing(tmp_path: Path) -> None:
    init_bundle(tmp_path, today=_TODAY, dry_run=False)
    bundle = load_bundle(tmp_path)
    report = validate(bundle, today=_TODAY, extra_rules=[sync_rule(SyncSnapshot.empty())])
    assert not report.by_code("sync.stale-page")
    assert not report.by_code("sync.missing-page")
    assert not report.by_code("sync.orphan-page")


def test_missing_resource_yields_a_pathless_finding(tmp_path: Path) -> None:
    init_bundle(tmp_path, today=_TODAY, dry_run=False)
    bundle = load_bundle(tmp_path)
    snapshot = SyncSnapshot(missing=frozenset({"pkg:acme/repo-a/widgets"}))
    report = validate(bundle, today=_TODAY, extra_rules=[sync_rule(snapshot)])
    findings = report.by_code("sync.missing-page")
    assert len(findings) == 1
    assert findings[0].path is None
    assert findings[0].severity == "warn"


def test_stale_resource_yields_a_finding_anchored_to_its_page(tmp_path: Path) -> None:
    init_bundle(tmp_path, today=_TODAY, dry_run=False)
    _write_concept(tmp_path, "packages/widgets.md", "pkg:acme/repo-a/widgets")
    bundle = load_bundle(tmp_path)
    snapshot = SyncSnapshot(stale=frozenset({"pkg:acme/repo-a/widgets"}))
    report = validate(bundle, today=_TODAY, extra_rules=[sync_rule(snapshot)])
    findings = report.by_code("sync.stale-page")
    assert len(findings) == 1
    assert findings[0].path == "packages/widgets.md"
    assert findings[0].severity == "warn"
    assert not report.by_code("sync.missing-page")
    assert not report.by_code("sync.orphan-page")


def test_stale_resource_with_no_matching_page_yields_nothing(tmp_path: Path) -> None:
    """The lookup path genuinely runs `resource_index` -- an unresolvable
    resource (no page carries it) yields no finding at all, rather than a
    finding with a guessed or empty path.
    """
    init_bundle(tmp_path, today=_TODAY, dry_run=False)
    bundle = load_bundle(tmp_path)
    snapshot = SyncSnapshot(stale=frozenset({"pkg:acme/repo-a/nowhere"}))
    report = validate(bundle, today=_TODAY, extra_rules=[sync_rule(snapshot)])
    assert not report.by_code("sync.stale-page")


def test_orphaned_resource_yields_a_finding_anchored_to_its_page(tmp_path: Path) -> None:
    init_bundle(tmp_path, today=_TODAY, dry_run=False)
    _write_concept(tmp_path, "packages/ghost.md", "pkg:acme/repo-a/ghost")
    bundle = load_bundle(tmp_path)
    snapshot = SyncSnapshot(orphaned=frozenset({"pkg:acme/repo-a/ghost"}))
    report = validate(bundle, today=_TODAY, extra_rules=[sync_rule(snapshot)])
    findings = report.by_code("sync.orphan-page")
    assert len(findings) == 1
    assert findings[0].path == "packages/ghost.md"
    assert findings[0].severity == "warn"
    assert not report.by_code("sync.stale-page")
    assert not report.by_code("sync.missing-page")


def test_orphaned_resource_with_no_matching_page_yields_nothing(tmp_path: Path) -> None:
    """Same guard as `.stale`, exercised on the `.orphaned` branch."""
    init_bundle(tmp_path, today=_TODAY, dry_run=False)
    bundle = load_bundle(tmp_path)
    snapshot = SyncSnapshot(orphaned=frozenset({"pkg:acme/repo-a/nowhere"}))
    report = validate(bundle, today=_TODAY, extra_rules=[sync_rule(snapshot)])
    assert not report.by_code("sync.orphan-page")


def test_strict_promotes_sync_findings_to_error(tmp_path: Path) -> None:
    init_bundle(tmp_path, today=_TODAY, dry_run=False)
    _write_concept(tmp_path, "packages/widgets.md", "pkg:acme/repo-a/widgets")
    _write_concept(tmp_path, "packages/ghost.md", "pkg:acme/repo-a/ghost")
    bundle = load_bundle(tmp_path)
    snapshot = SyncSnapshot(
        stale=frozenset({"pkg:acme/repo-a/widgets"}),
        missing=frozenset({"pkg:acme/repo-a/widgets"}),
        orphaned=frozenset({"pkg:acme/repo-a/ghost"}),
    )
    report = validate(bundle, today=_TODAY, extra_rules=[sync_rule(snapshot)], strict=True)
    assert report.by_code("sync.stale-page")[0].severity == "error"
    assert report.by_code("sync.missing-page")[0].severity == "error"
    assert report.by_code("sync.orphan-page")[0].severity == "error"
    assert not report.ok
