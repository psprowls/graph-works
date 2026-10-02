"""member_row() is the projection the index stores (D-005)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from ext_helpers import ACME_RETAIL, GA4
from okf_ext.readindex import member_row, open_index, read, reconcile
from okf_ext.readindex.project import fm_json
from okf_io import load_bundle
from readindex_helpers import tree

OKF_IO_FIXTURES = ACME_RETAIL.parents[1]


def _directory_file(directory_id: str, name: str) -> str:
    return f"{directory_id}/{name}" if directory_id else name


@pytest.mark.parametrize("root", [ACME_RETAIL, GA4, OKF_IO_FIXTURES / "edge", OKF_IO_FIXTURES / "nonconformant"])
def test_every_view_row_equals_member_row(root: Path, tmp_path: Path) -> None:
    bundle = load_bundle(root)
    expected = {f"{cid}.md": member_row(f"{cid}.md", "concept", doc) for cid, doc in bundle.concepts.items()}
    expected |= {
        _directory_file(d, "index.md"): member_row(_directory_file(d, "index.md"), "index", doc)
        for d, doc in bundle.indexes.items()
    }
    expected |= {
        _directory_file(d, "log.md"): member_row(_directory_file(d, "log.md"), "log", doc)
        for d, doc in bundle.logs.items()
    }
    expected |= {a: member_row(a, "asset") for a in bundle.assets}
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        with read(index) as view:
            got = {row.id: row for row in view.members()}
    assert {k: replace(v, sha256=None) for k, v in got.items()} == expected
    assert all(row.sha256 for row in got.values() if row.kind in ("concept", "index", "log"))


@pytest.mark.parametrize("kind", ["asset", "ignored"])
def test_member_row_without_a_document_is_the_ignored_shape(kind) -> None:
    row = member_row("schema/x.md", kind, sha256="supplied")
    assert (row.id, row.kind, row.sha256) == ("schema/x.md", kind, "supplied")
    assert (row.fm, row.fm_exact, row.tags, row.type, row.title, row.status, row.parse_error) == (
        None,
        True,
        (),
        None,
        None,
        None,
        None,
    )
    assert row.coercion_failures == frozenset()


@pytest.mark.parametrize(
    "yaml, expected, exact",
    [
        (".nan", "nan", False),
        (".inf", "inf", False),
        ("-.inf", "-inf", False),
        ("1.25", 1.25, True),
        ("!custom value", None, False),
    ],
)
def test_yaml_values_preserve_projection_and_exactness(tmp_path: Path, yaml, expected, exact) -> None:
    root = tree(tmp_path / "b", {"a.md": f"---\ntitle: Café\ntags: [z, a, z]\nodd: {yaml}\n---\n"})
    doc = load_bundle(root).concepts["a"]
    text, actual_exact = fm_json(doc)
    data = json.loads(text, parse_constant=lambda value: pytest.fail(f"nonfinite JSON: {value}"))
    assert actual_exact is exact
    assert data["title"] == "Café"
    if expected is not None:
        assert data["odd"] == expected
    else:
        assert isinstance(data["odd"], str) and "value" in data["odd"]
    row = member_row("a.md", "concept", doc, sha256="supplied")
    assert row.sha256 == "supplied" and row.tags == ("a", "z")
    assert row.fm_exact is exact and dict(row.fm) == data
    with pytest.raises(TypeError):
        row.fm["title"] = "changed"
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        with read(index) as view:
            assert replace(view.member("a.md"), sha256="supplied") == row


def test_recursive_yaml_fallback_preserves_fields_and_excludes_body(tmp_path: Path) -> None:
    root = tree(tmp_path / "b", {"a.md": "---\n&root\ntitle: A\nodd: *root\n---\n# Private body\n"})
    doc = load_bundle(root).concepts["a"]
    text, exact = fm_json(doc)
    data = json.loads(text)
    assert exact is False and data["title"] == "A"
    assert isinstance(data["odd"], str) and "recursive" in data["odd"]
    assert "Private body" not in text
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        with read(index) as view:
            assert replace(view.member("a.md"), sha256=None) == member_row("a.md", "concept", doc)
