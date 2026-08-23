"""`run_tokens_update`: one bundle walk, one stamped key, every other byte
left alone.

Byte fidelity is the property that makes this worth building on okf-io rather
than on a line filter: the count changes, and nothing else in the file does.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from code_graph_io import open_reader
from code_graph_io.records import GraphNode, GraphRecords
from code_graph_io.testing import open_store
from code_wiki_okf.config import Config, RepoConfig, StateGateConfig
from code_wiki_okf.init import install_bundle
from code_wiki_okf.sync import sync_bundle
from graph_works_core.util import commands
from graph_works_core.util.commands import SkippedPage, run_tokens_update
from graph_works_core.workspace.layout import WorkspaceLayout, layout_for

PAGE = """---
type: Explanation
title: {title}
---

# {title}

Body text for {title}.
"""


def _layout(tmp_path: Path):
    layout = layout_for(tmp_path / ".works")
    layout.bundle_dir.mkdir(parents=True)
    (layout.bundle_dir / "index.md").write_text("# Index\n", encoding="utf-8")
    (layout.bundle_dir / "log.md").write_text("# Log\n", encoding="utf-8")
    return layout


def _write(layout, member: str, text: str) -> Path:
    path = layout.bundle_dir / f"{member}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_first_run_stamps_every_page(tmp_path):
    layout = _layout(tmp_path)
    _write(layout, "concepts/alpha", PAGE.format(title="Alpha"))
    _write(layout, "concepts/beta", PAGE.format(title="Beta"))

    update = run_tokens_update(layout, dry_run=False)

    assert [stamp.page for stamp in update.updated] == ["concepts/alpha", "concepts/beta"]
    assert update.unchanged == ()
    assert update.skipped == ()
    assert all(stamp.tokens > 0 for stamp in update.updated)
    assert "tokens: " in (layout.bundle_dir / "concepts/alpha.md").read_text(encoding="utf-8")


def test_a_second_run_is_entirely_unchanged(tmp_path):
    layout = _layout(tmp_path)
    _write(layout, "concepts/alpha", PAGE.format(title="Alpha"))
    first = run_tokens_update(layout, dry_run=False)
    before = (layout.bundle_dir / "concepts/alpha.md").read_bytes()

    second = run_tokens_update(layout, dry_run=False)

    assert second.updated == ()
    assert second.unchanged == first.updated
    assert (layout.bundle_dir / "concepts/alpha.md").read_bytes() == before


def test_a_stamp_touches_exactly_the_tokens_line(tmp_path):
    layout = _layout(tmp_path)
    page = _write(layout, "concepts/alpha", PAGE.format(title="Alpha"))
    before = page.read_text(encoding="utf-8").splitlines()

    update = run_tokens_update(layout, dry_run=False)

    after = page.read_text(encoding="utf-8").splitlines()
    added = [line for line in after if line not in before]
    removed = [line for line in before if line not in after]
    assert added == [f"tokens: {update.updated[0].tokens}"]
    assert removed == []


def test_an_existing_tokens_key_keeps_its_position(tmp_path):
    layout = _layout(tmp_path)
    page = _write(
        layout,
        "concepts/alpha",
        "---\ntitle: Alpha\ntokens: 1\ntype: Explanation\n---\n\n# Alpha\n",
    )

    run_tokens_update(layout, dry_run=False)

    lines = page.read_text(encoding="utf-8").splitlines()
    assert lines[2].startswith("tokens: ")
    assert lines[1] == "title: Alpha"
    assert lines[3] == "type: Explanation"


def test_a_page_without_frontmatter_is_skipped(tmp_path):
    layout = _layout(tmp_path)
    _write(layout, "concepts/bare", "# Bare\n\nNo frontmatter here.\n")

    update = run_tokens_update(layout, dry_run=False)

    assert update.skipped == (SkippedPage(page="concepts/bare", reason="no-frontmatter"),)


def test_a_page_that_will_not_parse_is_skipped(tmp_path):
    layout = _layout(tmp_path)
    _write(layout, "concepts/broken", "---\nnot: [closed\n")

    update = run_tokens_update(layout, dry_run=False)

    assert update.skipped == (SkippedPage(page="concepts/broken", reason="parse-error"),)


def test_a_member_that_cannot_be_read_is_skipped(tmp_path):
    layout = _layout(tmp_path)
    (layout.bundle_dir / "concepts").mkdir(parents=True, exist_ok=True)
    (layout.bundle_dir / "concepts/binary.md").write_bytes(b"\xff\xfe---\ntype: Explanation\n---\n")

    update = run_tokens_update(layout, dry_run=False)

    assert update.skipped == (SkippedPage(page="concepts/binary.md", reason="unreadable"),)


def test_a_page_whose_count_fails_is_skipped(tmp_path, monkeypatch):
    layout = _layout(tmp_path)
    _write(layout, "concepts/alpha", PAGE.format(title="Alpha"))

    def boom(text):
        raise RuntimeError("encoder unavailable")

    monkeypatch.setattr(commands, "count_tokens", boom)

    update = run_tokens_update(layout, dry_run=False)

    assert update.skipped == (SkippedPage(page="concepts/alpha", reason="count-failed"),)
    assert update.updated == ()


def test_a_negative_count_is_guarded_off_not_stamped(tmp_path, monkeypatch):
    """The stamped value is `tokens_value`-guarded (D-041's `>= 0` invariant),
    not the counter's raw return -- `run_tokens_update` is that guard's one
    production caller now. See 2026-08-19-tech-debt-tokens-metric-proxy-string."""
    layout = _layout(tmp_path)
    _write(layout, "concepts/alpha", PAGE.format(title="Alpha"))

    monkeypatch.setattr(commands, "count_tokens", lambda text: -1)

    update = run_tokens_update(layout, dry_run=False)

    assert update.skipped == (SkippedPage(page="concepts/alpha", reason="count-failed"),)
    assert update.updated == ()


def test_a_dry_run_reports_the_same_buckets_and_writes_nothing(tmp_path):
    layout = _layout(tmp_path)
    page = _write(layout, "concepts/alpha", PAGE.format(title="Alpha"))
    before = page.read_bytes()

    dry = run_tokens_update(layout)

    assert dry.dry_run is True
    assert [stamp.page for stamp in dry.updated] == ["concepts/alpha"]
    assert page.read_bytes() == before

    wet = run_tokens_update(layout, dry_run=False)
    assert wet.updated == dry.updated
    assert page.read_bytes() != before


def test_the_index_and_the_log_are_never_candidates(tmp_path):
    layout = _layout(tmp_path)

    update = run_tokens_update(layout, dry_run=False)

    assert update == type(update)(updated=(), unchanged=(), skipped=(), dry_run=False)
    assert "tokens:" not in (layout.bundle_dir / "index.md").read_text(encoding="utf-8")
    assert "tokens:" not in (layout.bundle_dir / "log.md").read_text(encoding="utf-8")


def test_the_entity_lane_is_reached_by_the_bundle_walk(tmp_path):
    """`code_wiki_okf.sync` no longer stamps `tokens` (it stopped
    being a proxy-per-kind key -- 2026-08-19-tech-debt-tokens-metric-proxy-string).
    This is the other half of that fix: an entity page written by the composite
    sync must still be an ordinary candidate for `run_tokens_update`'s
    single bundle walk, or the field would end up with no writer at all."""
    graph_dir = tmp_path / "graph"
    store = open_store(graph_dir / "code.db", create=True)
    try:
        store.set_current_repo("repo:acme/repo-a")
        with store.transaction() as tx:
            tx.upsert_records(
                GraphRecords(
                    nodes=(
                        GraphNode(
                            kind="repository",
                            name="repo-a",
                            path="",
                            line=None,
                            attrs={
                                "uri": "repo:acme/repo-a",
                                "owner": "acme",
                                "name": "repo-a",
                                "url": "",
                                "default_branch": "main",
                            },
                        ),
                        GraphNode(
                            kind="package",
                            name="widgets",
                            path="packages/widgets/pyproject.toml",
                            line=None,
                            attrs={"uri": "pkg:acme/repo-a/widgets", "language": "python", "version": "0.1.0"},
                        ),
                    ),
                    edges=(),
                )
            )
        store.set_current_repo(None)
    finally:
        store.close()

    bundle_root = tmp_path / ".works" / "okf"
    install_bundle(bundle_root, today=date(2026, 1, 1), dry_run=False)
    config = Config(
        graph_dir=graph_dir,
        declarations_dir=bundle_root,
        repos=(RepoConfig(name="repo-a", path=tmp_path / "repo-a", ignore=()),),
        state_gate=StateGateConfig(enabled=False, branches=()),
    )
    with open_reader(graph_dir=graph_dir) as reader:
        sync_bundle(
            bundle_root,
            config=config,
            reader=reader,
            today=date(2026, 1, 1),
            at=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        )

    pkg_page = bundle_root / "repositories/repo-a/packages/widgets.md"
    assert "tokens:" not in pkg_page.read_text(encoding="utf-8")

    layout = WorkspaceLayout(
        root=tmp_path / ".works",
        config_dir=tmp_path / ".works" / ".gw",
        cache_dir=tmp_path / ".works" / ".gw" / "cache",
        bundle_dir=bundle_root,
        worktrees_dir=tmp_path / ".works" / ".gw" / "worktrees",
    )

    update = run_tokens_update(layout, dry_run=False)

    assert "repositories/repo-a/packages/widgets" in [stamp.page for stamp in update.updated]
    assert "tokens: " in pkg_page.read_text(encoding="utf-8")
