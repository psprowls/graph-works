from __future__ import annotations

from pathlib import Path

import pytest
from test_checkpoints import VALID as CHECKPOINT_DRAFT
from work_helpers import lane_report, write_item
from work_tracker_okf import checkpoints
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref

_EPIC = "work/epic-ledger-owner"
_CHILD = f"{_EPIC}/children/feature-ledger-child"
_CHILD2 = f"{_EPIC}/children/feature-ledger-child-two"


def _codes(root: Path, code: str) -> list[str]:
    return [finding.message for finding in lane_report(root).findings if finding.code == code]


def _epic(root: Path, *, phase: str = "execute", children: str = "") -> None:
    del children
    write_item(
        root,
        _EPIC,
        f"type: Epic\nstatus: stable\nwork_status: in-progress\nowner: fixture\n"
        f"phase: {phase}\neffort: large\nopened: 2026-07-01\nupdated: 2026-08-01\n",
    )


def _ledger(root: Path, text: str, *, owner: str = _EPIC) -> None:
    path = artifact_ref(owner, MANAGED_ARTIFACTS["decisions"]).path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_an_epic_past_design_with_no_ledger_is_reported(tmp_path: Path) -> None:
    _epic(tmp_path)
    assert _codes(tmp_path, "decisions.ledger-missing")


def test_an_epic_still_at_design_is_not(tmp_path: Path) -> None:
    """A pre-design epic legitimately has no ledger."""
    _epic(tmp_path, phase="design")
    assert _codes(tmp_path, "decisions.ledger-missing") == []


def test_a_present_ledger_clears_the_code(tmp_path: Path) -> None:
    """`has_member` sees `ignored` members, which is how `targets.artifact-missing`
    already resolves a `sources[].resource` under `references/`."""
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n")
    assert _codes(tmp_path, "decisions.ledger-missing") == []


def test_a_non_epic_is_never_asked_for_a_ledger(tmp_path: Path) -> None:
    write_item(
        tmp_path,
        "2026-07-03-bug-no-ledger",
        "type: Bug\nstatus: stable\nwork_status: open\nphase: execute\n"
        "effort: small\nopened: 2026-07-03\nupdated: 2026-08-01\n",
    )
    assert _codes(tmp_path, "decisions.ledger-missing") == []


def test_release_epic_and_feature_are_all_ledger_owners(tmp_path: Path) -> None:
    for path, type_name in (
        ("work/release-r1", "Release"),
        ("work/epic-e1", "Epic"),
        ("work/feature-f1", "Feature"),
    ):
        write_item(
            tmp_path,
            path,
            f"type: {type_name}\nstatus: stable\nwork_status: in-progress\nphase: execute\n"
            "effort: medium\nopened: 2026-07-03\nupdated: 2026-08-01\n",
        )
    assert len(_codes(tmp_path, "decisions.ledger-missing")) == 3


def test_an_unrecognized_status_is_an_error(tmp_path: Path) -> None:
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001 — q\nstatus: maybe\n")
    messages = _codes(tmp_path, "decisions.entry-invalid")
    assert any("maybe" in message for message in messages)


def test_a_missing_status_is_an_error(tmp_path: Path) -> None:
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001 — q\naffects: [a]\n")
    assert any("status" in message for message in _codes(tmp_path, "decisions.entry-invalid"))


def test_a_duplicate_id_is_an_error(tmp_path: Path) -> None:
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001 — a\nstatus: open\n\n## D-001 — b\nstatus: open\n")
    assert any("duplicated" in message for message in _codes(tmp_path, "decisions.entry-invalid"))


def test_a_duplicate_id_is_reported_even_across_the_padded_and_unpadded_spelling(tmp_path: Path) -> None:
    _epic(tmp_path)
    _ledger(tmp_path, "## D-1 — a\nstatus: open\n\n## D-001 — b\nstatus: open\n")
    assert any("duplicated" in message for message in _codes(tmp_path, "decisions.entry-invalid"))


def test_a_gap_in_the_id_sequence_is_an_error(tmp_path: Path) -> None:
    """A gap is a genuine lost-entry signal, because ids are never gap-filled."""
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001 — a\nstatus: open\n\n## D-003 — c\nstatus: open\n")
    assert any("gap" in message for message in _codes(tmp_path, "decisions.entry-invalid"))


