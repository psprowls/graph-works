"""Partial mutations retain their admitted corpus and render from parsed inputs."""

from __future__ import annotations

from pathlib import Path

import pytest
from okf_io import Document, load_bundle
from work_helpers import write_item
from work_tracker_okf import load_items
from work_tracker_okf.reparent import plan_reparent

SOURCE = "work/epic/children/source"
DESTINATION = "work/parent/children/source"


def _tree(root: Path) -> None:
    for path, kind in (
        ("work/epic", "Epic"),
        ("work/parent", "Epic"),
        (SOURCE, "Feature"),
        ("work/epic/children/sibling", "Feature"),
    ):
        write_item(root, path, f"type: {kind}\nwork_status: open\n")


def _parses(monkeypatch: pytest.MonkeyPatch) -> list[Path | None]:
    parsed: list[Path | None] = []
    original = Document.parse.__func__

    def record(cls: type[Document], text: str, *, path: Path | None = None) -> Document:
        parsed.append(path)
        return original(cls, text, path=path)

    monkeypatch.setattr(Document, "parse", classmethod(record))
    return parsed


@pytest.mark.parametrize("filename", ["01-design.md", "index.md", "log.md"])
def test_partial_plan_does_not_promote_unloaded_context_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    _tree(tmp_path)
    selected = f"{SOURCE}/references/{filename}"
    write_item(
        tmp_path,
        SOURCE,
        f"type: Feature\nwork_status: open\nsources:\n  - id: context\n    resource: /{selected}\n",
    )
    for owner in (SOURCE, "work/epic", "work/parent", "work/epic/children/sibling"):
        reference = tmp_path / owner / "references" / filename
        reference.parent.mkdir(parents=True)
        reference.write_text("Context without move references.\n", encoding="utf-8")
        if owner != SOURCE:
            write_item(
                tmp_path,
                owner,
                f"type: {'Feature' if '/children/' in owner else 'Epic'}\nwork_status: open\n"
                f"sources:\n  - id: context\n    resource: /{owner}/references/{filename}\n",
            )
    full = load_bundle(tmp_path)
    full_plan = plan_reparent(full, load_items(full), SOURCE, "work/parent")
    partial = load_bundle(
        tmp_path,
        only={
            "work/epic.md",
            "work/parent.md",
            f"{SOURCE}.md",
            "work/epic/children/sibling.md",
            selected,
        },
    )
    assert len(partial.unloaded) == 3
    parsed = _parses(monkeypatch)

    partial_plan = plan_reparent(partial, load_items(partial), SOURCE, "work/parent")

    assert partial_plan.ok, partial_plan.refusals
    assert partial_plan == full_plan
    assert parsed == []
    assert partial.unloaded == {
        f"{owner}/references/{filename}" for owner in ("work/epic", "work/parent", "work/epic/children/sibling")
    }
    assert partial_plan.move_plan is not None
    assert selected in {move.source for move in partial_plan.move_plan.moves}


@pytest.mark.parametrize("source_kind", ["none", "extant", "missing", "mixed"])
def test_moved_item_uses_original_document_for_exact_source_rebase_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_kind: str,
) -> None:
    _tree(tmp_path)
    sources = ""
    if source_kind in {"extant", "mixed"}:
        member = f"{SOURCE}/references/01-design.md"
        (tmp_path / member).parent.mkdir(parents=True)
        (tmp_path / member).write_text("Design\n", encoding="utf-8")
        sources += f"  - id: design\n    resource: '/{member}' # preserve comment\n"
    if source_kind in {"missing", "mixed"}:
        sources += f"  - id: absent\n    resource: /{SOURCE}/references/missing.md\n"
    if source_kind == "mixed":
        sources += "  - id: relative\n    resource: references/unwritten.md\n"
        sources += "  - id: external\n    resource: https://example.com/context\n"
    write_item(
        tmp_path,
        SOURCE,
        "type: Feature\nwork_status: open\n" + (f"sources:\n{sources}" if sources else ""),
        body="See [sibling](sibling.md).\n",
    )
    before = (tmp_path / f"{SOURCE}.md").read_bytes()
    expected = before.replace(b"[sibling](sibling.md)", b"[sibling](../../epic/children/sibling.md)")
    expected = expected.replace(
        b"/work/epic/children/source/references/",
        b"/work/parent/children/source/references/",
    )
    bundle = load_bundle(tmp_path)
    original_doc = bundle.concepts[SOURCE]
    parsed = _parses(monkeypatch)

    plan = plan_reparent(bundle, load_items(bundle), SOURCE, "work/parent")

    assert plan.ok, plan.refusals
    assert {write.member: write.after for write in plan.writes}[f"{DESTINATION}.md"] == expected
    assert parsed == []
    assert original_doc.raw_text.encode("utf-8") == before
    assert not original_doc._dirty
    if source_kind in {"missing", "mixed"}:
        assert plan.move_plan is not None
        assert not any(edit.old.endswith("/references/missing.md") for edit in plan.move_plan.edits)
    if source_kind == "mixed":
        assert plan.move_plan is not None
        assert "references/unwritten.md" in {entry.raw for entry in plan.move_plan.unrebased}
