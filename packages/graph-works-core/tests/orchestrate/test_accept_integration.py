"""Unverifiable integrations are recorded as attributed, attested evidence."""

import pytest
from _finish_repos import OWNER, TODAY, code_repo, git, workspace
from graph_works_core.orchestrate import accept_integration as accept_module
from graph_works_core.orchestrate.accept_integration import run_accept_integration
from graph_works_core.orchestrate.finish_receipt import run_record_finish
from graph_works_core.workspace.finish import inspect_finish, read_finish_receipt
from work_tracker_okf import decisions


def hand_integrated(tmp_path):
    """main holds the feature's change plus an edit, so no strategy verifies it."""
    repo, source = code_repo(tmp_path)
    git(repo, "merge", "--squash", "feature")
    (repo / "b.txt").write_text("feature, edited in review\n", encoding="utf-8", newline="\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-m", "squash with review edits")
    return repo, source, git(repo, "rev-parse", "HEAD")


def accept(layout, sha, *, reason="squashed with review edits", by="pat", apply=True):
    return run_accept_integration(
        layout, OWNER, repo_name="code", evidence=sha, reason=reason, by=by, today=TODAY, apply=apply
    )


def test_unverifiable_integration_is_accepted_with_attribution(tmp_path):
    repo, source, sha = hand_integrated(tmp_path)
    layout = workspace(tmp_path, repo, source)
    assert run_record_finish(layout, OWNER, repo_name="code", today=TODAY).refusal is not None

    planned = accept(layout, sha[:12], apply=False)
    assert (planned.refusal, planned.applied, planned.evidence) == (None, False, sha)
    assert read_finish_receipt(layout, OWNER)[0] is None

    result = accept(layout, sha[:12])

    assert (result.refusal, result.applied) == (None, True)
    (entry,) = read_finish_receipt(layout, OWNER)[1]
    assert (entry.strategy, entry.evidence, entry.result_commit, entry.accepted_by, entry.reason) == (
        "attested",
        "accepted",
        sha,
        "pat",
        "squashed with review edits",
    )
    assert entry.source_commit == git(source, "rev-parse", "HEAD")
    ledger = decisions.load(decisions.ledger_ref(OWNER).path(layout.bundle_dir)).entries
    assert ledger[-1].status == "answered" and ledger[-1].decided is not None and "by pat" in ledger[-1].decided
    assert ledger[-1].id == result.decision_id
    verification = inspect_finish(layout, OWNER)
    assert verification.complete and verification.accepted == ("code",) and verification.resolved_in == sha


@pytest.mark.parametrize(
    ("kwargs", "refusal"),
    [
        ({"reason": "  "}, "missing-attribution"),
        ({"by": ""}, "missing-attribution"),
    ],
)
def test_missing_attribution_refuses(tmp_path, kwargs, refusal):
    repo, source, sha = hand_integrated(tmp_path)

    assert accept(workspace(tmp_path, repo, source), sha, **kwargs).refusal == refusal


def test_unknown_and_off_target_commits_refuse(tmp_path):
    repo, source, _sha = hand_integrated(tmp_path)
    layout = workspace(tmp_path, repo, source)

    assert accept(layout, "f" * 40).refusal == "unknown-commit"
    assert accept(layout, git(source, "rev-parse", "HEAD")).refusal == "not-on-target"


def test_not_at_finish_refuses(tmp_path):
    repo, source, sha = hand_integrated(tmp_path)

    assert accept(workspace(tmp_path, repo, source, phase="execute"), sha).refusal == "not-at-finish"


def test_receipt_and_ledger_are_one_mutation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    repo, source, sha = hand_integrated(tmp_path)
    layout = workspace(tmp_path, repo, source)
    monkeypatch.setattr(accept_module, "apply_mutation", lambda *a, **k: SimpleNamespace(ok=False))

    assert accept(layout, sha).refusal == "receipt-refused"
    assert read_finish_receipt(layout, OWNER)[0] is None
    assert not decisions.ledger_ref(OWNER).path(layout.bundle_dir).exists()


def test_malformed_receipt_refuses_as_repair_not_replacement(tmp_path):
    repo, source, sha = hand_integrated(tmp_path)
    layout = workspace(tmp_path, repo, source)
    receipt = layout.bundle_dir / OWNER / "references/04-finish-receipt.md"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("---\nreceipt_version: 2\nintegrations: [oops\n---\n", encoding="utf-8", newline="\n")

    for apply in (False, True):
        result = accept(layout, sha, apply=apply)
        assert result.refusal == "receipt-refused" and "malformed" in result.detail
    assert "oops" in receipt.read_text(encoding="utf-8")


def test_a_verified_entry_is_never_replaced_by_attested_evidence(tmp_path):
    repo, source = code_repo(tmp_path)
    git(repo, "merge", "--no-ff", "-m", "merge feature", "feature")
    layout = workspace(tmp_path, repo, source)
    assert run_record_finish(layout, OWNER, repo_name="code", today=TODAY).refusal is None
    (verified,) = read_finish_receipt(layout, OWNER)[1]

    result = accept(layout, git(repo, "rev-parse", "HEAD"))

    assert result.refusal == "receipt-refused" and "verified" in result.detail
    assert read_finish_receipt(layout, OWNER)[1] == (verified,)
