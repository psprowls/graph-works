from __future__ import annotations

from pathlib import Path

import pytest
from ext_helpers import ACME_RETAIL, GA4
from okf_ext.readindex import open_index, read, reconcile
from readindex_helpers import age, assert_equivalent, tree

OKF_IO_FIXTURES = ACME_RETAIL.parents[1]


def _hostile(root: Path) -> Path:
    tree(
        root,
        {
            "ok.md": "---\ntitle: OK\ntags: [a, b]\n---\n"
            "[x](bad.md) [y](café.md) [z](schema/s.md) [w](vendor/v.md) [n](missing.md) ![i](p.png)\n# H\n",
            "yaml.md": "---\ntitle: [unclosed\n---\n",
            "unterminated.md": "---\ntitle: x\n",
            "café.md": "---\ntitle: NFD\n---\n[back](ok.md)\n",
            "schema/s.md": "x",
            "vendor/v.md": "x",
            "p.png": "png",
            "sub/index.md": "---\ntitle: Index\nstatus: deprecated\ntags: [z, a]\n---\n# Index\n[ok](../ok.md)\n",
            "sub/log.md": "---\ntitle: Log\n---\n## 2026-01-01\n",
        },
    )
    (root / "bad.md").write_bytes(b"\xff\xfe\x00bad")
    age(root)
    return root


CASES = [
    pytest.param(ACME_RETAIL, (), (), id="acme_retail"),
    pytest.param(GA4, (), (), id="ga4"),
    pytest.param(OKF_IO_FIXTURES / "edge", (), (), id="edge"),
    pytest.param(OKF_IO_FIXTURES / "nonconformant", (), (), id="nonconformant"),
]


@pytest.mark.parametrize(("root", "ignore", "prune"), CASES)
def test_fixture_bundles_are_equivalent(root: Path, ignore, prune, tmp_path: Path) -> None:
    assert_equivalent(root, tmp_path / "i.db", ignore=ignore, prune=prune)


def test_hostile_bundle_is_equivalent(tmp_path: Path) -> None:
    root = _hostile(tmp_path / "b")
    assert_equivalent(root, tmp_path / "i.db", ignore=["schema/*"], prune=["vendor"])


def test_link_to_undecodable_file_is_broken(tmp_path: Path) -> None:
    root = _hostile(tmp_path / "b")
    with open_index(tmp_path / "i.db", root) as index:
        reconcile(index)
        with read(index) as view:
            assert any(link.target == "bad.md" for link in view.broken("ok"))


@pytest.mark.parametrize("reverse", [False, True])
def test_nfc_collision_last_walk_winner_and_exact_links(tmp_path: Path, monkeypatch, reverse: bool) -> None:
    # APFS cannot store these three canonically equivalent names together.
    # Control only the file/walk boundary; both readers derive real documents,
    # canonical maps, diagnostics and link judgments from the same ordered walk.
    import okf_io.bundle as bundle_module
    from okf_ext.readindex import sync
    from okf_io import Document
    from okf_io.bundle import Member, MemberStat, Walk

    root = tree(tmp_path / "b", {"source.md": "[exact](Å.md) [alias](Å.md) [missing](no.md) ![image](Å.md)\n"})
    names = ["Å.md", "A\u030a.md"]
    if reverse:
        names.reverse()
    data = {name: b"---\ntitle: Collision\n---\n# Heading\n" for name in names}
    data["source.md"] = (root / "source.md").read_bytes()
    walked = Walk(
        tuple(Member(name, "concept", MemberStat(len(data[name]), 1, 1, 1)) for name in ["source.md", *names]),
        {"locked": "denied"},
        frozenset(),
    )
    monkeypatch.setattr(bundle_module, "walk", lambda *a, **kw: walked)
    monkeypatch.setattr(sync, "walk", lambda *a, **kw: walked)

    def read_document(root, name):
        return Document.parse(data[name].decode("utf-8"), path=root / name)

    monkeypatch.setattr(bundle_module, "read_member", read_document)
    monkeypatch.setattr(sync, "read_member", read_document)
    monkeypatch.setattr(sync, "_read_bytes", lambda root, name: data[name])
    monkeypatch.setattr(sync, "_stable", lambda root, member: True)
    assert_equivalent(root, tmp_path / "i.db")
    with open_index(tmp_path / "i.db", root) as index, read(index) as view:
        assert dict(view.diagnostics().collisions) == {"Å.md": tuple(names)}
        assert view.resolve("Å.md") == names[-1]
        assert view.resolve("Å.md") == "Å.md"
        assert view.backlinks(names[-1][:-3]) == ("source",)
        assert view.backlinks(names[0][:-3]) == (("source",) if names[0] == "Å.md" else ())
        assert [link.target for link in view.broken()] == ["no.md"]
