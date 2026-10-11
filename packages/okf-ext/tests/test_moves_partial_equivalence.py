"""A partial load must preserve exact move plans and rendered filesystem effects.

The linked corpus covers the locator/roundtrip shapes; the purpose-built cases
cover work-shaped subtrees, opaque/reserved members, dependencies and refusals.
"""

from __future__ import annotations

import unicodedata

import pytest
from ext_helpers import LINKED_MEMBERS, linked_bad_copy, linked_copy, write_tree
from okf_ext.moves import ReferenceField, materialize, plan_move_many, plan_repair, scan_touched
from okf_io import load_bundle
from test_moves_roundtrip import MOVES
from test_moves_touched import ESCAPED_REFERRERS, REFERRERS, SRC

DEPENDENCIES = (ReferenceField(("depends_on", "*", "path"), target="concept"),)


def _equivalent(root, mapping, *, sources=None, ignore=(), opaque=frozenset(), fields=(), repair=False):
    full_bundle = load_bundle(root, ignore=ignore)
    stems = sources if sources is not None else [source.removesuffix(".md") for source in mapping]
    scan = scan_touched(root, stems, ignore=ignore)
    assert scan.complete
    partial_bundle = load_bundle(root, ignore=ignore, only=scan.members, inventory=scan.inventory)
    if repair:
        full = plan_repair(full_bundle, mapping)
        partial = plan_repair(partial_bundle, mapping)
    else:
        full = plan_move_many(full_bundle, mapping, opaque_members=opaque, extra_reference_fields=fields)
        partial = plan_move_many(partial_bundle, mapping, opaque_members=opaque, extra_reference_fields=fields)
    # Dataclass equality includes root, moves, edits, refusals, digests,
    # unrebased, relocate and stranded; never relax it to just the edit count.
    assert partial == full
    if full.ok:
        assert materialize(partial_bundle, partial) == materialize(full_bundle, full)
    else:
        with pytest.raises(ValueError, match="refusal"):
            materialize(partial_bundle, partial)
        with pytest.raises(ValueError, match="refusal"):
            materialize(full_bundle, full)
    return full


CORPUS_MOVES = [
    *MOVES,
    ("concepts/café.md", "pages/café.md"),
    ("concepts/crlf.md", "nested/pages/crlf.md"),
    ("concepts/spaced name.md", "pages/spaced name.md"),
    ("concepts/frag.md", "pages/frag.md"),
    ("concepts/multi.md", "pages/multi.md"),
    ("concepts/encoded.md", "pages/encoded.md"),
    ("concepts/tabled.md", "pages/tabled.md"),
    ("concepts/decoy.md", "pages/decoy.md"),
]


@pytest.mark.parametrize("source,dest", CORPUS_MOVES)
def test_linked_corpus_partial_plan_and_bytes_equal_full(tmp_path, source, dest):
    assert _equivalent(linked_copy(tmp_path), {source: dest}).ok


def test_linked_directory_batch_preserves_interdependent_references(tmp_path):
    mapping = {
        member: member.replace("concepts/", "deep/pages/", 1)
        for member in LINKED_MEMBERS
        if member.startswith("concepts/")
    }
    assert _equivalent(linked_copy(tmp_path), mapping, sources=["concepts"]).ok


@pytest.mark.parametrize(
    "mapping,kind",
    [
        ({"concepts/alpha.md": "concepts/beta.md"}, "dest-exists"),
        ({"concepts/alpha.md": "pages/shared.md", "concepts/beta.md": "pages/shared.md"}, "dest-exists"),
        ({"index.md": "pages/i.md"}, "reserved-source"),
        ({"log.md": "pages/l.md"}, "reserved-source"),
        ({"concepts/alpha.md": "pages/index.md"}, "reserved-dest"),
        ({"concepts/beta.md": "pages/log.md"}, "reserved-dest"),
        ({"absent.md": "pages/absent.md"}, "not-a-member"),
        ({"concepts/alpha.md": "../escaped.md"}, "escapes-root"),
        ({"concepts/alpha.md": "concepts/alpha.md"}, "same-path"),
        ({"concepts/alpha.md": "img/alpha.png"}, "kind-change"),
    ],
)
def test_mapping_refusals_equal_full(tmp_path, mapping, kind):
    plan = _equivalent(linked_copy(tmp_path), mapping)
    assert kind in {refusal.kind for refusal in plan.refusals}


