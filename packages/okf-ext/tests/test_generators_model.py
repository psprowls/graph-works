"""The frozen values, and the two properties that make a plan a preview."""

from __future__ import annotations

from pathlib import Path

import pytest
from okf_ext.generators.model import KeyEdit, Regeneration, RegenerationPlan, Render, SectionEdit
from okf_ext.writing import Skipped


def _regeneration(concept_id: str) -> Regeneration:
    return Regeneration(
        concept_id=concept_id,
        path=f"{concept_id}.md",
        key_edits=(KeyEdit(key="title", action="set", value="x"),),
        section_edits=(SectionEdit(heading="Sources", line=7),),
        digest="deadbeef",
        after="## Sources\n",
    )


def test_an_empty_render_is_legal():
    """A caller with nothing to say for a concept must be able to say so
    without constructing two empty mappings by hand."""
    render = Render()
    assert render.frontmatter == {}
    assert render.sections == {}


def test_render_defaults_are_shared_immutable_singletons():
    """Defaults are the same object across instances, safe only because immutable.

    Sharing a single MappingProxyType({}) avoids the mutable-default hazard that
    would arise from `field(default_factory=dict)`. The load-bearing guarantee is
    immutability: two instances can safely share the same default because mutating
    it raises. This test enforces both properties together -- if one changes without
    the other, a future refactor introduces a real bug."""
    r1 = Render()
    r2 = Render()
    # The singleton is shared (safe only because immutable)
    assert r1.frontmatter is r2.frontmatter
    assert r1.sections is r2.sections
    # Mutation is forbidden (the reason sharing is safe)
    with pytest.raises(TypeError):
        r1.frontmatter["key"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        r1.sections["heading"] = "content"  # type: ignore[index]


def test_every_value_is_frozen():
    with pytest.raises(AttributeError):
        Render().frontmatter = {}  # type: ignore[misc]
    with pytest.raises(AttributeError):
        KeyEdit(key="k", action="set", value=1).key = "other"  # type: ignore[misc]


def test_a_plan_with_no_regenerations_is_empty():
    plan = RegenerationPlan(root=Path("/tmp/x"), regenerations=(), skipped=())
    assert plan.is_empty
    assert plan.concept_ids == ()


def test_a_plan_that_only_skipped_is_still_empty():
    """Skipped members do not count as work done; `is_empty` is the "nothing to do" signal.

    A plan with zero regenerations but some skipped members is still empty, because
    skipped members represent reading failures (a document that could not be processed),
    not work to be applied. `is_empty` returns `not self.regenerations`, deliberately
    excluding skipped: idempotence is "no regenerations planned", not "no work planned"."""
    plan = RegenerationPlan(
        root=Path("/tmp/x"),
        regenerations=(),
        skipped=(Skipped(concept_id="c1", path="c1.md", reason="parse-error", detail="invalid yaml"),),
    )
    assert plan.is_empty
    assert plan.concept_ids == ()


def test_concept_ids_are_sorted_and_deduplicated():
    plan = RegenerationPlan(
        root=Path("/tmp/x"),
        regenerations=(_regeneration("b"), _regeneration("a"), _regeneration("b")),
        skipped=(),
    )
    assert plan.concept_ids == ("a", "b")
    assert not plan.is_empty


def test_the_body_stays_out_of_the_repr():
    """`after` carries a whole document body; a plan of forty of them printed
    in a traceback is unreadable."""
    assert "## Sources" not in repr(_regeneration("a"))
