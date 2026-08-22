"""The bundle walk: what is skipped, what raises, and what becomes a plan."""

from __future__ import annotations

import pytest
from ext_helpers import GENERATED_DIR, generated_bundle, generated_copy, write_bundle
from okf_ext.generators.model import Render
from okf_ext.generators.plan import plan_regenerate
from okf_ext.sections import DEFAULT_IGNORE as DEFAULT_IGNORE_SECTIONS
from okf_ext.shape import load_sections
from okf_io import load_bundle

SECTION_SET = load_sections(GENERATED_DIR)


def _plan(renders):
    return plan_regenerate(generated_bundle(), SECTION_SET, renders)


def test_a_target_that_is_not_a_member_is_skipped_as_unreadable():
    plan = _plan({"nowhere": Render()})
    assert plan.is_empty
    assert [(s.concept_id, s.reason) for s in plan.skipped] == [("nowhere", "unreadable")]


def test_a_parse_error_concept_is_skipped():
    plan = _plan({"broken": Render(frontmatter={"title": "x"})})
    assert plan.is_empty
    assert [(s.concept_id, s.reason) for s in plan.skipped] == [("broken", "parse-error")]


def test_an_ungranted_frontmatter_key_raises():
    with pytest.raises(ValueError, match="status"):
        _plan({"full": Render(frontmatter={"status": "reviewed"})})


def test_an_ungranted_section_raises():
    with pytest.raises(ValueError, match="How this synthesis has changed"):
        _plan({"full": Render(sections={"How this synthesis has changed": "no"})})


def test_a_concept_with_no_type_raises_for_any_non_empty_render():
    """One rule covers the missing-declaration case for free: an empty granted
    set makes every supplied name ungranted. This diverges from
    `plan_sections`, which reports nothing for such a concept -- here the
    caller named this concept and handed over content for it, so silence
    would swallow the mistake."""
    with pytest.raises(ValueError, match=r"no .*declaration"):
        _plan({"untyped": Render(frontmatter={"title": "x"})})


def test_a_concept_with_an_unknown_type_raises_the_same_way():
    with pytest.raises(ValueError, match=r"no .*declaration"):
        _plan({"unknown_type": Render(sections={"Sources": "- [[a]]"})})


def test_an_empty_render_against_no_declaration_is_a_no_op():
    plan = _plan({"untyped": Render(), "unknown_type": Render()})
    assert plan.is_empty
    assert plan.skipped == ()


def test_an_absent_owned_section_is_skipped_not_created():
    plan = _plan({"no_sources_section": Render(sections={"Sources": "- [[a]]"})})
    reasons = {(s.concept_id, s.reason) for s in plan.skipped}
    assert ("no_sources_section", "section-missing") in reasons
    for regeneration in plan.regenerations:
        assert "## Sources" not in regeneration.after


def test_regenerating_with_the_values_already_on_disk_plans_nothing():
    """Idempotence surfaces as an empty plan -- the signal a `dry_run`
    boolean could not express."""
    plan = _plan(
        {
            "full": Render(
                frontmatter={"title": "okf-io", "sources": ["[[a]]"], "content_hash": "abc123"},
                sections={"Sources": "- [[a]]"},
            )
        }
    )
    assert plan.is_empty


def test_a_drifted_concept_plans_both_halves():
    plan = _plan(
        {
            "drifted": Render(
                frontmatter={"title": "okf-ext", "sources": ["[[new]]"]},
                sections={"Sources": "- [[new]]"},
            )
        }
    )
    assert plan.concept_ids == ("drifted",)
    regeneration = plan.regenerations[0]
    assert {edit.key for edit in regeneration.key_edits} == {"sources"}
    assert {edit.heading for edit in regeneration.section_edits} == {"Sources", "About this page"}
    assert "a human key nothing may touch" not in regeneration.after