@pytest.mark.parametrize("source", ["target.md", "broken.md", "refstyle.md"])
def test_linked_bad_corpus_preserves_fail_closed_refusals(tmp_path, source):
    plan = _equivalent(linked_bad_copy(tmp_path), {source: f"moved/{source}"})
    # Moving the referrer itself is permitted by the existing full planner;
    # moving its target must preserve every fail-closed inbound refusal.
    assert plan.ok == (source == "refstyle.md")
    if source == "target.md":
        assert {"parse-error", "reference-definition", "unlocatable-reference"} <= {r.kind for r in plan.refusals}


def _subtree(tmp_path, referrer, *, extra=None):
    files = {
        f"{SRC}.md": "---\ntitle: X\n---\n[design](./bug-x/references/01-design.md)\n[missing](./missing.md)\n",
        f"{SRC}/references/01-design.md": "# Design\n[owner](../../bug-x.md)\n",
        f"{SRC}/references/img.png": "opaque asset\n",
        "work/e/children/index.md": "# Children\n[x](./bug-x.md)\n",
        "work/log.md": f"# Log\n[x](/{SRC}.md)\n",
        "work/ref/r.md": referrer,
        "work/unrelated.md": "---\ntitle: Unrelated\n---\n# Nothing\n",
    }
    files.update(extra or {})
    root = write_tree(tmp_path / "b", files)
    mapping = {path: path.replace(SRC, "work/new/children/bug-x", 1) for path in files if path.startswith(SRC)}
    return root, mapping


@pytest.mark.parametrize("name,text", REFERRERS)
def test_each_referrer_form_preserves_exact_plan_and_writes(tmp_path, name, text):
    root, mapping = _subtree(tmp_path, text)
    plan = _equivalent(root, mapping, sources=[SRC], fields=DEPENDENCIES)
    if name == "reference-definition":
        assert {"reference-definition", "unlocatable-reference"} <= {r.kind for r in plan.refusals}
    elif name == "parse-error":
        assert "parse-error" in {r.kind for r in plan.refusals}
    elif name == "wikilink":
        assert any(entry.member == "work/ref/r.md" for entry in plan.stranded)
    else:
        assert plan.ok, plan.refusals
        assert any(edit.member == "work/ref/r.md" for edit in plan.edits)


@pytest.mark.parametrize("name,text,refusal", ESCAPED_REFERRERS, ids=[case[0] for case in ESCAPED_REFERRERS])
def test_grammar_escapes_preserve_full_refusals_or_exact_rewrites(tmp_path, name, text, refusal):
    root, mapping = _subtree(tmp_path, text)
    plan = _equivalent(root, mapping, sources=[SRC], fields=DEPENDENCIES)
    if refusal:
        assert not plan.ok
        assert any(r.path == "work/ref/r.md" and r.kind == refusal for r in plan.refusals)
    else:
        assert plan.ok, plan.refusals
        assert any(edit.member == "work/ref/r.md" for edit in plan.edits)


@pytest.mark.parametrize(
    "scalar", ['"/work/bug\n  x.md"', "'/work/bug\n  x.md'", ">-\n  /work/bug\n  x.md", "/work/bug\n  x.md"]
)
def test_folded_or_wrapped_yaml_scalar_preserves_full_plan_and_bytes(tmp_path, scalar):
    root = write_tree(
        tmp_path / "b",
        {
            "work/bug x.md": "---\ntitle: X\n---\n",
            "r.md": f"---\ntitle: R\nresource: {scalar}\n---\n",
        },
    )
    plan = _equivalent(root, {"work/bug x.md": "work/moved.md"})
    assert plan.ok
    assert any(edit.member == "r.md" for edit in plan.edits)


def test_yaml_doubled_single_quote_preserves_full_plan_and_bytes(tmp_path):
    root = write_tree(
        tmp_path / "b",
        {
            "work/bug'x.md": "---\ntitle: X\n---\n",
            "r.md": "---\ntitle: R\nresource: '/work/bug''x.md'\n---\n",
        },
    )
    plan = _equivalent(root, {"work/bug'x.md": "work/moved.md"})
    assert plan.ok
    assert any(edit.member == "r.md" for edit in plan.edits)


