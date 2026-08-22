"""`render_skeleton` and the plan/apply writers."""

from __future__ import annotations

import shutil
from types import MappingProxyType

import pytest
from ext_helpers import SECTIONS_DIR, sectioned_copy, write_bundle
from okf_ext.body import split_lines
from okf_ext.sections import TypeSections, apply, load_sections, plan_sections, render_skeleton
from okf_ext.sections.model import SectionPlan, SectionSet, SectionSpec, SectionSplice
from okf_io import load_bundle
from okf_io.document import Document
from ruamel.yaml.error import YAMLError

PLAN_TABLE = "| Action | Done when | Rationale |\n| --- | --- | --- |\n"

FEATURE = TypeSections(
    sections=(
        SectionSpec(heading="Summary", required=True, placeholder="<!-- one paragraph -->\n"),
        SectionSpec(heading="Options considered"),
        SectionSpec(heading="Plan", required=True, seeded_is_complete=True, placeholder=PLAN_TABLE),
        SectionSpec(heading="Notes / log"),
    )
)


def test_every_declared_section_is_emitted_required_and_optional():
    """This is the 'template for creating new items' half: optional sections
    belong in a fresh skeleton even though they are never scaffolded into an
    existing document."""
    rendered = render_skeleton(FEATURE)
    assert rendered == (
        "## Summary\n"
        "\n"
        "<!-- one paragraph -->\n"
        "\n"
        "## Options considered\n"
        "\n"
        "## Plan\n"
        "\n"
        "| Action | Done when | Rationale |\n"
        "| --- | --- | --- |\n"
        "\n"
        "## Notes / log\n"
        "\n"
    )


def test_declaration_order_is_the_rendered_order():
    headings = [line for line in render_skeleton(FEATURE).split("\n") if line.startswith("#")]
    assert headings == ["## Summary", "## Options considered", "## Plan", "## Notes / log"]


def test_a_declared_level_is_honoured():
    declaration = TypeSections(sections=(SectionSpec(heading="Entries", level=3),))
    assert render_skeleton(declaration) == "### Entries\n\n"


def test_the_newline_parameter_controls_the_line_ending():
    """No bundle, no filesystem, nothing to sniff a newline from."""
    rendered = render_skeleton(TypeSections(sections=(SectionSpec(heading="Summary"),)), newline="\r\n")
    assert rendered == "## Summary\r\n\r\n"
    assert "\n" not in rendered.replace("\r\n", "")


def test_a_declaration_with_no_sections_renders_nothing():
    assert render_skeleton(TypeSections(sections=())) == ""


def test_a_placeholders_surrounding_blank_lines_are_dropped():
    """A YAML `|` block scalar always ends in a newline, and a hand-written one
    may open with a blank. Neither should become a second blank in the output."""
    spec = SectionSpec(heading="Summary", placeholder="\n\nreal\n\n\n")
    assert render_skeleton(TypeSections(sections=(spec,))) == "## Summary\n\nreal\n\n"


def test_a_crlf_placeholder_is_re_terminated_with_the_requested_newline():
    spec = SectionSpec(heading="Summary", placeholder="a\r\nb\r\n")
    assert render_skeleton(TypeSections(sections=(spec,))) == "## Summary\n\na\nb\n\n"


FEATURE_SET = SectionSet(
    types=MappingProxyType({"Feature": FEATURE}),
    sources=MappingProxyType({"Feature": "Feature.yaml"}),
    fragments=MappingProxyType({}),
    root=SECTIONS_DIR,
)


def doc(body, *, type_name="Feature"):
    return f"---\ntype: {type_name}\ntitle: T\ndescription: D\n---\n\n{body}"


def planned(tmp_path, files, section_set=FEATURE_SET):
    bundle = write_bundle(tmp_path / "kb", files)
    return bundle, plan_sections(bundle, section_set)


def body_of(root, member):
    text = (root / member).read_bytes().decode("utf-8")
    return text.split("---\n", 2)[2]


def test_only_required_sections_are_scaffolded(tmp_path):
    """Optional sections appear in `render_skeleton`'s output and nowhere
    else -- scaffolding them would create sections nobody needs, which is the
    outcome the three-class model exists to avoid."""
    _, plan = planned(tmp_path, {"a.md": doc("## Summary\n\nReal.\n")})
    assert [i.heading for i in plan.splices[0].inserts] == ["Plan"]


