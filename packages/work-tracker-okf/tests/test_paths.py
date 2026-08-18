from pathlib import Path

import pytest
from work_tracker_okf import vocabulary
from work_tracker_okf.paths import (
    LEDGER_FILENAME,
    PHASE_ORDINALS,
    ArtifactRef,
    artifact_path,
    decisions_ledger,
    item_page,
    references_dir,
    source_id_for,
)

_SLUG = "2026-03-02-epic-feature-filing-writer"

#: Valid (phase, kind) combinations per the SOURCE_ID_PATTERN. spec is only in
#: design phase, plan is only in plan phase, and guidance/results/transcript are
#: in any phase. §2.3's round trip is asserted over these.
_PRODUCT = [
    (phase, kind)
    for phase in vocabulary.ARTIFACT_PHASES
    for kind in sorted(vocabulary.ARTIFACT_KINDS)
    if not (kind == "spec" and phase != "design") and not (kind == "plan" and phase != "plan")
]


def test_phase_ordinals_are_derived_not_retyped() -> None:
    """C2-D: work-io hand-wrote the map and carried a synthetic `open: "00"`."""
    assert PHASE_ORDINALS == {"design": "01", "plan": "02", "execute": "03", "finish": "04"}
    assert tuple(PHASE_ORDINALS) == vocabulary.ARTIFACT_PHASES
    assert "00" not in set(PHASE_ORDINALS.values())


def test_the_three_spellings_agree(tmp_path: Path) -> None:
    ref = artifact_path(_SLUG, "design", "spec")
    assert ref.rel == f"work/{_SLUG}/references/01-design-spec.md"
    assert ref.resource == f"/work/{_SLUG}/references/01-design-spec.md"
    assert ref.resource == f"/{ref.rel}"
    assert ref.path(tmp_path) == tmp_path / ref.rel


def test_the_carrier_is_frozen() -> None:
    ref = artifact_path(_SLUG, "design", "spec")
    with pytest.raises(AttributeError):  # FrozenInstanceError is a subclass of AttributeError
        ref.rel = "elsewhere"  # type: ignore[misc]


def test_item_page_and_references_dir_carry_no_source_id() -> None:
    """An item page is not an artifact, but it is a bundle path with the same
    three spellings and the same confusability (C2-B)."""
    assert item_page(_SLUG) == ArtifactRef(rel=f"work/{_SLUG}.md")
    assert item_page(_SLUG).source_id is None
    assert references_dir(_SLUG).rel == f"work/{_SLUG}/references"
    assert references_dir(_SLUG).source_id is None


def test_the_archived_form_differs_by_one_prefix_and_nothing_else() -> None:
    assert item_page(_SLUG, archived=True).rel == f"work/_archive/{_SLUG}.md"
    assert references_dir(_SLUG, archived=True).rel == f"work/_archive/{_SLUG}/references"
    active = artifact_path(_SLUG, "plan", "plan")
    archived = artifact_path(_SLUG, "plan", "plan", archived=True)
    assert archived.rel == active.rel.replace("work/", "work/_archive/", 1)
    assert archived.source_id == active.source_id


@pytest.mark.parametrize(("phase", "kind"), _PRODUCT)
def test_every_id_in_the_product_satisfies_child_ones_pattern(phase, kind) -> None:
    """§2.3's round trip: two vocabularies have to agree, and this is where."""
    assert vocabulary.is_source_id(source_id_for(phase, kind))


@pytest.mark.parametrize(("phase", "kind"), _PRODUCT)
def test_artifact_path_never_returns_a_ref_without_an_id(phase, kind) -> None:
    """A caller cannot obtain a resource without the matching id (C2-C)."""
    ref = artifact_path(_SLUG, phase, kind)
    assert ref.source_id is not None
    assert vocabulary.is_source_id(ref.source_id)


def test_the_two_shipped_literals_win_over_the_rule() -> None:
    assert source_id_for("design", "spec") == vocabulary.SPEC_SOURCE_ID == "design-spec"
    assert source_id_for("plan", "plan") == vocabulary.PLAN_SOURCE_ID == "plan"
    assert source_id_for("plan", "plan") != "plan-plan"


