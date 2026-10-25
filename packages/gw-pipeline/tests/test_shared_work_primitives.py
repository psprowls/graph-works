"""Pipeline-owned evidence primitives, exercised without a vertical caller."""

import json
import subprocess
from dataclasses import replace

import pytest
from gw_kernel.testing.transactions import workspace
from gw_kernel.workspace.repos import ItemRepo
from gw_pipeline import decision_owner as owners
from gw_pipeline import finish
from okf_io import load_bundle
from work_tracker_okf.items import load_items
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _init_git(root):
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Kernel Test")
    _git(root, "config", "user.email", "kernel@example.test")
    _git(root, "add", "-A")
    _git(root, "commit", "--allow-empty", "-m", "base")


def _page(layout, member="work/feature-owner", extra="", kind="Feature", affects="[]"):
    path = layout.bundle_dir / (member + ".md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {kind}\ntitle: Owner\ndescription: d\nstatus: stable\n"
        "work_status: in-progress\nphase: finish\neffort: medium\n"
        f"opened: 2026-08-01\nupdated: 2026-08-01\naffects: {affects}\n" + extra + "---\n\n## Summary\n\nd\n",
        encoding="utf-8",
        newline="\n",
    )
    return member


def _items(layout):
    return load_items(load_bundle(layout.bundle_dir))


