"""Acceptance tests for `scripts/migrate_source_paths.py`.

Outside the package coverage gate on purpose: `scripts/` is repo tooling.
Run with `uv run pytest scripts/tests/test_migrate_source_paths.py`.
Every resolution branch is one live bucket from the C1 census.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from migrate_source_paths import Refused, apply, main, plan

DESIGN_SPEC = "01-" "design-spec.md"  # fmt: skip


def put(root: Path, member: str, data: str | bytes) -> Path:
    target = root / member
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return target


def source_page(value: str, *, newline: str = "\n", bom: bool = False) -> str:
    lines = [
        "---",
        "type: Source",
        "title: T",
        "description: D.",
        f"source_path: {value}",
        "origin: kept/as/is",
        "---",
        "",
        "# T",
        "",
    ]
    return ("﻿" if bom else "") + newline.join(lines)


def old_work(slug: str, *, archived: bool = False, day: str = "01") -> str:
    prefix = "work/_archive" if archived else "work"
    return f"{prefix}/2026-08-{day}-{slug}/{DESIGN_SPEC}"


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """`<tmp>/ws/okf`, inside a git repo rooted at `<tmp>/ws`."""
    ws = tmp_path / "ws"
    root = ws / "okf"
    root.mkdir(parents=True)
    put(root, "index.md", "---\nokf_version: 0.2\n---\n\n# b\n")
    return root


def git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True, text=True).stdout


def commit_raw_then_delete(ws: Path, member: str, data: bytes) -> str:
    git(ws, "init", "-q")
    git(ws, "config", "user.email", "t@t")
    git(ws, "config", "user.name", "t")
    git(ws, "config", "core.autocrlf", "false")
    put(ws, member, data)
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "add")
    (ws / member).unlink()
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "delete")
    return "HEAD^"


# --- the four resolution branches --------------------------------------------


def test_old_dated_work_layout_resolves_through_the_kind_prefixed_archive(
    bundle: Path,
) -> None:
    put(
        bundle,
        f"work/_archive/feature-epic-feature-foo/references/{DESIGN_SPEC}",
        "# spec\n",
    )
    put(
        bundle,
        "sources/2026-08-foo.md",
        source_page(old_work("foo", archived=True)),
    )
    planned, refused = plan(bundle, restore_from="HEAD")
    assert refused == []
    assert [(p.page, p.new, p.action) for p in planned] == [
        ("sources/2026-08-foo.md", "sources/references/2026-08-foo.md", "copy")
    ]


def test_newer_work_layout_finds_the_item_under_the_archive(bundle: Path) -> None:
    put(
        bundle,
        "work/_archive/epic-x/children/bug-bar/references/01-design.md",
        "# d\n",
    )
    put(
        bundle,
        "sources/2026-08-bar.md",
        source_page("work/bug-bar/references/01-design.md"),
    )
    planned, refused = plan(bundle, restore_from="HEAD")
    assert refused == []
    assert planned[0].source == (bundle / "work/_archive/epic-x/children/bug-bar/references/01-design.md")


def test_references_archive_subdirectory_is_searched(bundle: Path) -> None:
    put(
        bundle,
        f"work/_archive/tech-debt-baz/references/_archive/{DESIGN_SPEC}",
        "# d\n",
    )
    put(
        bundle,
        "sources/2026-08-baz.md",
        source_page(f"work/{'2026-07-02-baz'}/_archive/{DESIGN_SPEC}"),
    )
    planned, _ = plan(bundle, restore_from="HEAD")
    assert planned[0].source == (bundle / f"work/_archive/tech-debt-baz/references/_archive/{DESIGN_SPEC}")


def test_absolute_path_into_a_retired_vault_resolves_through_okf(bundle: Path) -> None:
    put(bundle, "sources/references/2026-08-design-q.md", "# q\n")
    put(
        bundle,
        "sources/2026-08-q.md",
        source_page("/Users/pat/old/graph-works/okf/sources/references/2026-08-design-q.md"),
    )
    planned, refused = plan(bundle, restore_from="HEAD")
    assert refused == []
    assert planned[0].source == bundle / "sources/references/2026-08-design-q.md"
    assert planned[0].new == "sources/references/2026-08-q.md"


def test_deleted_raw_material_is_restored_from_git(bundle: Path) -> None:
    rev = commit_raw_then_delete(bundle.parent, "raw/_archive/specs/SPEC.md", b"# spec bytes\r\n")
    put(
        bundle,
        "sources/2026-07-spec.md",
        source_page("raw/_archive/specs/SPEC.md"),
    )
    planned, refused = plan(bundle, restore_from=rev)
    assert refused == []
    assert planned[0].action == "restore"
    apply(bundle, planned)
    assert (bundle / "sources/references/2026-07-spec.md").read_bytes() == (b"# spec bytes\r\n")


# --- refusals, never guesses ---------------------------------------------------


def test_no_matching_item_is_refused(bundle: Path) -> None:
    put(
        bundle,
        "sources/2026-08-nope.md",
        source_page(old_work("nope")),
    )
    _, refused = plan(bundle, restore_from="HEAD")
    assert refused == [
        Refused(
            "sources/2026-08-nope.md",
            old_work("nope"),
            "no work item matches `nope`",
        )
    ]


def test_an_ambiguous_suffix_match_is_refused(bundle: Path) -> None:
    put(
        bundle,
        f"work/_archive/feature-dup/references/{DESIGN_SPEC}",
        "# a\n",
    )
    put(
        bundle,
        f"work/_archive/bug-dup/references/{DESIGN_SPEC}",
        "# b\n",
    )
    put(
        bundle,
        "sources/2026-08-dup.md",
        source_page(old_work("dup")),
    )
    _, refused = plan(bundle, restore_from="HEAD")
    assert len(refused) == 1
    assert refused[0].reason.startswith("2 work items match `dup`")


def test_a_raw_path_git_does_not_have_is_refused(bundle: Path) -> None:
    rev = commit_raw_then_delete(bundle.parent, "raw/other.md", b"x")
    put(
        bundle,
        "sources/2026-07-gone.md",
        source_page("raw/_archive/missing.md"),
    )
    _, refused = plan(bundle, restore_from=rev)
    assert refused[0].reason == f"nothing at `raw/_archive/missing.md` in {rev}"


def test_main_exits_non_zero_and_writes_nothing_on_any_refusal(
    bundle: Path,
) -> None:
    put(
        bundle,
        f"work/_archive/feature-ok/references/{DESIGN_SPEC}",
        "# ok\n",
    )
    put(
        bundle,
        "sources/2026-08-ok.md",
        source_page(old_work("ok")),
    )
    put(
        bundle,
        "sources/2026-08-nope.md",
        source_page(old_work("nope")),
    )
    before = (bundle / "sources/2026-08-ok.md").read_bytes()
    assert main([str(bundle), "--write", "--restore-from", "HEAD"]) == 1
    assert (bundle / "sources/2026-08-ok.md").read_bytes() == before
    assert not (bundle / "sources/references/2026-08-ok.md").exists()


# --- destinations -------------------------------------------------------------


def test_an_existing_destination_is_reused_not_overwritten_and_a_difference_is_noted(
    bundle: Path,
) -> None:
    put(
        bundle,
        f"work/_archive/feature-r/references/{DESIGN_SPEC}",
        "# newer\n",
    )
    put(bundle, "sources/references/2026-08-r.md", "# ingested earlier\n")
    put(
        bundle,
        "sources/2026-08-r.md",
        source_page(old_work("r")),
    )
    planned, _ = plan(bundle, restore_from="HEAD")
    assert planned[0].action == "reuse"
    assert planned[0].note == (f"destination differs from `work/_archive/feature-r/references/{DESIGN_SPEC}`")
    apply(bundle, planned)
    assert (bundle / "sources/references/2026-08-r.md").read_text(encoding="utf-8") == "# ingested earlier\n"


def test_a_page_inside_references_points_at_itself(bundle: Path) -> None:
    put(
        bundle,
        "sources/references/2026-08-design-s.md",
        source_page(old_work("s")),
    )
    planned, refused = plan(bundle, restore_from="HEAD")
    assert refused == []
    assert [(p.new, p.action) for p in planned] == [("sources/references/2026-08-design-s.md", "self")]


# --- the rewrite ---------------------------------------------------------------


@pytest.mark.parametrize(("newline", "bom"), [("\n", False), ("\r\n", False), ("\n", True)])
def test_only_the_source_path_line_changes(bundle: Path, newline: str, bom: bool) -> None:
    put(
        bundle,
        f"work/_archive/feature-e/references/{DESIGN_SPEC}",
        "# e\n",
    )
    original = source_page(old_work("e"), newline=newline, bom=bom)
    page = put(bundle, "sources/2026-08-e.md", original)
    apply(bundle, plan(bundle, restore_from="HEAD")[0])
    expected = original.replace(
        old_work("e"),
        "sources/references/2026-08-e.md",
    )
    assert page.read_bytes() == expected.encode("utf-8")


def test_copies_are_byte_exact(bundle: Path) -> None:
    put(
        bundle,
        f"work/_archive/feature-b/references/{DESIGN_SPEC}",
        b"\xef\xbb\xbf# b\r\n",
    )
    put(
        bundle,
        "sources/2026-08-b.md",
        source_page(old_work("b")),
    )
    apply(bundle, plan(bundle, restore_from="HEAD")[0])
    assert (bundle / "sources/references/2026-08-b.md").read_bytes() == (b"\xef\xbb\xbf# b\r\n")


def test_live_values_in_either_spelling_are_left_alone(bundle: Path) -> None:
    put(bundle, "sources/references/2026-08-l.md", "# l\n")
    put(
        bundle,
        "sources/2026-08-l.md",
        source_page("sources/references/2026-08-l.md"),
    )
    put(
        bundle,
        "sources/2026-08-m.md",
        source_page("/sources/references/2026-08-l.md"),
    )
    assert plan(bundle, restore_from="HEAD") == ([], [])


def test_dry_run_writes_nothing(bundle: Path, capsys: pytest.CaptureFixture[str]) -> None:
    put(
        bundle,
        f"work/_archive/feature-d/references/{DESIGN_SPEC}",
        "# d\n",
    )
    put(
        bundle,
        "sources/2026-08-d.md",
        source_page(old_work("d")),
    )
    before = {p: p.read_bytes() for p in bundle.rglob("*") if p.is_file()}
    assert main([str(bundle), "--restore-from", "HEAD"]) == 0
    assert {p: p.read_bytes() for p in bundle.rglob("*") if p.is_file()} == before
    out = capsys.readouterr().out
    assert (f"sources/2026-08-d.md: {old_work('d')} → sources/references/2026-08-d.md [copy]") in out


def test_a_second_run_is_a_no_op(bundle: Path) -> None:
    put(
        bundle,
        f"work/_archive/feature-i/references/{DESIGN_SPEC}",
        "# i\n",
    )
    put(
        bundle,
        "sources/2026-08-i.md",
        source_page(old_work("i")),
    )
    assert main([str(bundle), "--write", "--restore-from", "HEAD"]) == 0
    assert plan(bundle, restore_from="HEAD") == ([], [])
