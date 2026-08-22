"""Retyping: refusals as data, the durable half first, and the round trip."""

from __future__ import annotations

from pathlib import Path

import pytest
from diataxis_helpers import build_bundle, schema_set, section_set
from doc_wiki_okf.diataxis.classify import Classification
from doc_wiki_okf.diataxis.pages import new_page_text
from doc_wiki_okf.diataxis.retype import apply_retype, plan_retype
from okf_io import load, load_bundle


def _page(type_name: str, title: str, concept_id: str) -> str:
    return new_page_text(
        schema_set=schema_set(),
        section_set=section_set(),
        classification=Classification(
            type_name=type_name,
            concept_id=concept_id,
            title=title,
            rationale="because",
            decided_by="agent:test",
        ),
        description=f"A {type_name} page.",
    )


@pytest.fixture
def bundle_root(tmp_path: Path) -> Path:
    """An `Explanation` page, plus a third page linking to it."""
    root = tmp_path / "bundle"
    citing = (
        "---\ntype: Reference\ntitle: Flags\ndescription: Flags.\n---\n\n"
        "## Summary\n\nSee [the explanation](../explanations/why-it-works.md).\n"
    )
    build_bundle(
        root,
        {
            "explanations/why-it-works": _page("Explanation", "Why it works", "explanations/why-it-works"),
            "references/flags": citing,
        },
    )
    return root


def test_the_round_trip_moves_the_page_rewrites_the_type_and_repairs_the_link(bundle_root: Path) -> None:
    """Spec §7.6."""
    bundle = load_bundle(bundle_root)
    plan = plan_retype(bundle, schema_set(), "explanations/why-it-works", "Reference")
    assert plan.ok, plan.diff()
    assert plan.dest == "references/why-it-works.md"
    assert plan.from_type == "Explanation"

    result = apply_retype(bundle, plan)
    assert result.ok, result.move.failed
    assert result.type_written is True

    moved = bundle_root / "references" / "why-it-works.md"
    assert moved.is_file()
    assert not (bundle_root / "explanations" / "why-it-works.md").exists()
    assert load(moved).fm.type == "Reference"

    citing = (bundle_root / "references" / "flags.md").read_text(encoding="utf-8")
    assert "../explanations/why-it-works.md" not in citing
    assert "why-it-works.md" in citing


def test_the_type_write_lands_even_though_the_move_is_planned_first(bundle_root: Path) -> None:
    """Spec §5.1: `MovePlan.digests` is over the body, so a frontmatter edit
    cannot make the move plan stale."""
    bundle = load_bundle(bundle_root)
    plan = plan_retype(bundle, schema_set(), "explanations/why-it-works", "Tutorial")
    result = apply_retype(bundle, plan)
    assert result.type_written is True
    assert result.move.ok, result.move.failed
    assert load(bundle_root / "tutorials" / "why-it-works.md").fm.type == "Tutorial"


def test_a_page_that_is_not_a_member_is_refused(bundle_root: Path) -> None:
    plan = plan_retype(load_bundle(bundle_root), schema_set(), "explanations/nope", "Reference")
    assert plan.ok is False
    assert [refusal.kind for refusal in plan.refusals] == ["not-a-member"]


def test_a_type_outside_the_rubric_is_refused(bundle_root: Path) -> None:
    plan = plan_retype(load_bundle(bundle_root), schema_set(), "explanations/why-it-works", "Concept")
    assert plan.ok is False
    assert [refusal.kind for refusal in plan.refusals] == ["unknown-type"]


def test_a_rubric_type_with_no_installed_schema_is_refused(bundle_root: Path, tmp_path: Path) -> None:
    partial = tmp_path / "schema"
    partial.mkdir()
    (partial / "Explanation.schema.json").write_text(
        '{"$schema": "https://json-schema.org/draft/2020-12/schema",'
        ' "properties": {"type": {"const": "Explanation"}},'
        ' "x-okf-directory": "explanations/"}',
        encoding="utf-8",
    )
    from okf_ext.schemas import load_schemas

    plan = plan_retype(load_bundle(bundle_root), load_schemas(partial), "explanations/why-it-works", "Reference")
    assert plan.ok is False
    assert [refusal.kind for refusal in plan.refusals] == ["undeclared-type"]


def test_retyping_to_the_type_it_already_is_is_refused(bundle_root: Path) -> None:
    plan = plan_retype(load_bundle(bundle_root), schema_set(), "explanations/why-it-works", "Explanation")
    assert plan.ok is False
    assert [refusal.kind for refusal in plan.refusals] == ["same-type"]


