"""Task 6 (child 4's done-when): prove the full pipeline end to end.

- A page whose `last_updated_commit` predates the file's latest change fires
  exactly one `sync.stale-page` for that resource.
- Both vendored okf-io bundles (`acme_retail`, `ga4`) report zero `sync.*`
  findings against an empty snapshot -- they carry no `file:`/`pkg:`-tracked
  sources this package's mirror or entity lanes would recognize.
- Orphan detection survives a prose edit: a page whose source vanished is
  still reported even though `apply_mirror`'s deletion guard declines to
  delete it (the report/write divergence the epic's done-when calls out).

This is the last of six tasks; Tasks 1-5 (token stamping, `plan_entities`,
`SyncSnapshot`/`snapshot_bundle`, `sync_rule`, and `cli.py validate`) are
already shipped and reviewed. Nothing here writes to the vendored bundles --
only `load_bundle`/`validate` ever touch `acme_retail`/`ga4`.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from code_graph_io import open_reader
from code_graph_io import update as graph_update
from code_wiki_okf.config import Config, RepoConfig, StateGateConfig
from code_wiki_okf.init import install_bundle
from code_wiki_okf.mirror.apply import apply_mirror
from code_wiki_okf.mirror.plan import plan_mirror
from code_wiki_okf.mirror.walk import tracked_files
from code_wiki_okf.sync.rule import sync_rule
from code_wiki_okf.sync.snapshot import SyncSnapshot, snapshot_bundle
from okf_ext.shape import load_sections
from okf_io import load_bundle, validate

_TODAY = date(2026, 1, 1)
_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


@pytest.fixture
def synced_bundle(tmp_path: Path):
    repo_root = tmp_path / "repo-a"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "mod.py").write_text("VALUE = 1\n")
    _git(["init", "-q", "-b", "main"], repo_root)
    _git(["config", "user.email", "t@t"], repo_root)
    _git(["config", "user.name", "t"], repo_root)
    _git(["add", "-A"], repo_root)
    _git(["commit", "-q", "-m", "init"], repo_root)
    first_sha = _git(["rev-parse", "HEAD"], repo_root)

    graph_dir = tmp_path / "graph"
    graph_update.run_workspace([repo_root], graph_dir=graph_dir, full=True)

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    config = Config(
        graph_dir=graph_dir,
        declarations_dir=bundle_root,
        repos=(RepoConfig(name="repo-a", path=repo_root, ignore=()),),
        state_gate=StateGateConfig(enabled=False, branches=()),
    )
    section_set = load_sections(bundle_root / "sections")

    with open_reader(graph_dir=graph_dir) as reader:
        walked = tracked_files(config)
        repo = config.repos[0]
        plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=walked[repo.name], sha=first_sha, at=_AT)
        apply_mirror(load_bundle(bundle_root), plan, repo, section_set=section_set)

    return repo_root, graph_dir, bundle_root, config, first_sha


def test_doctored_last_updated_commit_fires_stale_page(synced_bundle) -> None:
    repo_root, graph_dir, bundle_root, config, _first_sha = synced_bundle

    # The file changes content that shows up in the mirrored page's generated
    # sections (a new top-level symbol, not just a literal edit) -- the
    # on-disk page's `last_updated_commit` now predates the tracked source,
    # and the render `plan_mirror` would propose genuinely differs from what
    # is on disk, so it lands in `plan.updates` and therefore `.stale`.
    (repo_root / "src" / "mod.py").write_text("VALUE = 2\n\n\ndef helper():\n    return VALUE\n")
    _git(["add", "-A"], repo_root)
    _git(["commit", "-q", "-m", "bump"], repo_root)
    graph_update.run_workspace([repo_root], graph_dir=graph_dir, full=True)

    with open_reader(graph_dir=graph_dir) as reader:
        snapshot = snapshot_bundle(load_bundle(bundle_root), config, reader, at=_AT)

    assert "file:repo-a/src/mod.py" in snapshot.stale

    report = validate(load_bundle(bundle_root), today=_TODAY, extra_rules=[sync_rule(snapshot)])
    findings = report.by_code("sync.stale-page")
    assert len(findings) == 1
    assert "file:repo-a/src/mod.py" in findings[0].message


def test_orphan_after_source_removed_regardless_of_prose_edit(synced_bundle) -> None:
    repo_root, graph_dir, bundle_root, config, _first_sha = synced_bundle

    page_path = bundle_root / "repositories" / "repo-a" / "fs" / "src" / "mod.py.md"
    assert page_path.exists()

    # A human-authored prose edit -- `apply_mirror`'s deletion guard (see
    # `mirror/plan.py::_notes_matches_placeholder`) will decline to delete a
    # page like this once its source disappears. The report must diverge
    # from what a write would do: `.orphaned` still has to flag it.
    placeholder = (
        "> TODO: anything a reader should know about this file that the generated sections below don't capture."
    )
    text = page_path.read_text()
    assert placeholder in text
    page_path.write_text(text.replace(placeholder, "> Hand-written notes a human added about this file."))

    (repo_root / "src" / "mod.py").unlink()
    _git(["add", "-A"], repo_root)
    _git(["commit", "-q", "-m", "remove mod.py"], repo_root)
    graph_update.run_workspace([repo_root], graph_dir=graph_dir, full=True)

    with open_reader(graph_dir=graph_dir) as reader:
        snapshot = snapshot_bundle(load_bundle(bundle_root), config, reader, at=_AT)

    assert "file:repo-a/src/mod.py" in snapshot.orphaned

    report = validate(load_bundle(bundle_root), today=_TODAY, extra_rules=[sync_rule(snapshot)])
    findings = report.by_code("sync.orphan-page")
    assert len(findings) == 1
    assert findings[0].path == "repositories/repo-a/fs/src/mod.py.md"


def test_vendored_bundles_report_zero_sync_findings() -> None:
    here = Path(__file__).resolve()
    workspace_root = next(
        parent
        for parent in here.parents
        if (parent / "packages" / "okf-io" / "tests" / "fixtures" / "bundles").is_dir()
    )
    vendored = workspace_root / "packages" / "okf-io" / "tests" / "fixtures" / "bundles"

    for name in ("acme_retail", "ga4"):
        bundle = load_bundle(vendored / name)
        report = validate(bundle, today=_TODAY, extra_rules=[sync_rule(SyncSnapshot.empty())])
        assert not report.by_code("sync.stale-page"), name
        assert not report.by_code("sync.missing-page"), name
        assert not report.by_code("sync.orphan-page"), name