def test_the_id_flips_the_filenames_order() -> None:
    """Shipped and not relitigated: the filename is `<phase>-<kind>`, the id is
    `<kind>-<phase>`. One function holding both is what keeps them from drifting."""
    ref = artifact_path(_SLUG, "execute", "results")
    assert ref.rel.endswith("03-execute-results.md")
    assert ref.source_id == "results-execute"


def test_the_suffix_lands_on_both_the_filename_and_the_id() -> None:
    ref = artifact_path(_SLUG, "plan", "transcript", suffix="subagent-1")
    assert ref.rel.endswith("02-plan-transcript-subagent-1.md")
    assert ref.source_id == "transcript-plan-subagent-1"
    assert vocabulary.is_source_id(ref.source_id)


def test_the_extension_is_a_parameter_and_transcripts_land_flat() -> None:
    """C2-D: flat in `references/`, not under `references/transcripts/`."""
    ref = artifact_path(_SLUG, "execute", "transcript", ext="jsonl")
    assert ref.rel == f"work/{_SLUG}/references/03-execute-transcript.jsonl"


@pytest.mark.parametrize("phase", ["open", "done", "", "Design", "designs"])
def test_an_unknown_phase_raises(phase) -> None:
    """C2-L: caller error, the same class as child 1's `InitError`."""
    with pytest.raises(ValueError, match="unknown phase"):
        artifact_path(_SLUG, phase, "spec")
    with pytest.raises(ValueError, match="unknown phase"):
        source_id_for(phase, "spec")


@pytest.mark.parametrize("kind", ["notes", "", "Spec", "specs"])
def test_an_unknown_kind_raises(kind) -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        artifact_path(_SLUG, "design", kind)
    with pytest.raises(ValueError, match="unknown kind"):
        source_id_for("design", kind)


@pytest.mark.parametrize(("phase", "kind"), [("design", "spec"), ("plan", "plan")])
def test_the_two_literals_refuse_a_suffix(phase, kind) -> None:
    """P-1: the shipped pattern has no legal suffixed form for either literal, so
    honouring the suffix is impossible and dropping it would give two resources
    one id — the mismatch C2-C exists to prevent."""
    with pytest.raises(ValueError, match="cannot carry a suffix"):
        source_id_for(phase, kind, "draft")
    with pytest.raises(ValueError, match="cannot carry a suffix"):
        artifact_path(_SLUG, phase, kind, suffix="draft")


@pytest.mark.parametrize(
    ("phase", "kind"),
    [
        ("design", "plan"),
        ("execute", "spec"),
        ("plan", "spec"),
        ("execute", "plan"),
        ("finish", "spec"),
        ("finish", "plan"),
    ],
)
def test_spec_and_plan_only_valid_in_their_phases(phase, kind) -> None:
    """spec is only valid in design phase, plan is only valid in plan phase."""
    with pytest.raises(ValueError, match="is only valid with phase"):
        source_id_for(phase, kind)
    with pytest.raises(ValueError, match="is only valid with phase"):
        artifact_path(_SLUG, phase, kind)


def test_an_empty_suffix_is_no_suffix() -> None:
    assert source_id_for("design", "spec", "") == "design-spec"
    assert artifact_path(_SLUG, "plan", "guidance", suffix="").rel.endswith("02-plan-guidance.md")


def test_the_ledger_sits_under_references_beside_the_other_artifacts() -> None:
    """§3.1: inside `references/`, so `IGNORE`'s existing `*/references/*`
    already covers it — no new ignore pattern, no `test_ignore.py` churn."""
    assert decisions_ledger(_SLUG).rel == f"work/{_SLUG}/references/00-decisions.md"
    assert decisions_ledger(_SLUG).resource == f"/work/{_SLUG}/references/00-decisions.md"


def test_the_archived_ledger_is_the_symmetric_twin() -> None:
    assert decisions_ledger(_SLUG, archived=True).rel == f"work/_archive/{_SLUG}/references/00-decisions.md"


def test_the_ledger_carries_no_source_id() -> None:
    """§3.2: the epic page does not stamp its ledger into `sources[]`, so there
    is no id to carry — `references_dir`'s `.source_id` is `None` for the same reason."""
    assert decisions_ledger(_SLUG).source_id is None


def test_the_ledger_filename_is_the_module_constant() -> None:
    assert LEDGER_FILENAME == "00-decisions.md"
    assert decisions_ledger(_SLUG).rel.endswith(LEDGER_FILENAME)