def test_one_splice_per_document_however_many_inserts(tmp_path):
    _, plan = planned(tmp_path, {"a.md": doc("Prose with no headings at all.\n")})
    assert len(plan.splices) == 1
    assert [i.heading for i in plan.splices[0].inserts] == ["Summary", "Plan"]


def test_a_missing_section_lands_after_the_last_earlier_one_present(tmp_path):
    """Fallback 1: walk backwards through the declared list."""
    _, plan = planned(tmp_path, {"a.md": doc("## Summary\n\nReal.\n\n## Notes / log\n\nTail.\n")})
    after = plan.splices[0].after
    assert after.index("## Plan") > after.index("## Summary")
    assert after.index("## Plan") < after.index("## Notes / log")


def test_a_missing_section_lands_before_the_first_later_one_present(tmp_path):
    """Fallback 2: nothing earlier is present, so walk forwards."""
    _, plan = planned(tmp_path, {"a.md": doc("## Plan\n\n| a |\n\n## Notes / log\n\nTail.\n")})
    after = plan.splices[0].after
    assert after.index("## Summary") < after.index("## Plan")


def test_a_missing_section_is_appended_when_nothing_declared_is_present(tmp_path):
    """Fallback 3."""
    _, plan = planned(tmp_path, {"a.md": doc("## Appendix\n\nUndeclared.\n")})
    after = plan.splices[0].after
    assert after.index("## Summary") > after.index("## Appendix")
    assert after.index("## Summary") < after.index("## Plan")


def test_a_missing_section_lands_at_the_end_of_the_earlier_ones_declared_territory(tmp_path):
    """Fallback 1 anchors on the *next declared heading in document order*,
    not on the earlier section's `Section.stop`.

    The distinction only shows up when an undeclared heading is interleaved,
    and it is the whole reason `_declared_territory_end` exists. `Summary.stop`
    is bounded by the next heading of any kind, so anchoring there would plant
    `## Plan` between `## Summary` and `## Appendix` -- flush against the
    undeclared heading, splitting authored content. Walking forward to the
    next *declared* heading instead lands it after `## Appendix`, which is
    what makes "undeclared headings do not perturb placement" true rather
    than merely intended.

    The design spec's §6.3 says `Section.stop` and then claims the
    non-perturbation property in the following paragraph; those cannot both
    hold, and this is the reading that shipped.
    """
    body = "## Summary\n\nReal.\n\n## Appendix\n\nUndeclared.\n\n## Notes / log\n\nTail.\n"
    _, plan = planned(tmp_path, {"a.md": doc(body)})
    assert plan.splices[0].after == (
        "## Summary\n\nReal.\n\n## Appendix\n\nUndeclared.\n\n"
        "## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n\n"
        "## Notes / log\n\nTail.\n"
    )


def test_a_declared_but_out_of_order_earlier_section_still_wins_over_a_later_one(tmp_path):
    """§5.4: reordering sections is house style, not a defect. `Notes / log`
    (declared *after* `Plan`) physically precedes `Summary` (declared
    *before* `Plan`, required, present) here -- a legal reordering. The
    missing `Plan` must still land after `Summary`, the present section it is
    declared to follow, not before it merely because `Notes / log` happens to
    sit earlier in the file."""
    body = "## Notes / log\n\nTail.\n\n## Summary\n\nReal.\n"
    _, plan = planned(tmp_path, {"a.md": doc(body)})
    assert plan.splices[0].after == (
        "## Notes / log\n\nTail.\n\n## Summary\n\nReal.\n\n"
        "## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |\n\n"
    )


def test_a_run_of_missing_sections_lands_in_declaration_order_and_contiguously(tmp_path):
    """Each is placed against the document as amended by the previous insert."""
    _, plan = planned(tmp_path, {"a.md": doc("## Notes / log\n\nTail.\n")})
    after = plan.splices[0].after
    assert after.index("## Summary") < after.index("## Plan") < after.index("## Notes / log")
    between = after[after.index("## Summary") : after.index("## Notes / log")]
    assert "Tail." not in between


def test_a_separating_blank_is_inserted_above_non_blank_content(tmp_path):
    """The body has no trailing newline, so the scaffolded one has none
    either -- `_block` closes every section with a blank sentinel line, and
    appending at the true end drops it rather than silently granting the body
    a terminator it never had. `tables/splice.py` omits the same blank for
    the same reason."""
    _, plan = planned(tmp_path, {"a.md": doc("## Summary\n\nReal.")})
    assert plan.splices[0].after.endswith("Real.\n\n## Plan\n\n| Action | Done when | Rationale |\n| --- | --- | --- |")


