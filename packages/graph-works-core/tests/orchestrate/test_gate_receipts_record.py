"""Appending a gate run to the owner's receipt through gw's committed mutation path."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import date

import pytest
from _transaction_helpers import _init_git
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate.gate_receipts import GateRun, parse_gate_receipt, record_gate_run

TODAY = date(2026, 9, 28)
OWNER = "work/feature-example"
RUN = GateRun(
    run_id="20260928T120000Z-0a1b2c3d",
    repo="code",
    worktree="/w",
    head="b" * 40,
    tree="a" * 40,
    clean=True,
    tree_changed=False,
    scope="full",
    command="just check",
    names=(),
    exit=0,
    log_path="/w/.git/gw-gate/x/20260928T120000Z-0a1b2c3d.log",
    log_tail="ok",
    started="2026-09-28T12:00:00Z",
    duration_s=1.5,
)


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def setup(tmp_path, *, work_status="in-progress", phase="execute"):
    root = tmp_path / "workspace"
    root.mkdir()
    layout = apply_init(plan_init(root, today=TODAY, topic="Gate")).layout
    page = layout.bundle_dir / (OWNER + ".md")
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f"---\ntype: Feature\ntitle: Example\nwork_status: {work_status}\nphase: {phase}\nowner: someone\n---\n\n"
        "Authored content.\n",
        encoding="utf-8",
        newline="\n",
    )
    _init_git(layout.root)
    return layout, OWNER


def receipt_file(layout, owner):
    return layout.bundle_dir / owner / "references" / "03-gate-receipts.md"


def test_first_run_creates_registers_and_commits(tmp_path):
    layout, owner = setup(tmp_path)
    result = record_gate_run(layout, owner, RUN, today=TODAY)
    assert result.refusal is None and result.changed
    assert parse_gate_receipt(receipt_file(layout, owner).read_text(encoding="utf-8")) == (owner, (RUN,))
    page = (layout.bundle_dir / f"{owner}.md").read_text(encoding="utf-8")
    assert "id: gate-receipts" in page and "[^gate-receipts]" in page
    assert git(layout.root, "status", "--porcelain") == ""
    assert "gate receipt" in git(layout.root, "log", "-1", "--format=%s")


def test_second_run_appends_and_keeps_the_first(tmp_path):
    layout, owner = setup(tmp_path)
    record_gate_run(layout, owner, RUN, today=TODAY)
    second = replace(RUN, run_id="20260928T130000Z-00000000", exit=1)
    record_gate_run(layout, owner, second, today=TODAY)
    assert parse_gate_receipt(receipt_file(layout, owner).read_text(encoding="utf-8"))[1] == (RUN, second)


def test_recording_the_same_run_twice_is_a_no_op(tmp_path):
    layout, owner = setup(tmp_path)
    record_gate_run(layout, owner, RUN, today=TODAY)
    again = record_gate_run(layout, owner, RUN, today=TODAY)
    assert again.refusal is None and not again.changed


@pytest.mark.parametrize("status", ["resolved", "wontfix", "superseded"])
def test_terminal_owner_refuses(tmp_path, status):
    layout, owner = setup(tmp_path, work_status=status, phase="done")
    assert record_gate_run(layout, owner, RUN, today=TODAY).refusal == "owner-terminal"


def test_recording_is_allowed_after_the_item_moved_to_finish(tmp_path):
    layout, owner = setup(tmp_path, phase="finish")
    assert record_gate_run(layout, owner, RUN, today=TODAY).refusal is None


def test_malformed_existing_receipt_is_refused_not_rewritten(tmp_path):
    layout, owner = setup(tmp_path)
    receipt = receipt_file(layout, owner)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("---\ntype: Explanation\n---\n", encoding="utf-8", newline="\n")
    assert record_gate_run(layout, owner, RUN, today=TODAY).refusal == "malformed-receipt"
    assert receipt.read_text(encoding="utf-8") == "---\ntype: Explanation\n---\n"


def test_receipt_naming_another_owner_is_refused(tmp_path):
    layout, owner = setup(tmp_path)
    receipt = receipt_file(layout, owner)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        "---\ntype: Explanation\nreceipt_version: 1\nowner: work/other\nruns: []\n---\n", encoding="utf-8", newline="\n"
    )
    result = record_gate_run(layout, owner, RUN, today=TODAY)
    assert result.refusal == "malformed-receipt" and "work/other" in result.detail


def test_unknown_owner_refuses(tmp_path):
    layout, _ = setup(tmp_path)
    assert record_gate_run(layout, "work/nope", RUN, today=TODAY).refusal == "unknown-item"


def test_crlf_owner_page_keeps_its_line_endings_for_the_footnote(tmp_path):
    layout, owner = setup(tmp_path)
    page = layout.bundle_dir / f"{owner}.md"
    page.write_bytes(page.read_bytes().replace(b"\n", b"\r\n"))
    assert record_gate_run(layout, owner, RUN, today=TODAY).changed
    assert b"[^gate-receipts]: " in page.read_bytes()


def test_a_refused_transaction_reports_transaction_refused(tmp_path, monkeypatch):
    from graph_works_core.orchestrate import gate_receipts

    layout, owner = setup(tmp_path)

    class Refused:
        ok = False

        def __str__(self):
            return "refused"

    monkeypatch.setattr(gate_receipts, "apply_mutation", lambda *a, **k: Refused())
    result = record_gate_run(layout, owner, RUN, today=TODAY)
    assert result.refusal == "transaction-refused" and result.detail == "refused"