def test_a_residual_parser_warning_passes_through_at_warn(tmp_path: Path) -> None:
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001\nstatus: open\n")
    warns = [
        finding
        for finding in lane_report(tmp_path).findings
        if finding.code == "decisions.entry-invalid" and finding.severity == "warn"
    ]
    assert [finding.message for finding in warns] == ["ledger parse warning: D-001: heading has no question text"]


def test_a_question_with_an_invalid_phase_is_an_error(tmp_path: Path) -> None:
    _epic(tmp_path)
    _ledger(tmp_path, f"## D-001 — q\nstatus: open\naffects: [{_EPIC}]\nphase: someday\n")
    findings = [finding for finding in lane_report(tmp_path).findings if finding.code.startswith("decisions.")]
    assert [(finding.code, finding.severity) for finding in findings] == [("decisions.entry-invalid", "error")]
    assert "D-001" in findings[0].message
    assert "phase 'someday'" in findings[0].message


def test_one_bad_entry_is_one_finding_not_two(tmp_path: Path) -> None:
    """The parser warning for an invalid status is filtered out of the `warn`
    passthrough — the sub-check already reported it at `error`."""
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001 — q\nstatus: maybe\n")
    findings = [f for f in lane_report(tmp_path).findings if f.code == "decisions.entry-invalid"]
    assert [f.severity for f in findings] == ["error"]


def test_open_entries_at_finish_are_an_error(tmp_path: Path) -> None:
    _epic(tmp_path, phase="finish")
    _ledger(tmp_path, "## D-001 — q\nstatus: open\n")
    assert any("D-001" in message for message in _codes(tmp_path, "decisions.open-at-finish"))


def test_open_entries_before_finish_are_not(tmp_path: Path) -> None:
    _epic(tmp_path, phase="execute")
    _ledger(tmp_path, "## D-001 — q\nstatus: open\n")
    assert _codes(tmp_path, "decisions.open-at-finish") == []


def test_a_dangling_supersedes_is_an_error(tmp_path: Path) -> None:
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\nsupersedes: D-009\n")
    assert any("does not exist" in message for message in _codes(tmp_path, "decisions.supersedes-invalid"))


def test_a_supersedes_naming_a_live_entry_is_an_error(tmp_path: Path) -> None:
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001 — a\nstatus: open\n\n## D-002 — b\nstatus: answered\nsupersedes: D-001\n")
    assert any("not 'superseded'" in message for message in _codes(tmp_path, "decisions.supersedes-invalid"))


def test_a_well_formed_supersede_pair_is_silent(tmp_path: Path) -> None:
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001 — a\nstatus: superseded\n\n## D-002 — b\nstatus: answered\nsupersedes: D-001\n")
    assert _codes(tmp_path, "decisions.supersedes-invalid") == []


def test_an_unpadded_supersedes_still_resolves(tmp_path: Path) -> None:
    """Mirrors `id_number`'s padded/unpadded unification, so a hand-typed `D-1`
    resolves against `D-001` instead of reporting a phantom dangling reference."""
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001 — a\nstatus: superseded\n\n## D-002 — b\nstatus: answered\nsupersedes: D-1\n")
    assert _codes(tmp_path, "decisions.supersedes-invalid") == []


def test_a_malformed_supersedes_value_is_a_dangling_reference(tmp_path: Path) -> None:
    _epic(tmp_path)
    _ledger(tmp_path, "## D-001 — a\nstatus: answered\nsupersedes: fourteen\n")
    assert any("does not exist" in message for message in _codes(tmp_path, "decisions.supersedes-invalid"))


def _child_with_spec(root: Path, body: str, *, slug: str = _CHILD, opened: str = "2026-07-02") -> None:
    write_item(
        root,
        slug,
        "type: Feature\nstatus: stable\nwork_status: open\nphase: design\n"
        f"effort: medium\nopened: {opened}\nupdated: 2026-08-01\n"
        "sources:\n"
        "  - id: design\n"
        f"    resource: /{slug}/references/01-design.md\n"
        "    title: Design spec\n",
    )
    spec = root / slug / "references" / "01-design.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(body, encoding="utf-8")


def test_a_child_spec_citing_an_absent_id_is_an_error(tmp_path: Path) -> None:
    """The rule runs over any item with a resolvable epic ancestor, not only
    epics — a child's spec is what cites the parent's decisions."""
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n")
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n", owner=_CHILD)
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-009.\n")
    assert any("D-009" in message for message in _codes(tmp_path, "decisions.cite-missing"))


