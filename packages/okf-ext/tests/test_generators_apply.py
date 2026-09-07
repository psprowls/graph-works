"""The one write that touches both halves."""

from __future__ import annotations

import copy
import io
import tokenize
from pathlib import Path

import pytest
from ext_helpers import GENERATED_DIR, generated_copy, read, write, write_bundle
from okf_ext.generators import Regeneration, RegenerationPlan, Render, apply, plan_regenerate
from okf_ext.sections import DEFAULT_IGNORE as DEFAULT_IGNORE_SECTIONS
from okf_ext.shape import load_sections
from okf_io import load_bundle

SECTION_SET = load_sections(GENERATED_DIR)
IGNORE = ("sections/*", "*/sections/*")

DRIFTED = Render(
    frontmatter={"title": "okf-ext", "sources": ["[[new]]"], "content_hash": "fresh"},
    sections={"Sources": "- [[new]]"},
)


def _bundle(tmp_path):
    return load_bundle(generated_copy(tmp_path), ignore=IGNORE)


def test_owned_keys_and_owned_sections_are_rewritten(tmp_path):
    bundle = _bundle(tmp_path)
    result = apply(bundle, plan_regenerate(bundle, SECTION_SET, {"drifted": DRIFTED}))
    assert result.ok
    assert result.written == ("drifted.md",)
    text = read(bundle.root / "drifted.md")
    assert "- [[new]]" in text
    assert "content_hash: fresh" in text
    assert "Generated from the code graph." in text


def test_every_byte_the_declaration_did_not_grant_survives(tmp_path):
    bundle = _bundle(tmp_path)
    apply(bundle, plan_regenerate(bundle, SECTION_SET, {"drifted": DRIFTED}))
    text = read(bundle.root / "drifted.md")
    assert "status: draft" in text
    assert "notes: a human key nothing may touch" in text
    assert "Prose that must survive a regeneration byte for byte." in text


def test_the_whole_file_matches_byte_for_byte(tmp_path):
    """Criterion 1's strongest form: not "these substrings appear" but "this
    is the entire file". Every line not touched by an owned key or an owned
    section -- `type`, `status`, `notes`, the quoting ruamel chose for the
    freshly-set Python list versus the human's original block style, the
    untouched `## Summary` and `## How this synthesis has changed` prose --
    is pinned here, not sampled."""
    bundle = _bundle(tmp_path)
    apply(bundle, plan_regenerate(bundle, SECTION_SET, {"drifted": DRIFTED}))
    text = read(bundle.root / "drifted.md")
    assert text == (
        "---\n"
        "title: okf-ext\n"
        "type: Entity\n"
        "status: draft\n"
        "content_hash: fresh\n"
        "sources:\n"
        "  - '[[new]]'\n"
        "notes: a human key nothing may touch\n"
        "---\n"
        "\n"
        "## Summary\n"
        "\n"
        "Tier two.\n"
        "\n"
        "## Sources\n"
        "\n"
        "- [[new]]\n"
        "\n"
        "## How this synthesis has changed\n"
        "\n"
        "Prose that must survive a regeneration byte for byte.\n"
        "\n"
        "## About this page\n"
        "\n"
        "Generated from the code graph. Edits here are overwritten on the next run.\n"
    )


def test_a_sibling_is_not_touched(tmp_path):
    bundle = _bundle(tmp_path)
    before = read(bundle.root / "full.md")
    apply(bundle, plan_regenerate(bundle, SECTION_SET, {"drifted": DRIFTED}))
    assert read(bundle.root / "full.md") == before


def test_the_in_memory_bundle_agrees_with_disk_after_the_write(tmp_path):
    """`on_written` adopts both halves onto the live `Document`, for exactly
    the documents written and no others -- with no reload."""
    bundle = _bundle(tmp_path)
    apply(bundle, plan_regenerate(bundle, SECTION_SET, {"drifted": DRIFTED}))
    document = bundle.concepts["drifted"]
    assert document.fm_raw["content_hash"] == "fresh"
    assert "- [[new]]" in document.body
    assert bundle.concepts["full"].fm_raw["content_hash"] == "abc123"