@pytest.mark.parametrize(
    "key", ["resource", "computation", "executor.resource", "attester.resource", "sources.0.resource"]
)
def test_standard_frontmatter_reference_fields_equal_full(tmp_path, key):
    value = f"/{SRC}.md"
    nested = {
        "resource": f"resource: {value}\n",
        "computation": f"computation: {value}\n",
        "executor.resource": f"executor:\n  resource: {value}\n",
        "attester.resource": f"attester:\n  resource: {value}\n",
        "sources.0.resource": f"sources:\n  - id: source\n    resource: {value}\n",
    }
    root, mapping = _subtree(tmp_path, "---\ntitle: R\n" + nested[key] + "---\n# R\n")
    plan = _equivalent(root, mapping, sources=[SRC])
    assert plan.ok
    assert any(edit.member == "work/ref/r.md" and edit.key == key for edit in plan.edits)


@pytest.mark.parametrize("ignore", [(), ("*/references/*",)])
def test_opaque_subtree_and_asset_effects_equal_full(tmp_path, ignore):
    root, mapping = _subtree(tmp_path, f"[x](/{SRC}.md)\n")
    opaque = frozenset({f"{SRC}/references/01-design.md"})
    plan = _equivalent(root, mapping, sources=[SRC], ignore=ignore, opaque=opaque)
    assert plan.ok
    assert any(move.opaque for move in plan.moves)
    assert any(move.is_asset for move in plan.moves)


def test_reserved_subtree_members_preserve_the_full_refusal(tmp_path):
    root, mapping = _subtree(tmp_path, "# R\n", extra={f"{SRC}/index.md": "# I\n", f"{SRC}/log.md": "# L\n"})
    plan = _equivalent(root, mapping, sources=[SRC])
    assert {r.path for r in plan.refusals if r.kind == "reserved-source"} == {f"{SRC}/index.md", f"{SRC}/log.md"}


@pytest.mark.parametrize("source_form", ["NFC", "NFD"])
@pytest.mark.parametrize("spelling", ["NFC", "NFD", "percent"])
def test_unicode_source_subtree_and_referrer_forms_equal_full(tmp_path, source_form, spelling):
    raw_source = unicodedata.normalize("NFD", "work/e/children/bug-café")
    source = unicodedata.normalize(source_form, raw_source)
    href = unicodedata.normalize(spelling if spelling != "percent" else "NFC", raw_source)
    if spelling == "percent":
        href = href.replace("é", "%C3%A9")
    root = write_tree(
        tmp_path / "b",
        {
            f"{raw_source}.md": "---\ntitle: Café\n---\n[out](../../../elsewhere.md)\n",
            f"{raw_source}/references/design.md": "# Design\n",
            "work/ref/r.md": f"[x](/{href}.md)\n",
            "work/réf.md": f"[[{href}]]\n",
            "elsewhere.md": "---\ntitle: Elsewhere\n---\n",
        },
    )
    mapping = {
        f"{source}.md": "work/new/children/bug-café.md",
        f"{source}/references/design.md": "work/new/children/bug-café/references/design.md",
    }
    plan = _equivalent(root, mapping, sources=[source])
    assert plan.ok
    assert len(plan.moves) == 2
    assert any(edit.member == "work/ref/r.md" for edit in plan.edits)


def test_unicode_destination_identity_collision_preserves_refusal(tmp_path):
    nfd = unicodedata.normalize("NFD", "café")
    root = write_tree(tmp_path / "b", {"x.md": "---\ntitle: X\n---\n", f"{nfd}.md": "---\ntitle: Café\n---\n"})
    plan = _equivalent(root, {"x.md": "café.md"})
    assert "dest-exists" in {r.kind for r in plan.refusals}


def test_repair_of_missing_source_preserves_exact_plan_and_bytes(tmp_path):
    root = write_tree(
        tmp_path / "b",
        {
            "moved/x.md": "---\ntitle: X\n---\n",
            "ref.md": "---\ntitle: R\n---\n[old](./old/x.md)\n[[old/x]]\n",
        },
    )
    plan = _equivalent(root, {"old/x.md": "moved/x.md"}, repair=True)
    assert plan.ok and not plan.relocate and plan.stranded
