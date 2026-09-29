"""Receipt v2 reads strictly and projects v1 entries as verified ancestry."""

from datetime import date

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.finish import VerifiedIntegration, read_finish_receipt, receipt_entry_data

OWNER = "work/feature-x"
A, B, C = "a" * 40, "b" * 40, "c" * 40
BASE = [
    "  - repo: code",
    "    source_branch: feature",
    f"    source_commit: {A}",
    "    target_branch: main",
    f"    result_commit: {B}",
]


def write(tmp_path, lines, version=2):
    layout = apply_init(plan_init(tmp_path / "ws", today=date(2026, 9, 28), topic="Receipt")).layout
    path = layout.bundle_dir / OWNER / "references/04-finish-receipt.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Explanation\nreceipt_version: {version}\nowner: {OWNER}\nintegrations:\n"
        + "\n".join(lines)
        + "\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    return layout


def test_v1_entry_reads_as_verified_ancestry(tmp_path):
    _doc, entries, error = read_finish_receipt(write(tmp_path, BASE, version=1), OWNER)

    assert error is None
    assert entries == (VerifiedIntegration("code", "feature", A, "main", B, "ancestry", "verified", None, None, None),)


def test_v2_squash_and_attested_entries_read(tmp_path):
    lines = [
        *BASE,
        "    strategy: squash",
        "    evidence: verified",
        f"    target_before: {C}",
        "  - repo: ui",
        "    source_branch: feature",
        f"    source_commit: {A}",
        "    target_branch: main",
        f"    result_commit: {C}",
        "    strategy: attested",
        "    evidence: accepted",
        "    accepted_by: pat",
        "    reason: squashed upstream in PR 12",
    ]

    _doc, entries, error = read_finish_receipt(write(tmp_path, lines), OWNER)

    assert error is None
    assert [(e.strategy, e.evidence, e.target_before, e.accepted_by, e.reason) for e in entries] == [
        ("squash", "verified", C, None, None),
        ("attested", "accepted", None, "pat", "squashed upstream in PR 12"),
    ]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["    strategy: rebase", "    evidence: verified"], "malformed finish receipt integration"),
        (["    strategy: [squash]"], "malformed finish receipt integration"),
        (
            ["    strategy: squash", "    evidence: bogus", f"    target_before: {C}"],
            "malformed finish receipt integration",
        ),
        (
            ["    strategy: squash", "    evidence: accepted", f"    target_before: {C}"],
            "malformed finish receipt integration",
        ),
        (["    strategy: attested", "    evidence: verified"], "malformed finish receipt integration"),
        (
            ["    strategy: attested", "    evidence: accepted", "    accepted_by: pat"],
            "malformed finish receipt attribution",
        ),
        (
            ["    strategy: attested", "    evidence: accepted", "    accepted_by: pat", "    reason: '  '"],
            "malformed finish receipt attribution",
        ),
        (
            ["    strategy: merge", "    evidence: verified", f"    target_before: {C}", "    reason: why"],
            "malformed finish receipt attribution",
        ),
        (["    strategy: squash", "    evidence: verified"], "malformed finish receipt target_before"),
        (
            ["    strategy: ancestry", "    evidence: verified", f"    target_before: {C}"],
            "malformed finish receipt target_before",
        ),
        (
            ["    strategy: merge", "    evidence: verified", "    target_before: HEAD"],
            "malformed finish receipt target_before",
        ),
    ],
)
def test_malformed_v2_entries_refuse(tmp_path, extra, message):
    _doc, entries, error = read_finish_receipt(write(tmp_path, [*BASE, *extra]), OWNER)

    assert (entries, error) == ((), message)


@pytest.mark.parametrize("version", ["3", "0", "true", "'2'"])
def test_unknown_receipt_version_refuses(tmp_path, version):
    assert read_finish_receipt(write(tmp_path, BASE, version=version), OWNER)[2] == "malformed finish receipt"


def test_entry_data_omits_absent_optional_fields():
    assert receipt_entry_data(VerifiedIntegration("code", "feature", A, "main", B)) == {
        "repo": "code",
        "source_branch": "feature",
        "source_commit": A,
        "target_branch": "main",
        "result_commit": B,
        "strategy": "ancestry",
        "evidence": "verified",
    }
