from __future__ import annotations

from pathlib import Path

from okf_io import load_bundle, parse
from repositories_okf.changelog import (
    ChangelogEntry,
    changelog_path,
    entries_from_bundle,
    entry_for,
    reconcile_changelog,
)
from repositories_okf.snapshots import Snapshot, render_snapshot

E1 = ChangelogEntry("repositories/demo/snapshots/2026-09-01-aaaaaaa.md", "2026-09-01", "v1.0.0", "baseline")
E2 = ChangelogEntry("repositories/demo/snapshots/2026-09-10-bbbbbbb.md", "2026-09-10", "v1.1.0", "3 commits, 2 files")
E3 = ChangelogEntry(
    "repositories/demo/snapshots/2026-09-20-ccccccc.md", "2026-09-20", "ccccccc", "1 commits, 1 files, rewritten"
)


def _body_lines(text: str) -> list[str]:
    return [line for line in parse(text).body.splitlines() if line.startswith("- ")]


def test_path_and_line() -> None:
    assert changelog_path("demo") == "repositories/demo/changelog.md"
    assert E2.line == "- [2026-09-10 — v1.1.0](/repositories/demo/snapshots/2026-09-10-bbbbbbb.md) — 3 commits, 2 files"


def test_a_new_changelog_lists_entries_newest_first() -> None:
    text = reconcile_changelog(None, name="demo", entries=[E1, E3, E2])
    data = parse(text).fm_data(dates="iso")
    assert (data["type"], data["repository"]) == ("RepositoryChangelog", "/repositories/demo.md")
    assert _body_lines(text) == [E3.line, E2.line, E1.line]
    assert "## Entries" in text


def test_edited_text_is_kept_new_entries_inserted_and_dead_ones_pruned() -> None:
    before = reconcile_changelog(None, name="demo", entries=[E1, E2])
    edited = before.replace(
        E2.line, "- [2026-09-10 — v1.1.0](/repositories/demo/snapshots/2026-09-10-bbbbbbb.md) — the auth rewrite landed"
    )
    edited = edited.replace("## Entries\n", "## Entries\n\nHand-written intro.\n")
    after = reconcile_changelog(edited, name="demo", entries=[E2, E3])
    assert _body_lines(after) == [
        E3.line,
        "- [2026-09-10 — v1.1.0](/repositories/demo/snapshots/2026-09-10-bbbbbbb.md) — the auth rewrite landed",
    ]
    assert "Hand-written intro." in after
    assert after.split("\n---\n", 1)[0] == edited.split("\n---\n", 1)[0]  # frontmatter byte-identical


def test_reconciling_an_unchanged_set_is_a_byte_identical_no_op() -> None:
    before = reconcile_changelog(None, name="demo", entries=[E1, E2])
    assert reconcile_changelog(before, name="demo", entries=[E2, E1]) == before


def test_crlf_is_preserved() -> None:
    before = reconcile_changelog(None, name="demo", entries=[E1]).replace("\n", "\r\n")
    after = reconcile_changelog(before, name="demo", entries=[E1, E2])
    assert "\r\n" in after and "\n" not in after.replace("\r\n", "")
    assert E2.line + "\r\n" in after


def test_a_changelog_without_the_heading_gains_one_at_the_end() -> None:
    before = (
        "---\ntype: RepositoryChangelog\ntitle: t\ndescription: d\nrepository: /repositories/demo.md\n---\n\nIntro.\n"
    )
    after = reconcile_changelog(before, name="demo", entries=[E1])
    assert after.startswith(before)
    assert after.endswith(f"## Entries\n\n{E1.line}\n")


def test_links_to_other_repositories_are_prose_not_entries() -> None:
    foreign = "- [x](/repositories/other/snapshots/2026-01-01-ddddddd.md) — keep me"
    before = reconcile_changelog(None, name="demo", entries=[E1]).replace(E1.line, f"{E1.line}\n{foreign}")
    after = reconcile_changelog(before, name="demo", entries=[E1, E2])
    assert foreign in after


def test_entries_come_from_snapshot_pages_in_the_bundle(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    snapshot = Snapshot(name="demo", commit="c" * 40, fetched_at="2026-09-20T01:00:00Z", describe="v2")
    (root / snapshot.path).parent.mkdir(parents=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# b\n", encoding="utf-8", newline="")
    (root / snapshot.path).write_text(render_snapshot(snapshot), encoding="utf-8", newline="")
    (root / "repositories" / "demo" / "snapshots" / "notes.md").write_text(
        "---\ntype: Note\ntitle: n\n---\n\nx\n", encoding="utf-8", newline=""
    )
    assert entries_from_bundle(load_bundle(root), "demo") == (entry_for(snapshot),)
    assert entry_for(snapshot) == ChangelogEntry(snapshot.path, "2026-09-20", "v2", "baseline")