def test_a_child_spec_citing_a_present_id_is_silent(tmp_path: Path) -> None:
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n")
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n", owner=_CHILD)
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-001.\n")
    assert _codes(tmp_path, "decisions.cite-missing") == []


def test_an_unpadded_citation_still_resolves_against_the_padded_ledger_entry(tmp_path: Path) -> None:
    """Mirrors `_supersedes_findings`'s own padded/unpadded unification: a
    hand-typed `D-1` in a design spec must resolve against the ledger's
    zero-padded `D-001`, not report a phantom missing citation."""
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n")
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n", owner=_CHILD)
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-1.\n")
    assert _codes(tmp_path, "decisions.cite-missing") == []


def test_a_parent_capable_item_with_no_ancestor_uses_its_own_ledger(tmp_path: Path) -> None:
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-009.\n")
    assert any("D-009" in message for message in _codes(tmp_path, "decisions.cite-missing"))


def test_an_absent_spec_file_is_not_citation_checked(tmp_path: Path) -> None:
    """Tolerant on the read: an unreadable or absent spec is simply not checked,
    same as an unstamped one."""
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n")
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n", owner=_CHILD)
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-009.\n")
    (tmp_path / _CHILD / "references" / "01-design.md").unlink()
    assert _codes(tmp_path, "decisions.cite-missing") == []


def test_an_item_with_no_design_spec_source_is_not_citation_checked(tmp_path: Path) -> None:
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n")
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n")
    write_item(
        tmp_path,
        _CHILD,
        "type: Feature\nstatus: stable\nwork_status: open\nphase: design\n"
        "effort: medium\nopened: 2026-07-02\nupdated: 2026-08-01\n",
    )
    assert _codes(tmp_path, "decisions.cite-missing") == []


def test_a_second_child_under_the_same_epic_reuses_the_cached_ledger(tmp_path: Path) -> None:
    """The rule loads an epic's ledger once per report and caches it by slug —
    a second child sharing the same epic must see the same (correct) id set,
    not a stale or empty one, whether the id it cites is present or absent."""
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n  - {_CHILD2}\n")
    _ledger(tmp_path, "## D-001 — a\nstatus: answered\n", owner=_CHILD)
    _ledger(tmp_path, "## D-002 — b\nstatus: answered\n", owner=_CHILD2)
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-001.\n")
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-002 and D-009.\n", slug=_CHILD2, opened="2026-07-03")
    messages = _codes(tmp_path, "decisions.cite-missing")
    assert len(messages) == 1
    assert "D-009" in messages[0]


def test_a_self_owned_leaf_ledger_is_linted(tmp_path: Path) -> None:
    leaf = "work/bug-leaf"
    write_item(
        tmp_path,
        leaf,
        "type: Bug\nstatus: stable\nwork_status: in-progress\nphase: execute\n"
        "effort: small\nopened: 2026-07-03\nupdated: 2026-08-01\n",
    )
    attachment = artifact_ref(leaf, MANAGED_ARTIFACTS["decisions"]).path(tmp_path)
    attachment.parent.mkdir(parents=True)
    attachment.write_text("## D-001 — q\nstatus: maybe\n", encoding="utf-8")
    assert any("maybe" in m for m in _codes(tmp_path, "decisions.entry-invalid"))


def test_an_epic_citing_its_own_ledger_is_checked_too(tmp_path: Path) -> None:
    """An epic is its own nearest epic, so its spec is citation-checked against
    the ledger it owns."""
    write_item(
        tmp_path,
        _EPIC,
        "type: Epic\nstatus: stable\nwork_status: in-progress\nowner: fixture\n"
        "phase: execute\neffort: large\nopened: 2026-07-01\nupdated: 2026-08-01\n"
        "sources:\n"
        "  - id: design\n"
        f"    resource: /{_EPIC}/references/01-design.md\n"
        "    title: Design spec\n",
    )
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n")
    spec = tmp_path / _EPIC / "references" / "01-design.md"
    spec.write_text("# Spec\n\nSee D-009.\n", encoding="utf-8")
    assert any("D-009" in message for message in _codes(tmp_path, "decisions.cite-missing"))


_LONE = "work/bug-lone-holder"
_PARK_REF = f"/{_LONE}/references/03-execute-checkpoint-D-001.md"