def test_a_bare_render_against_a_declared_type_deletes_every_owned_key():
    """The hazard `Render`'s docstring used to get backwards: a bare
    `Render()` is a no-op only against a concept with **no usable
    declaration** (see the other tests in this module using `nowhere`,
    `untyped` and `unknown_type`). Against a concept whose type **is**
    declared, omission is the delete signal -- `full`'s `Entity` declaration
    owns `title` and `sources`, both present on disk, and a caller handing
    over `Render()` for it plans a `KeyEdit("delete", ...)` for both. The way
    to leave a declared concept alone is to omit it from `renders` entirely,
    never to hand over an empty `Render` for it."""
    plan = _plan({"full": Render()})
    edits = {(edit.key, edit.action) for edit in plan.regenerations[0].key_edits}
    assert ("title", "delete") in edits
    assert ("sources", "delete") in edits


def test_an_owned_key_the_run_omits_is_planned_for_deletion():
    plan = _plan({"drifted": Render(frontmatter={"title": "okf-ext"})})
    edits = {(edit.key, edit.action) for edit in plan.regenerations[0].key_edits}
    assert ("sources", "delete") in edits


def test_a_provenance_key_the_run_omits_is_not_planned_at_all():
    plan = _plan({"drifted": Render(frontmatter={"title": "okf-ext", "sources": ["[[old]]"]})})
    keys = {edit.key for regeneration in plan.regenerations for edit in regeneration.key_edits}
    assert "content_hash" not in keys


def test_the_plan_carries_the_bundle_root_it_was_built_against():
    bundle = generated_bundle()
    plan = plan_regenerate(bundle, SECTION_SET, {"drifted": Render(frontmatter={"title": "x"})})
    assert plan.root == bundle.root


def test_a_document_with_no_trailing_newline_whose_content_already_matches_plans_nothing(tmp_path):
    """The `after == document.body` check in `plan_regenerate` is not
    decoration -- it is the only thing standing between a body with no final
    trailing newline and a spurious `Regeneration` on every run against it.

    `splice.replace` unconditionally terminates the last line of what it
    writes, and `assemble` restores the body's own trailing-newline state by
    undoing at most one terminator. For a section that runs to the true end
    of a body with no trailing newline, that interaction can report a genuine
    `SectionEdit` even though the content did not change at all: the sentinel
    byte the splice manufactures is the same byte `assemble` immediately
    erases again on the way back out, so `after` still comes out identical to
    `body`, but `regenerate_body`'s own edit list is not empty. Without this
    check, that phantom edit would turn an unchanged document into a
    `Regeneration`, which is exactly the idempotence contract
    `RegenerationPlan`'s docstring promises does not happen.

    None of the seven committed fixtures lack a trailing newline, so this
    input class is not in the corpus at all -- built here at runtime, on a
    throwaway copy, rather than as an eighth committed fixture, so the corpus
    stays uniformly LF-with-trailing-newline (see
    `test_every_committed_fixture_is_lf_with_a_trailing_newline`)."""
    root = generated_copy(tmp_path)
    full = root / "full.md"
    full.write_bytes(full.read_bytes()[:-1])  # drop exactly the final "\n"
    bundle = load_bundle(root, ignore=DEFAULT_IGNORE_SECTIONS)

    plan = plan_regenerate(
        bundle,
        SECTION_SET,
        {
            "full": Render(
                frontmatter={"title": "okf-io", "sources": ["[[a]]"], "content_hash": "abc123"},
                sections={"Sources": "- [[a]]"},
            )
        },
    )
    assert plan.is_empty
    assert plan.regenerations == ()


def test_the_combined_offense_message_names_both_the_key_and_the_heading():
    """`test_an_ungranted_frontmatter_key_raises` and
    `test_an_ungranted_section_raises` each exercise one offense; neither
    reaches the `"; and "` joiner in `_refuse`'s message, which only fires
    when a single `Render` carries both kinds of ungranted content at once."""
    with pytest.raises(ValueError, match=r"status.*How this synthesis has changed"):
        _plan(
            {
                "full": Render(
                    frontmatter={"status": "reviewed"},
                    sections={"How this synthesis has changed": "no"},
                )
            }
        )


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

_ROOT_INDEX = "---\nokf_version: 0.2\n---\n\n# Bundle\n"


def _indexed(tmp_path, files=None):
    """A bundle with a root index and a `sections/_index.yaml`."""
    members = {"index.md": _ROOT_INDEX, "sections/_index.yaml": _INDEX_DECLARATION}
    members.update(files or {})
    bundle = write_bundle(tmp_path / "kb", members, ignore=DEFAULT_IGNORE_SECTIONS)
    return bundle, load_sections(bundle.root / "sections")