def test_applying_twice_writes_nothing_the_second_time(tmp_path):
    bundle = _bundle(tmp_path)
    apply(bundle, plan_regenerate(bundle, SECTION_SET, {"drifted": DRIFTED}))
    second = plan_regenerate(bundle, SECTION_SET, {"drifted": DRIFTED})
    assert second.is_empty


def test_a_plan_from_another_bundle_raises(tmp_path):
    one = _bundle(tmp_path / "one")
    two = _bundle(tmp_path / "two")
    plan = plan_regenerate(one, SECTION_SET, {"drifted": DRIFTED})
    with pytest.raises(ValueError, match="different bundle"):
        apply(two, plan)


def test_a_stale_plan_is_refused_for_that_document_alone(tmp_path):
    bundle = _bundle(tmp_path)
    plan = plan_regenerate(bundle, SECTION_SET, {"drifted": DRIFTED})
    write(bundle.root / "drifted.md", read(bundle.root / "drifted.md") + "\nedited\n")
    reloaded = load_bundle(bundle.root, ignore=IGNORE)
    result = apply(reloaded, RegenerationPlan(root=reloaded.root, regenerations=plan.regenerations, skipped=()))
    assert [failure.kind for failure in result.failed] == ["stale"]


def test_a_plan_naming_one_concept_twice_is_refused(tmp_path):
    bundle = _bundle(tmp_path)
    plan = plan_regenerate(bundle, SECTION_SET, {"drifted": DRIFTED})
    doubled = RegenerationPlan(
        root=bundle.root,
        regenerations=plan.regenerations + plan.regenerations,
        skipped=(),
    )
    result = apply(bundle, doubled)
    assert [failure.kind for failure in result.failed] == ["duplicate-edit"]


def test_a_hand_built_plan_naming_a_stranger_is_not_a_member(tmp_path):
    bundle = _bundle(tmp_path)
    plan = RegenerationPlan(
        root=bundle.root,
        regenerations=(
            Regeneration(
                concept_id="nowhere",
                path="nowhere.md",
                key_edits=(),
                section_edits=(),
                digest="x",
                after="",
            ),
        ),
        skipped=(),
    )
    assert [failure.kind for failure in apply(bundle, plan).failed] == ["not-a-member"]


def test_the_plans_skips_are_carried_into_the_result(tmp_path):
    bundle = _bundle(tmp_path)
    plan = plan_regenerate(
        bundle, SECTION_SET, {"drifted": DRIFTED, "no_sources_section": Render(sections={"Sources": "- [[a]]"})}
    )
    result = apply(bundle, plan)
    assert any(skip.reason == "section-missing" for skip in result.skipped)


def test_the_capability_surface_is_importable():
    from okf_ext import generators

    for name in ("plan_regenerate", "apply"):
        assert callable(getattr(generators, name))


def test_a_parse_error_document_named_directly_is_refused(tmp_path):
    """Defence in depth: `plan_regenerate` already skips a parse-error concept
    via `Skipped(reason="parse-error")`, so this exercises the guard `apply`
    keeps for a hand-built plan that names one anyway -- the same shape as
    `test_a_hand_built_plan_naming_a_stranger_is_not_a_member` above."""
    bundle = _bundle(tmp_path)
    document = bundle.concepts["broken"]
    assert document.parse_error is not None
    plan = RegenerationPlan(
        root=bundle.root,
        regenerations=(
            Regeneration(
                concept_id="broken",
                path="broken.md",
                key_edits=(),
                section_edits=(),
                digest="x",
                after="",
            ),
        ),
        skipped=(),
    )
    result = apply(bundle, plan)
    assert [failure.kind for failure in result.failed] == ["parse-error"]