def _lone(root: Path, *, phase: str = "execute") -> None:
    write_item(
        root,
        _LONE,
        f"type: Bug\nstatus: stable\nwork_status: in-progress\nowner: fixture\nphase: {phase}\n"
        "effort: medium\nopened: 2026-07-01\nupdated: 2026-08-01\n",
    )


def _checkpoint(root: Path, decision_id: str = "D-001", phase: str = "execute") -> None:
    text = checkpoints.stamp(CHECKPOINT_DRAFT.replace("work/epic-a/children/feature-b", _LONE), decision_id)
    target = root / f"{_LONE}/references/03-execute-checkpoint-{decision_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.replace("phase: execute", f"phase: {phase}"), encoding="utf-8", newline="")


def test_a_valid_park_on_a_self_owned_ledger_is_clean(tmp_path: Path) -> None:
    _lone(tmp_path)
    _ledger(
        tmp_path,
        f"## D-001 — q\nstatus: open\naffects: [{_LONE}]\nhold: park\nphase: execute\ncheckpoint: {_PARK_REF}\n",
        owner=_LONE,
    )
    _checkpoint(tmp_path)
    for code in (
        "decisions.hold-invalid",
        "decisions.hold-phase-stale",
        "decisions.checkpoint-invalid",
        "decisions.entry-invalid",
        "decisions.ledger-missing",
    ):
        assert _codes(tmp_path, code) == [], code


def test_a_lone_item_without_a_ledger_is_normal(tmp_path: Path) -> None:
    _lone(tmp_path)
    assert _codes(tmp_path, "decisions.ledger-missing") == []


@pytest.mark.parametrize(
    "entry",
    [
        "hold: pause\nphase: execute\n",
        "hold: park\nphase: execute\n",  # park without checkpoint
        f"hold: skip\nphase: execute\ncheckpoint: {_PARK_REF}\n",  # skip with checkpoint
        "hold: skip\n",  # missing phase
        "hold: skip\nphase: someday\n",  # phase outside HOLD_PHASES
        f"hold: park\nphase: entry\ncheckpoint: {_PARK_REF}\n",  # park at entry
    ],
)
def test_malformed_holds_are_errors(tmp_path: Path, entry: str) -> None:
    _lone(tmp_path)
    _ledger(tmp_path, f"## D-001 — q\nstatus: open\naffects: [{_LONE}]\n{entry}", owner=_LONE)
    _checkpoint(tmp_path)
    assert _codes(tmp_path, "decisions.hold-invalid")
    assert not [m for m in _codes(tmp_path, "decisions.entry-invalid") if "invalid hold" in m or "invalid phase" in m]


def test_a_hold_naming_two_items_is_an_error(tmp_path: Path) -> None:
    _lone(tmp_path)
    _ledger(
        tmp_path,
        f"## D-001 — q\nstatus: open\naffects: [{_LONE}, work/other]\nhold: skip\nphase: execute\n",
        owner=_LONE,
    )
    assert _codes(tmp_path, "decisions.hold-invalid")


def test_answered_history_keeps_its_hold_keys_validly(tmp_path: Path) -> None:
    _lone(tmp_path, phase="finish")
    _ledger(
        tmp_path,
        f"## D-001 — q\nstatus: answered\naffects: [{_LONE}]\ndecided: 2026-09-01 by user\n"
        f"hold: park\nphase: execute\ncheckpoint: {_PARK_REF}\n\n**Answer:** go\n",
        owner=_LONE,
    )
    _checkpoint(tmp_path)
    assert _codes(tmp_path, "decisions.hold-invalid") == []
    assert _codes(tmp_path, "decisions.hold-phase-stale") == []  # stale is judged on open holds only


def test_an_open_hold_at_a_stale_phase_warns(tmp_path: Path) -> None:
    _lone(tmp_path, phase="finish")
    _ledger(tmp_path, f"## D-001 — q\nstatus: open\naffects: [{_LONE}]\nhold: skip\nphase: execute\n", owner=_LONE)
    messages = _codes(tmp_path, "decisions.hold-phase-stale")
    assert messages and "finish" in messages[0]


def test_missing_or_invalid_checkpoints_are_errors(tmp_path: Path) -> None:
    _lone(tmp_path)
    _ledger(
        tmp_path,
        f"## D-001 — q\nstatus: open\naffects: [{_LONE}]\nhold: park\nphase: execute\ncheckpoint: {_PARK_REF}\n",
        owner=_LONE,
    )
    assert any("missing" in m for m in _codes(tmp_path, "decisions.checkpoint-invalid"))
    _checkpoint(tmp_path, phase="plan")
    assert any("phase" in m for m in _codes(tmp_path, "decisions.checkpoint-invalid"))