def test_the_insert_line_is_the_first_line_of_the_whole_block(tmp_path):
    """Including the separating blank, so a caller diffing from `line` sees a
    contiguous span with no gap-shaped hole above it."""
    _, plan = planned(tmp_path, {"a.md": doc("## Summary\n\nReal.")})
    insert = plan.splices[0].inserts[0]
    lines = split_lines(plan.splices[0].after)
    assert not lines[insert.line - 1].strip()
    assert lines[insert.line].startswith("## Plan")


def test_no_second_blank_is_stacked_on_an_existing_one(tmp_path):
    _, plan = planned(tmp_path, {"a.md": doc("## Summary\n\nReal.\n\n")})
    assert "\n\n\n" not in plan.splices[0].after


def test_a_document_already_carrying_every_required_section_produces_no_splice(tmp_path):
    """Idempotence surfaces as an empty plan."""
    _, plan = planned(tmp_path, {"a.md": doc("## Summary\n\nReal.\n\n## Plan\n\n| a |\n")})
    assert plan.is_empty


def test_a_document_with_no_type_is_not_a_skip_and_is_not_reported(tmp_path):
    """It was read perfectly well; it simply has no declaration to be measured
    against. `sections.no-declaration-for-type` is where that gap surfaces, and
    the writer does not duplicate the rule."""
    _, plan = planned(tmp_path, {"a.md": "---\ntitle: T\n---\n\n## X\n"})
    assert plan.is_empty
    assert plan.skipped == ()


def test_a_type_with_no_declaration_is_not_a_skip_either(tmp_path):
    _, plan = planned(tmp_path, {"a.md": doc("## X\n", type_name="Glossary")})
    assert plan.is_empty
    assert plan.skipped == ()


def test_a_parse_error_is_skipped_with_that_reason(tmp_path):
    _, plan = planned(tmp_path, {"a.md": "---\ntype: [unclosed\n---\n\n## X\n"})
    assert [(s.concept_id, s.reason) for s in plan.skipped] == [("a", "parse-error")]


def test_an_unreadable_member_is_skipped_with_that_reason(tmp_path):
    """`load_bundle` records a member it could not decode in `bundle.unreadable`
    and never makes it a concept, so this is the only place it can surface."""
    root = tmp_path / "kb"
    root.mkdir()
    (root / "a.md").write_bytes(b"---\ntype: Feature\n---\n\n\xff\xfe not utf-8\n")
    plan = plan_sections(load_bundle(root), FEATURE_SET)
    assert [(s.concept_id, s.reason) for s in plan.skipped] == [("a", "unreadable")]


def test_reserved_members_are_not_reported_as_unreadable(tmp_path):
    """`index.md` and `log.md` are not concepts, so an unreadable one is not
    this capability's business."""
    root = tmp_path / "kb"
    root.mkdir()
    (root / "index.md").write_bytes(b"\xff\xfe")
    (root / "a.md").write_text(doc("## Summary\n\nR.\n\n## Plan\n\n| a |\n"), encoding="utf-8")
    assert plan_sections(load_bundle(root), FEATURE_SET).skipped == ()


def test_apply_writes_the_planned_body_and_reports_it(tmp_path):
    root = sectioned_copy(tmp_path)
    section_set = load_sections(SECTIONS_DIR)
    bundle = load_bundle(root, ignore=("sections/*",))
    plan = plan_sections(bundle, section_set)
    result = apply(bundle, plan)
    assert result.ok
    assert "missing.md" in result.written
    assert "## Plan" in body_of(root, "missing.md")


def test_applying_twice_is_a_no_op(tmp_path):
    """A generator that re-runs is the reason this exists."""
    root = sectioned_copy(tmp_path)
    section_set = load_sections(SECTIONS_DIR)
    bundle = load_bundle(root, ignore=("sections/*",))
    apply(bundle, plan_sections(bundle, section_set))
    first = (root / "missing.md").read_bytes()
    reloaded = load_bundle(root, ignore=("sections/*",))
    assert plan_sections(reloaded, section_set).is_empty
    assert (root / "missing.md").read_bytes() == first


