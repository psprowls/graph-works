from __future__ import annotations

from pathlib import Path

from okf_ext.readindex import open_index, read, reconcile
from readindex_helpers import tree


def _index(tmp_path: Path):
    root = tree(
        tmp_path / "b",
        {
            "a/one.md": "---\ntype: Metric\ntitle: One\ntags: [x]\nphase: plan\n---\n# T\n",
            "a/two.md": "---\ntype: Table\n---\n",
            "another/three.md": "---\ntype: Metric\n---\n",
            "b.md": "---\nstatus: deprecated\n---\n",
        },
    )
    index = open_index(tmp_path / "i.db", root)
    reconcile(index)
    return index


def test_members_prefix_is_case_sensitive(tmp_path: Path) -> None:
    with _index(tmp_path) as index, read(index) as view:
        assert [r.id for r in view.members(prefix="a/")] == ["a/one.md", "a/two.md"]
        assert view.members(prefix="A/") == ()


def test_members_filter_by_type_and_kind(tmp_path: Path) -> None:
    with _index(tmp_path) as index, read(index) as view:
        assert [r.id for r in view.members(type="Metric")] == ["a/one.md", "another/three.md"]
        assert all(r.kind == "concept" for r in view.members(kind="concept"))


def test_member_row_carries_fm_tags_status(tmp_path: Path) -> None:
    with _index(tmp_path) as index, read(index) as view:
        one = view.member("a/one.md")
        assert one is not None and one.fm is not None
        assert one.fm["phase"] == "plan" and one.tags == ("x",) and one.status == "stable"
        assert view.member("b.md").status == "deprecated"  # type: ignore[union-attr]
        assert view.member("nope.md") is None


def test_view_pins_one_generation(tmp_path: Path) -> None:
    with _index(tmp_path) as index:
        with read(index) as view:
            before = view.generation
            (index.root / "a" / "two.md").write_bytes(b"---\ntype: Changed\n---\n")
            with open_index(tmp_path / "i.db", index.root) as other:
                reconcile(other)
            assert view.generation == before
            assert view.member("a/two.md").type == "Table"  # type: ignore[union-attr]
        with read(index) as fresh:
            assert fresh.generation == before + 1
            assert fresh.member("a/two.md").type == "Changed"  # type: ignore[union-attr]


def test_pruned_files_resolve_without_becoming_rows(tmp_path: Path) -> None:
    root = tree(
        tmp_path / "b",
        {"vendor/v.md": "# V\n", "source.md": "[v](vendor/v.md) [escape](../outside.md) [web](https://example.com)\n"},
    )
    with open_index(tmp_path / "i.db", root, prune=["vendor"]) as index:
        reconcile(index)
        with read(index) as view:
            assert view.resolve("vendor/v.md") == "vendor/v.md"
            assert view.member("vendor/v.md") is None
            assert view.backlinks("vendor/v") == ()
            assert len(view.broken("source")) == 1
            assert view.broken("source")[0].target is None
            assert view.broken("missing") == ()
            assert view.outlinks("missing") == ()
            assert view.headings("missing.md") == ()


def test_read_rolls_back_on_exception_and_rows_are_immutable(tmp_path: Path) -> None:
    import pytest

    with _index(tmp_path) as index:
        with pytest.raises(RuntimeError, match="stop"), read(index) as view:
            row = view.member("a/one.md")
            assert row is not None and row.fm is not None
            with pytest.raises(TypeError):
                row.fm["phase"] = "changed"
            raise RuntimeError("stop")
        assert not index.connection.in_transaction
        with read(index) as view:
            assert view.member("a/one.md").fm["phase"] == "plan"
