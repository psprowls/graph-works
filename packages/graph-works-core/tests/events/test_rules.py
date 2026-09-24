"""Table-driven lexical change classification tests."""

from __future__ import annotations

import os
from pathlib import Path, PurePath

import pytest
from graph_works_core import events
from graph_works_core.events import Change, ChangeEvent, EventKind, classify
from graph_works_core.workspace.layout import WorkspaceLayout, layout_for

W, D, P, L = EventKind.WORK_ITEM, EventKind.DECISIONS, EventKind.PAGE, EventKind.LOG


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    return layout_for(tmp_path / "ws")


BUNDLE_ROWS: list[tuple[str, tuple[EventKind, str] | None]] = [
    ("log.md", (L, "log.md")),
    ("work/foo.md", (W, "work/foo")),
    ("work/a/children/b.md", (W, "work/a/children/b")),
    ("work/a/children/b/children/c.md", (W, "work/a/children/b/children/c")),
    ("work/_archive/foo.md", (W, "work/_archive/foo")),
    ("work/a/children/_archive/b.md", (W, "work/a")),
    ("work/a/references/00-decisions.md", (D, "work/a")),
    ("work/a/references/01-design.md", (W, "work/a")),
    ("work/a/children/b/references/02-plan.md", (W, "work/a/children/b")),
    ("work/a/children/b/references/00-decisions.md", (D, "work/a/children/b")),
    ("work/a/references/orca-placement/gw-plan-x.json", (W, "work/a")),
    ("work/a/references/03-execute-transcript.jsonl", (W, "work/a")),
    ("work/a/references/nested/00-decisions.md", (W, "work/a")),
    ("work/a/children/index.md", (W, "work/a")),
    ("work/a/children/_archive/index.md", (W, "work/a")),
    ("work/a/children/index/references/note.txt", (W, "work/a")),
    ("work/_archive/foo/references/01-design.md", (W, "work/_archive/foo")),
    ("work/a/children/_archive/b/references/00-decisions.md", (W, "work/a")),
    ("index.md", (P, "index.md")),
    ("work/index.md", (P, "work/index.md")),
    ("work/_archive/index.md", (P, "work/_archive/index.md")),
    ("work/Not_An_Item.md", (P, "work/Not_An_Item.md")),
    ("proposals/p-1.md", (P, "proposals/p-1.md")),
    ("adrs/0001-x.md", (P, "adrs/0001-x.md")),
    ("docs/explanations/deep/topic.md", (P, "docs/explanations/deep/topic.md")),
    (".DS_Store", None),
    ("work/.foo.md.swp", None),
    ("work/a/references/.hidden/00-decisions.md", None),
    (".obsidian/workspace.md", None),
    ("work/foo.md.lock", None),
    ("index.lock", None),
    ("work-index.json", None),
    ("assets/diagram.png", None),
    ("work/foo/notes.txt", None),
    ("work/a", None),
]


@pytest.mark.parametrize("change", list(Change))
@pytest.mark.parametrize(("member", "expected"), BUNDLE_ROWS)
def test_bundle_rules(
    layout: WorkspaceLayout, member: str, expected: tuple[EventKind, str] | None, change: Change
) -> None:
    event = classify(layout, layout.bundle_dir / member, change)
    if expected is None:
        assert event is None
    else:
        kind, identity = expected
        assert event == ChangeEvent(kind=kind, path=identity, member=member, change=change)


def test_identities_are_posix_strings(layout: WorkspaceLayout) -> None:
    event = classify(
        layout,
        layout.bundle_dir / "work" / "a" / "children" / "b" / "references" / "02-plan.md",
        Change.ADDED,
    )
    assert event is not None
    assert event.path == "work/a/children/b"
    assert event.member == "work/a/children/b/references/02-plan.md"
    assert "\\" not in event.path and "\\" not in (event.member or "")