def test_the_moves_own_refusals_stay_on_the_move_and_still_sink_ok(bundle_root: Path) -> None:
    """A destination that already exists is `moves`' `dest-exists`, not ours."""
    (bundle_root / "references" / "why-it-works.md").write_text(
        "---\ntype: Reference\ntitle: Taken\ndescription: Taken.\n---\n\n## Summary\n\nWords.\n",
        encoding="utf-8",
    )
    bundle = load_bundle(bundle_root)
    plan = plan_retype(bundle, schema_set(), "explanations/why-it-works", "Reference")
    assert plan.refusals == ()
    assert [refusal.kind for refusal in plan.move.refusals] == ["dest-exists"]
    assert plan.ok is False
    assert "dest-exists" in plan.diff()


def test_a_serialize_error_from_save_is_reported_as_serialize_error(
    bundle_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`document.save()` raising a content-shaped exception is `serialize-error`,
    not `commit-error` -- the split `okf_ext.writing.FailureKind` draws between
    "raised by the document itself" and "a disk-commit failure". `Document` is
    `slots=True`, so the replacement lands on the class, not the instance."""
    bundle = load_bundle(bundle_root)
    plan = plan_retype(bundle, schema_set(), "explanations/why-it-works", "Reference")
    assert plan.ok, plan.diff()

    document = bundle.concepts["explanations/why-it-works"]

    def _boom(self: object, path: Path | None = None) -> None:
        raise ValueError("boom")

    monkeypatch.setattr(type(document), "save", _boom)

    result = apply_retype(bundle, plan)
    assert result.type_written is False
    assert len(result.move.failed) == 1
    failure = result.move.failed[0]
    assert failure.kind == "serialize-error"
    assert "boom" in failure.error


def test_a_commit_error_from_save_is_reported_as_commit_error(
    bundle_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `OSError` from `document.save()` -- a disk-commit failure -- stays
    `commit-error`, worth retrying as-is."""
    bundle = load_bundle(bundle_root)
    plan = plan_retype(bundle, schema_set(), "explanations/why-it-works", "Reference")
    assert plan.ok, plan.diff()

    document = bundle.concepts["explanations/why-it-works"]

    def _boom(self: object, path: Path | None = None) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(type(document), "save", _boom)

    result = apply_retype(bundle, plan)
    assert result.type_written is False
    assert len(result.move.failed) == 1
    failure = result.move.failed[0]
    assert failure.kind == "commit-error"
    assert "disk full" in failure.error


def test_applying_a_refused_plan_raises(bundle_root: Path) -> None:
    bundle = load_bundle(bundle_root)
    plan = plan_retype(bundle, schema_set(), "explanations/why-it-works", "Explanation")
    with pytest.raises(ValueError, match="refused plan"):
        apply_retype(bundle, plan)


def test_diff_renders_and_writes_nothing(bundle_root: Path) -> None:
    bundle = load_bundle(bundle_root)
    ok_plan = plan_retype(bundle, schema_set(), "explanations/why-it-works", "Reference")
    assert "references/why-it-works.md" in ok_plan.diff()
    assert (bundle_root / "explanations" / "why-it-works.md").is_file()

    refused = plan_retype(bundle, schema_set(), "explanations/why-it-works", "Explanation")
    assert "same-type" in refused.diff()


def test_a_wikilink_only_vault_and_a_quiet_vault_produce_different_retype_plans(tmp_path):
    """`retype` has no CLI caller, so the `diff()` line is its whole surface --
    already there the day a command appears (2026-08-21 spec §4.4)."""
    citing = (
        "---\ntype: Reference\ntitle: Flags\ndescription: Flags.\n---\n\n"
        "## Summary\n\nSee [[explanations/why-it-works]] for the reason.\n"
    )
    quiet = "---\ntype: Reference\ntitle: Flags\ndescription: Flags.\n---\n\n## Summary\n\nNothing points anywhere.\n"

    plans = []
    for name, citing_text in (("loud", citing), ("quiet", quiet)):
        root = tmp_path / name
        build_bundle(
            root,
            {
                "explanations/why-it-works": _page("Explanation", "Why it works", "explanations/why-it-works"),
                "reference/flags": citing_text,
            },
        )
        plans.append(plan_retype(load_bundle(root), schema_set(), "explanations/why-it-works", "Tutorial"))

    loud, quiet_plan = plans
    assert loud.ok and quiet_plan.ok
    assert [entry.target for entry in loud.move.stranded] == ["explanations/why-it-works.md"]
    assert quiet_plan.move.stranded == ()
    assert loud.diff() != quiet_plan.diff()
    assert "1 inbound [[wikilink]]" in loud.diff()
    assert "OKF markdown references repaired:" in quiet_plan.diff()


def test_retype_registers_no_cli_command():
    """Spec §6: `retype` gains the render line and nothing else."""
    from doc_wiki_okf import cli

    assert "retype" not in {command.name for command in cli.app.registered_commands}