def test_decision_projection_reads_each_owner_once_and_lock_rechecks_reparent(tmp_path, monkeypatch):
    layout = workspace(tmp_path)
    parent = _page(layout, kind="Epic")
    child = _page(layout, parent + "/children/bug-child", kind="Bug")
    other = _page(layout, "work/bug-other", kind="Bug")
    ledger = layout.bundle_dir / f"{parent}/references/00-decisions.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        f"## D-001 — admission\nstatus: open\naffects: [{child}]\nhold: skip\nphase: finish\n",
        encoding="utf-8",
        newline="\n",
    )
    items = _items(layout)
    context = owners.decision_context(layout, child)
    assert context.owner.owner_path == parent and context.owner.redirected_from == child
    assert owners.hold_in(context, child).decision_id == "D-001"
    assert owners.hold_for(items, layout.bundle_dir, other) is None
    assert owners.hold_for(items, layout.bundle_dir, "work/unknown") is None
    assert set(owners.holds_by_path(items, layout.bundle_dir)) == {child}
    original = owners._decisions.load
    reads = []

    def observed(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(owners._decisions, "load", observed)
    reports = owners.open_holds(items, layout.bundle_dir, [parent, child, child, other, "work/unknown"])
    assert [(r.path, r.owner_path, r.decision.id) for r in reports] == [(child, parent, "D-001")]
    assert len(reads) == 2
    answers = iter([child, parent, parent, parent])
    original_owner = owners.decision_owner
    monkeypatch.setattr(owners, "decision_owner", lambda *_: next(answers))
    with owners.locked_decision_owner(layout, child) as locked:
        assert locked.owner.owner_path == parent
    monkeypatch.setattr(owners, "decision_owner", original_owner)
    with pytest.raises(ValueError, match="unknown work item"):
        owners.decision_context(layout, "work/unknown")


@pytest.mark.parametrize("strategy", ["ff", "merge", "squash"])
def test_finish_rediscovery_rechecks_real_git_evidence_without_mutation(tmp_path, strategy):
    layout = workspace(tmp_path)
    _init_git(layout.root)
    before = _git(layout.root, "rev-parse", "HEAD")
    _git(layout.root, "checkout", "-b", "feature")
    (layout.root / "change").write_text("change", encoding="utf-8", newline="\n")
    _git(layout.root, "add", "change")
    _git(layout.root, "commit", "-m", "feature")
    source = _git(layout.root, "rev-parse", "HEAD")
    _git(layout.root, "checkout", "main")
    if strategy == "ff":
        _git(layout.root, "merge", "--ff-only", "feature")
    elif strategy == "merge":
        _git(layout.root, "merge", "--no-ff", "feature", "-m", "merge")
    else:
        _git(layout.root, "merge", "--squash", "feature")
        _git(layout.root, "commit", "-m", "squash")
    target = finish.FinishTarget(
        ItemRepo("code", layout.root, "sole"), str(layout.root), "feature", "main", str(layout.root)
    )
    tip = _git(layout.root, "rev-parse", "HEAD")
    observed = finish.discover_integration(target)
    assert isinstance(observed, finish.VerifiedIntegration)
    assert observed.strategy == ("ancestry" if strategy == "ff" else strategy)
    assert finish.verify_integration(target, observed) is None
    assert finish.historical_integration(target, observed)
    assert finish.merge_tree_unsupported(layout.root) is None
    assert (
        finish.verify_integration(replace(target, source_branch="absent"), observed) == "entry names a different target"
    )
    assert (
        finish.discover_integration(replace(target, source_branch="absent"))
        == "cannot resolve the source or target branch"
    )
    assert finish.discover_integration(replace(target, repo=ItemRepo(None, None, "sole"))) == "no repository checkout"
    assert (
        finish.integration_shape(layout.root, replace(observed, source_commit="f" * 40))
        == "source commit is not in the repository"
    )
    assert (
        finish.integration_shape(layout.root, replace(observed, result_commit="f" * 40))
        == "result commit is not in the repository"
    )
    assert _git(layout.root, "rev-parse", "HEAD") == tip
    assert before != source


def test_finish_inspection_refuses_missing_receipt_and_guard_tracks_raw_preimages(tmp_path):
    layout = workspace(tmp_path)
    _init_git(layout.root)
    member = _page(layout, extra=f"worktree: {layout.root}\nbranch: main\n")
    first = finish.finish_read_guard(layout, member)
    # No declared code repository: no finish targets, hence nothing to verify.
    assert finish.inspect_finish(layout, member).complete
    assert "unknown finish owner" in finish.inspect_finish(layout, "work/missing").blockers[0]
    (layout.bundle_dir / (member + ".md")).write_text("---\ntype: Feature\n---\n", encoding="utf-8", newline="\n")
    assert finish.finish_read_guard(layout, member) != first
    first = finish.finish_read_guard(layout, "work/missing")
    layout.manifest_path.with_name("workspace.local.yaml").write_text(
        "repositories: {}\n", encoding="utf-8", newline="\n"
    )
    assert finish.finish_read_guard(layout, "work/missing") != first


def _receipt(layout, member, entries):
    path = artifact_ref(member, MANAGED_ARTIFACTS["finish-receipt"]).path(layout.bundle_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Explanation\nreceipt_version: 2\nowner: {member}\nintegrations: {json.dumps(entries)}\n---\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def test_finish_inspection_accepts_attributed_evidence_and_rejects_extra_repository(tmp_path, monkeypatch):
    layout = workspace(tmp_path)
    _init_git(layout.root)
    member = _page(layout)
    sha = _git(layout.root, "rev-parse", "HEAD")
    target = finish.FinishTarget(
        ItemRepo("code", layout.root, "sole"), str(layout.root), "main", "main", str(layout.root)
    )
    monkeypatch.setattr(finish, "resolve_finish_targets", lambda *_: finish.FinishPlan((target,), ()))
    entry = finish.VerifiedIntegration(
        "code", "main", sha, "main", sha, "attested", "accepted", accepted_by="pat", reason="reviewed"
    )
    _receipt(layout, member, [finish.receipt_entry_data(entry)])
    result = finish.inspect_finish(layout, member)
    assert result.complete and result.resolved_in == sha and result.accepted == ("code",)
    _receipt(
        layout, member, [finish.receipt_entry_data(entry), finish.receipt_entry_data(replace(entry, repo="unexpected"))]
    )
    assert finish.inspect_finish(layout, member).blockers == ("finish receipt contains an unexpected repository",)
    _receipt(layout, member, [finish.receipt_entry_data(replace(entry, source_commit="f" * 40))])
    assert "source branch moved" in finish.inspect_finish(layout, member).blockers[0]


@pytest.mark.parametrize(
    "updates,message",
    [
        ({"repo": ""}, "integration"),
        ({"source_commit": "invalid"}, "commit"),
        ({"strategy": "invalid"}, "integration"),
        ({"strategy": "attested"}, "integration"),
        ({"strategy": "attested", "evidence": "accepted"}, "attribution"),
        ({"accepted_by": "pat"}, "attribution"),
        ({"strategy": "squash"}, "target_before"),
        ({"target_before": "a" * 40}, "target_before"),
    ],
)
def test_finish_receipt_rejects_inconsistent_evidence_at_public_reader(tmp_path, updates, message):
    layout = workspace(tmp_path)
    member = _page(layout)
    data = finish.receipt_entry_data(finish.VerifiedIntegration("code", "feature", "a" * 40, "main", "b" * 40))
    data.update(updates)
    _receipt(layout, member, [data])
    assert finish.read_finish_receipt(layout, member)[2] == "malformed finish receipt " + message