@pytest.mark.skipif(os.name == "nt", reason="Windows treats backslash as a path separator")
def test_page_with_literal_backslash_is_not_a_portable_identity(layout: WorkspaceLayout) -> None:
    assert classify(layout, layout.bundle_dir / r"notes\draft.md", Change.ADDED) is None


@pytest.mark.skipif(os.name == "nt", reason="Windows treats backslash as a path separator")
def test_owned_artifact_with_literal_backslash_is_not_a_portable_identity(layout: WorkspaceLayout) -> None:
    path = layout.bundle_dir / "work" / "a" / "references" / r"notes\draft.txt"
    assert classify(layout, path, Change.ADDED) is None


@pytest.mark.parametrize("change", list(Change))
def test_config_files(layout: WorkspaceLayout, change: Change) -> None:
    rows = {
        layout.cache_dir / "config.json": "projection",
        layout.manifest_path: "manifest",
        layout.local_manifest_path: "manifest-local",
    }
    for path, token in rows.items():
        assert classify(layout, path, change) == ChangeEvent(EventKind.CONFIG, token, None, change)


def test_relocated_control_plane_outside_the_root(tmp_path: Path) -> None:
    elsewhere = (tmp_path / "elsewhere").resolve()
    layout = layout_for(tmp_path / "ws", config_dir=str(elsewhere / "gw"), cache_dir=str(elsewhere / "cache"))
    assert classify(layout, elsewhere / "cache" / "config.json", Change.MODIFIED) == ChangeEvent(
        EventKind.CONFIG, "projection", None, Change.MODIFIED
    )
    assert classify(layout, layout.root / ".gw" / "cache" / "config.json", Change.MODIFIED) is None
    assert classify(layout, layout.bundle_dir / "work" / "foo.md", Change.ADDED) == ChangeEvent(
        W, "work/foo", "work/foo.md", Change.ADDED
    )


def test_relocated_bundle(tmp_path: Path) -> None:
    layout = layout_for(tmp_path / "ws", bundle_dir="kb")
    assert classify(layout, layout.root / "kb" / "log.md", Change.ADDED) == ChangeEvent(
        L, "log.md", "log.md", Change.ADDED
    )
    assert classify(layout, layout.root / "okf" / "log.md", Change.ADDED) is None


def test_dispatch_documents_only_when_passed(layout: WorkspaceLayout) -> None:
    shared = layout.config_dir / "rules" / "custom-dispatch.yaml"
    local = layout.root / "dispatch.local.yaml"
    documents = (shared, local)
    assert classify(layout, shared, Change.MODIFIED, dispatch_documents=documents) == ChangeEvent(
        EventKind.CONFIG, "dispatch", None, Change.MODIFIED
    )
    assert classify(layout, local, Change.DELETED, dispatch_documents=documents) == ChangeEvent(
        EventKind.CONFIG, "dispatch-local", None, Change.DELETED
    )
    assert classify(layout, shared, Change.MODIFIED) is None
    assert classify(layout, local, Change.MODIFIED) is None


def test_dispatch_documents_do_not_shadow_the_fixed_config_files(layout: WorkspaceLayout) -> None:
    documents = (layout.manifest_path, layout.root / "d.local.yaml")
    assert classify(layout, layout.manifest_path, Change.MODIFIED, dispatch_documents=documents) == ChangeEvent(
        EventKind.CONFIG, "manifest", None, Change.MODIFIED
    )


@pytest.mark.parametrize("path", [PurePath("okf/log.md"), PurePath("log.md"), PurePath("/somewhere/else/okf/log.md")])
def test_none_outside_every_root(layout: WorkspaceLayout, path: PurePath) -> None:
    assert classify(layout, path, Change.ADDED) is None


def test_bundle_dir_itself_and_workspace_root_files(layout: WorkspaceLayout) -> None:
    assert classify(layout, layout.bundle_dir, Change.DELETED) is None
    assert classify(layout, layout.root / "README.md", Change.ADDED) is None


def test_package_exports() -> None:
    assert sorted(events.__all__) == ["Change", "ChangeEvent", "EventKind", "classify"]