def test_the_in_memory_bundle_agrees_with_disk_after_apply(tmp_path):
    root = sectioned_copy(tmp_path)
    section_set = load_sections(SECTIONS_DIR)
    bundle = load_bundle(root, ignore=("sections/*",))
    apply(bundle, plan_sections(bundle, section_set))
    assert "## Plan" in bundle.concepts["missing"].body


def test_a_stale_digest_is_refused_as_kind_stale(tmp_path):
    """The one kind worth re-planning over."""
    root = sectioned_copy(tmp_path)
    section_set = load_sections(SECTIONS_DIR)
    bundle = load_bundle(root, ignore=("sections/*",))
    plan = plan_sections(bundle, section_set)
    (root / "missing.md").write_text(
        "---\ntype: Feature\ntitle: T\ndescription: D\n---\n\nsomething else entirely\n", encoding="utf-8"
    )
    result = apply(load_bundle(root, ignore=("sections/*",)), plan)
    assert [(f.path, f.kind) for f in result.failed] == [("missing.md", "stale")]


def test_a_plan_from_another_bundle_raises(tmp_path):
    """A plan's digests and line numbers mean nothing outside the bundle it was
    planned against."""
    root = sectioned_copy(tmp_path)
    other = tmp_path / "other"
    shutil.copytree(root, other)
    section_set = load_sections(SECTIONS_DIR)
    plan = plan_sections(load_bundle(root, ignore=("sections/*",)), section_set)
    with pytest.raises(ValueError, match="different bundle"):
        apply(load_bundle(other, ignore=("sections/*",)), plan)


def test_a_splice_naming_a_concept_absent_from_this_bundle_is_reported(tmp_path):
    """A hand-built plan can name a concept that is not (or no longer) a
    member of this bundle; `plan_sections` itself can never produce one."""
    root = sectioned_copy(tmp_path)
    bundle = load_bundle(root, ignore=("sections/*",))
    ghost = SectionSplice(concept_id="ghost", path="ghost.md", inserts=(), digest="d", after="x")
    result = apply(bundle, SectionPlan(root=bundle.root, splices=(ghost,), skipped=()))
    assert [(f.path, f.kind) for f in result.failed] == [("ghost.md", "not-a-member")]


def test_a_hand_built_splice_over_a_parse_error_concept_is_refused(tmp_path):
    root = sectioned_copy(tmp_path)
    (root / "broken.md").write_text("---\ntype: [\n---\n\n# Broken\n", encoding="utf-8")
    before = (root / "broken.md").read_bytes()
    bundle = load_bundle(root, ignore=("sections/*",))
    splice = SectionSplice(concept_id="broken", path="broken.md", inserts=(), digest="d", after="x")
    result = apply(bundle, SectionPlan(root=bundle.root, splices=(splice,), skipped=()))
    assert result.failed[0].kind == "parse-error"
    assert (root / "broken.md").read_bytes() == before


def test_a_serialize_failure_for_one_document_does_not_block_its_siblings(tmp_path, monkeypatch):
    root = sectioned_copy(tmp_path)
    section_set = load_sections(SECTIONS_DIR)
    bundle = load_bundle(root, ignore=("sections/*",))
    plan = plan_sections(bundle, section_set)
    assert "missing.md" in [s.path for s in plan.splices]
    original = Document.serialize

    def flaky(self):
        if self.path is not None and self.path.name == "missing.md":
            raise YAMLError("boom")
        return original(self)

    monkeypatch.setattr(Document, "serialize", flaky)
    result = apply(bundle, plan)
    assert "missing.md" not in result.written
    assert ("missing.md", "serialize-error") in [(f.path, f.kind) for f in result.failed]


def test_a_plan_naming_one_concept_twice_is_refused(tmp_path):
    """No plan `plan_sections` builds can carry one, but a hand-built plan
    could -- and both splices were computed against the same body, so applying
    them in sequence would silently discard the first."""
    root = sectioned_copy(tmp_path)
    bundle = load_bundle(root, ignore=("sections/*",))
    plan = plan_sections(bundle, load_sections(SECTIONS_DIR))
    doubled = SectionPlan(root=plan.root, splices=plan.splices + plan.splices[:1], skipped=())
    result = apply(bundle, doubled)
    assert "duplicate-edit" in {f.kind for f in result.failed}


def test_the_plan_carries_the_bundle_root_it_was_built_against(tmp_path):
    bundle, plan = planned(tmp_path, {"a.md": doc("## Summary\n\nReal.\n")})
    assert plan.root == bundle.root
    assert plan.concept_ids == ("a",)
