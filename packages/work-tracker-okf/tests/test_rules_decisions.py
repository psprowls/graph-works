from __future__ import annotations

from pathlib import Path

from work_helpers import lane_report, write_item
from work_tracker_okf.paths import decisions_ledger

_EPIC = "2026-07-01-epic-ledger-owner"
_CHILD = "2026-07-02-epic-feature-ledger-child"
_CHILD2 = "2026-07-03-epic-feature-ledger-child-two"


def _codes(root: Path, code: str) -> list[str]:
    return [finding.message for finding in lane_report(root).findings if finding.code == code]


def _epic(root: Path, *, phase: str = "execute", children: str = "") -> None:
    write_item(
        root,
        _EPIC,
        f"type: Epic\nstatus: stable\nworkflow_status: in-progress\nowner: fixture\n"
        f"phase: {phase}\neffort: large\nopened: 2026-07-01\nupdated: 2026-08-01\n{children}",
    )


def _ledger(root: Path, text: str, *, slug: str = _EPIC) -> None:
    path = decisions_ledger(slug).path(root)
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
        "type: Bug\nstatus: stable\nworkflow_status: open\nphase: execute\n"
        "effort: small\nopened: 2026-07-03\nupdated: 2026-08-01\n",
    )
    assert _codes(tmp_path, "decisions.ledger-missing") == []


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
        "type: Feature\nstatus: stable\nworkflow_status: open\nphase: design\n"
        f"effort: medium\nopened: {opened}\nupdated: 2026-08-01\n"
        f"parent: {_EPIC}\n"
        "sources:\n"
        "  - id: design-spec\n"
        f"    resource: /work/{slug}/references/01-design-spec.md\n"
        "    title: Design spec\n",
    )
    spec = root / "work" / slug / "references" / "01-design-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(body, encoding="utf-8")


def test_a_child_spec_citing_an_absent_id_is_an_error(tmp_path: Path) -> None:
    """The rule runs over any item with a resolvable epic ancestor, not only
    epics — a child's spec is what cites the parent's decisions."""
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n")
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n")
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-009.\n")
    assert any("D-009" in message for message in _codes(tmp_path, "decisions.cite-missing"))


def test_a_child_spec_citing_a_present_id_is_silent(tmp_path: Path) -> None:
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n")
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n")
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-001.\n")
    assert _codes(tmp_path, "decisions.cite-missing") == []


def test_an_unpadded_citation_still_resolves_against_the_padded_ledger_entry(tmp_path: Path) -> None:
    """Mirrors `_supersedes_findings`'s own padded/unpadded unification: a
    hand-typed `D-1` in a design spec must resolve against the ledger's
    zero-padded `D-001`, not report a phantom missing citation."""
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n")
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n")
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-1.\n")
    assert _codes(tmp_path, "decisions.cite-missing") == []


def test_an_item_with_no_epic_ancestor_is_not_citation_checked(tmp_path: Path) -> None:
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-009.\n")
    assert _codes(tmp_path, "decisions.cite-missing") == []


def test_an_absent_spec_file_is_not_citation_checked(tmp_path: Path) -> None:
    """Tolerant on the read: an unreadable or absent spec is simply not checked,
    same as an unstamped one."""
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n")
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n")
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-009.\n")
    (tmp_path / "work" / _CHILD / "references" / "01-design-spec.md").unlink()
    assert _codes(tmp_path, "decisions.cite-missing") == []


def test_an_item_with_no_design_spec_source_is_not_citation_checked(tmp_path: Path) -> None:
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n")
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n")
    write_item(
        tmp_path,
        _CHILD,
        "type: Feature\nstatus: stable\nworkflow_status: open\nphase: design\n"
        f"effort: medium\nopened: 2026-07-02\nupdated: 2026-08-01\nparent: {_EPIC}\n",
    )
    assert _codes(tmp_path, "decisions.cite-missing") == []


def test_a_second_child_under_the_same_epic_reuses_the_cached_ledger(tmp_path: Path) -> None:
    """The rule loads an epic's ledger once per report and caches it by slug —
    a second child sharing the same epic must see the same (correct) id set,
    not a stale or empty one, whether the id it cites is present or absent."""
    _epic(tmp_path, children=f"children:\n  - {_CHILD}\n  - {_CHILD2}\n")
    _ledger(tmp_path, "## D-001 — a\nstatus: answered\n\n## D-002 — b\nstatus: answered\n")
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-001.\n")
    _child_with_spec(tmp_path, "# Spec\n\nFollows from D-002 and D-009.\n", slug=_CHILD2, opened="2026-07-03")
    messages = _codes(tmp_path, "decisions.cite-missing")
    assert len(messages) == 1
    assert "D-009" in messages[0]


def test_an_epic_citing_its_own_ledger_is_checked_too(tmp_path: Path) -> None:
    """An epic is its own nearest epic, so its spec is citation-checked against
    the ledger it owns."""
    write_item(
        tmp_path,
        _EPIC,
        "type: Epic\nstatus: stable\nworkflow_status: in-progress\nowner: fixture\n"
        "phase: execute\neffort: large\nopened: 2026-07-01\nupdated: 2026-08-01\n"
        "sources:\n"
        "  - id: design-spec\n"
        f"    resource: /work/{_EPIC}/references/01-design-spec.md\n"
        "    title: Design spec\n",
    )
    _ledger(tmp_path, "## D-001 — q\nstatus: answered\n")
    spec = tmp_path / "work" / _EPIC / "references" / "01-design-spec.md"
    spec.write_text("# Spec\n\nSee D-009.\n", encoding="utf-8")
    assert any("D-009" in message for message in _codes(tmp_path, "decisions.cite-missing"))