@pytest.mark.parametrize(
    "resource",
    [
        _PARK_REF.removeprefix("/"),
        f"/{_LONE}/references/../../../outside.md",
        f"/{_LONE}/references/../references/03-execute-checkpoint-D-001.md",
        f"/{_LONE}/references/03-execute-checkpoint-D-002.md",
        f"/{_LONE}/references/02-plan-checkpoint-D-001.md",
        f"/{_LONE}/references/checkpoint.md",
        f"/{_PARK_REF}",
    ],
)
def test_checkpoint_resources_must_be_canonical_before_reading(tmp_path: Path, resource: str, monkeypatch) -> None:
    _lone(tmp_path)
    _ledger(
        tmp_path,
        f"## D-001 — q\nstatus: open\naffects: [{_LONE}]\nhold: park\nphase: execute\ncheckpoint: {resource}\n",
        owner=_LONE,
    )
    _checkpoint(tmp_path)
    read_text = Path.read_text

    def guarded_read(path, *args, **kwargs):
        if path.name != "00-decisions.md" and ("checkpoint" in path.name or path.name == "outside.md"):
            pytest.fail(f"read an invalid checkpoint resource: {path}")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    assert _codes(tmp_path, "decisions.checkpoint-invalid")


@pytest.mark.parametrize(
    "phase, affects",
    [
        ("entry", _LONE),
        ("someday", _LONE),
        ("", _LONE),
        ("execute", ""),
        ("execute", f"{_LONE}, work/other"),
        ("execute", "../outside"),
        ("execute", "/work/bug-lone-holder"),
    ],
)
def test_bad_checkpoint_identity_is_reported_tolerantly(tmp_path: Path, phase: str, affects: str) -> None:
    _lone(tmp_path)
    _ledger(
        tmp_path,
        f"## D-001 — q\nstatus: open\naffects: [{affects}]\nhold: park\nphase: {phase}\ncheckpoint: {_PARK_REF}\n",
        owner=_LONE,
    )
    _checkpoint(tmp_path)
    assert _codes(tmp_path, "decisions.checkpoint-invalid")


@pytest.mark.parametrize("directory_link", [False, True])
def test_checkpoint_symlinks_outside_the_bundle_are_rejected(tmp_path: Path, directory_link: bool, monkeypatch) -> None:
    root = tmp_path / "bundle"
    _lone(root)
    _ledger(
        root,
        f"## D-001 — q\nstatus: open\naffects: [{_LONE}]\nhold: park\nphase: execute\ncheckpoint: {_PARK_REF}\n",
        owner=_LONE,
    )
    outside = tmp_path / "outside"
    _checkpoint(outside)
    target = outside / _PARK_REF.removeprefix("/")
    link = root / _PARK_REF.removeprefix("/")
    if directory_link:
        # Keep the ledger at its usual path; only the held item's owned
        # checkpoint directory points outside the bundle.
        _epic(root)
        _ledger(root, (root / _LONE / "references/00-decisions.md").read_text(encoding="utf-8"))
        (root / _LONE / "references/00-decisions.md").unlink()
        link.parent.rmdir()
        link, target = link.parent, target.parent
    try:
        link.symlink_to(target, target_is_directory=directory_link)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")
    read_text = Path.read_text

    def guarded_read(path, *args, **kwargs):
        if path.name.endswith("checkpoint-D-001.md"):
            pytest.fail(f"read an outside checkpoint: {path}")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    assert any("outside" in m for m in _codes(root, "decisions.checkpoint-invalid"))


@pytest.mark.parametrize("content", [b"\xff", b"not a checkpoint"])
def test_unreadable_or_malformed_checkpoint_content_is_reported(tmp_path: Path, content: bytes) -> None:
    _lone(tmp_path)
    _ledger(
        tmp_path,
        f"## D-001 — q\nstatus: open\naffects: [{_LONE}]\nhold: park\nphase: execute\ncheckpoint: {_PARK_REF}\n",
        owner=_LONE,
    )
    (tmp_path / _PARK_REF.removeprefix("/")).write_bytes(content)
    assert _codes(tmp_path, "decisions.checkpoint-invalid")