def test_a_serialize_error_is_refused_for_that_document_alone_and_a_sibling_still_writes(tmp_path, monkeypatch):
    """Criterion 5's "siblings still write" half, which a single-concept plan
    cannot demonstrate: with only one concept in the batch, there is no
    sibling to observe succeeding alongside a failure. This plans **two**
    concepts -- `drifted`, forced to blow up in `Document.serialize`, and
    `empty_sections`, a real edit against a real fixture -- and checks both
    `result.failed` (naming the one that broke) and `result.written` (naming
    the one that did not), then reads `empty_sections.md` back off disk to
    confirm the write actually landed rather than merely being reported."""
    bundle = _bundle(tmp_path)
    renders = {
        "drifted": DRIFTED,
        "empty_sections": Render(frontmatter={"sources": ["[[b]]"]}, sections={"Sources": "- [[b]]"}),
    }
    plan = plan_regenerate(bundle, SECTION_SET, renders)
    assert {regeneration.concept_id for regeneration in plan.regenerations} == {"drifted", "empty_sections"}

    from okf_io import document as document_module

    original_serialize = document_module.Document.serialize

    def _boom(self):
        if self.path is not None and self.path.name == "drifted.md":
            raise ValueError("synthetic serialize failure")
        return original_serialize(self)

    monkeypatch.setattr(document_module.Document, "serialize", _boom)

    result = apply(bundle, plan)

    assert [failure.path for failure in result.failed] == ["drifted.md"]
    assert [failure.kind for failure in result.failed] == ["serialize-error"]
    assert result.written == ("empty_sections.md",)
    assert "- [[b]]" in read(bundle.root / "empty_sections.md")


def test_a_duplicate_edit_plan_is_refused_before_touching_the_document(tmp_path):
    """Pins the duplicate-edit refusal itself -- **not** scratch privacy.

    `apply` catches `len(items) > 1` before ever calling `_rendered`, so
    `_rendered`'s own `copy.deepcopy` runs zero times on this path: a
    `_rendered` that skipped the deepcopy and aliased `document.fm_raw`
    directly would leave this test passing exactly the same. See
    `test_a_serialize_error_leaves_the_live_document_untouched` below for the
    negative case that path actually exercises `_rendered` and can tell the
    two apart."""
    bundle = _bundle(tmp_path)
    plan = plan_regenerate(bundle, SECTION_SET, {"drifted": DRIFTED})
    doubled = RegenerationPlan(root=bundle.root, regenerations=plan.regenerations + plan.regenerations, skipped=())

    result = apply(bundle, doubled)

    assert [failure.kind for failure in result.failed] == ["duplicate-edit"]


def test_a_serialize_error_leaves_the_live_document_untouched(tmp_path, monkeypatch):
    """Criterion 3's real negative case.

    `serialize-error` is the one failure kind that is only ever detected
    *after* `_rendered` has already run `copy.deepcopy` and mutated the
    scratch's frontmatter and body via `Document.set` / `Document.delete` /
    `Document.set_body` -- `scratch.serialize()` is the very last thing
    `_rendered` does before handing back to `apply`. If `_rendered` aliased
    `document.fm_raw` instead of copying it (the aliasing hazard
    `rendered_with_body()`'s own docstring warns about), those mutations
    would land on the *live* document even though the write never reaches
    disk. The duplicate-edit test above cannot see this, because it never
    reaches `_rendered` at all.

    The snapshot is a `copy.deepcopy`, not a shallow `dict(...)`: `sources`
    is a nested `CommentedSeq`, and a shallow copy would still alias that
    inner sequence, silently agreeing with a mutated live document."""
    bundle = _bundle(tmp_path)
    plan = plan_regenerate(bundle, SECTION_SET, {"drifted": DRIFTED})
    document = bundle.concepts["drifted"]
    before_fm = copy.deepcopy(document.fm_raw)
    before_body = document.body

    from okf_io import document as document_module

    original_serialize = document_module.Document.serialize

    def _boom(self):
        if self.path is not None and self.path.name == "drifted.md":
            raise ValueError("synthetic serialize failure")
        return original_serialize(self)

    monkeypatch.setattr(document_module.Document, "serialize", _boom)

    result = apply(bundle, plan)

    assert [failure.kind for failure in result.failed] == ["serialize-error"]
    assert document.fm_raw == before_fm
    assert document.body == before_body