def test_an_index_target_is_planned_against_bundle_indexes(tmp_path):
    bundle, section_set = _indexed(tmp_path)
    plan = plan_regenerate(
        bundle, section_set, {}, index_renders={"": Render(sections={"Repositories": "- [a](/repositories/a.md)"})}
    )
    assert not plan.is_empty
    regeneration = plan.regenerations[0]
    assert (regeneration.concept_id, regeneration.path) == ("", "index.md")
    assert regeneration.key_edits == ()
    assert regeneration.after.endswith("## Repositories\n\n- [a](/repositories/a.md)\n")


def test_a_nested_index_target_carries_its_directory_path(tmp_path):
    bundle, section_set = _indexed(tmp_path, {"packages/index.md": "---\n---\n\n# Packages\n"})
    plan = plan_regenerate(bundle, section_set, {}, index_renders={"packages": Render(sections={"Inventory": "- x"})})
    assert [r.path for r in plan.regenerations] == ["packages/index.md"]


def test_regenerating_an_index_with_the_values_already_there_plans_nothing(tmp_path):
    bundle, section_set = _indexed(tmp_path)
    render = {"": Render(sections={"Repositories": "- [a](/repositories/a.md)"})}
    first = plan_regenerate(bundle, section_set, {}, index_renders=render)
    (bundle.root / "index.md").write_text(
        _ROOT_INDEX.split("# Bundle")[0] + first.regenerations[0].after, encoding="utf-8"
    )
    reloaded = load_bundle(bundle.root, ignore=DEFAULT_IGNORE_SECTIONS)
    assert plan_regenerate(reloaded, section_set, {}, index_renders=render).is_empty


def test_frontmatter_in_an_index_render_raises(tmp_path):
    """AC 7. §8 gives a bundle-root index exactly one legal key, so an index
    declaration grants no frontmatter at all and the existing granted-set
    refusal fires."""
    bundle, section_set = _indexed(tmp_path)
    with pytest.raises(ValueError, match="okf_version"):
        plan_regenerate(bundle, section_set, {}, index_renders={"": Render(frontmatter={"title": "x"})})


def test_an_ungranted_index_section_raises(tmp_path):
    bundle, section_set = _indexed(tmp_path)
    with pytest.raises(ValueError, match="Concepts"):
        plan_regenerate(bundle, section_set, {}, index_renders={"": Render(sections={"Concepts": "- x"})})


def test_an_index_with_no_declaration_raises_for_any_non_empty_render(tmp_path):
    bundle, section_set = _indexed(tmp_path, {"work/index.md": "---\n---\n\n# Work\n"})
    with pytest.raises(ValueError, match=r"no .*declaration"):
        plan_regenerate(bundle, section_set, {}, index_renders={"work": Render(sections={"Items": "- x"})})


def test_an_empty_index_render_against_no_declaration_is_a_no_op(tmp_path):
    bundle, section_set = _indexed(tmp_path, {"work/index.md": "---\n---\n\n# Work\n"})
    plan = plan_regenerate(bundle, section_set, {}, index_renders={"work": Render()})
    assert plan.is_empty
    assert plan.skipped == ()


def test_an_index_that_is_not_a_member_is_skipped_as_unreadable(tmp_path):
    bundle, section_set = _indexed(tmp_path)
    plan = plan_regenerate(bundle, section_set, {}, index_renders={"nowhere": Render()})
    assert [(s.concept_id, s.path, s.reason) for s in plan.skipped] == [("", "nowhere/index.md", "unreadable")]


def test_an_unparseable_index_is_skipped(tmp_path):
    bundle, section_set = _indexed(tmp_path, {"packages/index.md": "---\nnot: [closed\n"})
    plan = plan_regenerate(bundle, section_set, {}, index_renders={"packages": Render(sections={"Inventory": "- x"})})
    assert [(s.path, s.reason) for s in plan.skipped] == [("packages/index.md", "parse-error")]


def test_concept_ids_does_not_report_an_index(tmp_path):
    bundle, section_set = _indexed(tmp_path)
    plan = plan_regenerate(bundle, section_set, {}, index_renders={"": Render(sections={"Repositories": "- x"})})
    assert plan.concept_ids == ()