def test_apply_never_uses_rendered_with_body():
    """Criterion 2, asserted mechanically rather than trusted by convention,
    over the **whole capability** -- not just `apply.py`.

    `rendered_with_body()` is a shallow copy (`clone.fm_raw is document.fm_raw`)
    that is safe only because `set_body` never touches `fm_raw` -- this is the
    first capability to edit both halves in one write, so using it anywhere
    in this package would alias the live document's frontmatter. Unlike
    `sections/scaffold.py` and `tables/splice.py`, which use it correctly
    because they only ever touch the body, nothing in `generators` may reach
    for it -- including a module this test does not know about yet.

    Walks every `.py` file in `apply`'s own parent directory rather than
    naming `apply.py`, `plan.py`, `regenerate.py`, ... by hand: a future
    module dropped into the package is covered automatically, and the list
    here can never drift out of sync with what is actually on disk.

    Checks tokens, not raw text: this module's own docstring names
    `rendered_with_body()` to explain what it deliberately avoids, and a
    plain substring search would fail against its own explanation.
    `tokenize` strips comments and string literals (docstrings included),
    leaving only the code a caller could actually execute.

    Reached via `apply.__globals__["__file__"]` rather than
    `import okf_ext.generators.apply as m; m.__file__`: `__init__.py` rebinds
    the package attribute `apply` to this very function, so
    `okf_ext.generators.apply` as a dotted attribute access no longer names
    the submodule at all -- the classic package/name collision. The
    function's own `__globals__` is immune to that shadowing.
    """
    package_dir = Path(apply.__globals__["__file__"]).parent
    python_files = sorted(package_dir.glob("*.py"))
    assert len(python_files) >= 5  # apply, frontmatter, model, plan, regenerate, __init__ -- a floor, not a ceiling

    offenders = []
    for path in python_files:
        source = path.read_text(encoding="utf-8")
        code_tokens = [
            token.string
            for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type not in (tokenize.STRING, tokenize.COMMENT)
        ]
        if "rendered_with_body" in code_tokens:
            offenders.append(path.name)

    assert offenders == []


_INDEX_DECLARATION = """\
directories:
  "":
    sections:
      - heading: Repositories
        ownership: generated
        required: true
        placeholder: |
          _(not yet generated)_
  packages:
    sections:
      - heading: Inventory
        ownership: generated
"""


def test_two_index_targets_in_one_plan_both_write(tmp_path):
    """The regression the `concept_id` grouping would cause: every index
    carries `concept_id=""`, so grouping by id collapses them into one
    `duplicate-edit` refusal and resolves neither."""
    root = tmp_path / "kb"
    files = {
        "index.md": "---\nokf_version: 0.2\n---\n\n# Bundle\n",
        "packages/index.md": "---\n---\n\n# Packages\n",
        "sections/_index.yaml": _INDEX_DECLARATION,
    }
    bundle = write_bundle(root, files, ignore=DEFAULT_IGNORE_SECTIONS)
    section_set = load_sections(root / "sections")
    plan = plan_regenerate(
        bundle,
        section_set,
        {},
        index_renders={
            "": Render(sections={"Repositories": "- [a](/repositories/a.md)"}),
            "packages": Render(sections={"Inventory": "- widgets"}),
        },
    )
    result = apply(bundle, plan)
    assert result.ok
    assert sorted(result.written) == ["index.md", "packages/index.md"]
    assert "## Repositories" in (root / "index.md").read_text(encoding="utf-8")
    assert "## Inventory" in (root / "packages" / "index.md").read_text(encoding="utf-8")


def test_an_index_write_preserves_every_other_byte(tmp_path):
    root = tmp_path / "kb"
    before = "---\nokf_version: 0.2\n---\n\n# Bundle\n\nHand-written prose nobody may touch.\n"
    bundle = write_bundle(
        root, {"index.md": before, "sections/_index.yaml": _INDEX_DECLARATION}, ignore=DEFAULT_IGNORE_SECTIONS
    )
    section_set = load_sections(root / "sections")
    index_renders = {"": Render(sections={"Repositories": "- [a](/a.md)"})}
    plan = plan_regenerate(bundle, section_set, {}, index_renders=index_renders)
    apply(bundle, plan)
    after = (root / "index.md").read_text(encoding="utf-8")
    assert after.startswith(before.rstrip("\n"))
    assert "Hand-written prose nobody may touch." in after
    assert after.endswith("## Repositories\n\n- [a](/a.md)\n")
