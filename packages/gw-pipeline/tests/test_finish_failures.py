"""Finish evidence refuses unavailable or malformed observations."""

from __future__ import annotations

from pathlib import Path

import pytest
from gw_kernel.testing.transactions import workspace
from gw_kernel.workspace.provenance import GitFailure, GitOutcome
from gw_kernel.workspace.repos import ItemRepo
from gw_pipeline import finish
from okf_io import load_bundle
from work_tracker_okf.items import load_items


@pytest.mark.parametrize("cause", ["missing", "timeout", "error", "unresolved"])
def test_finish_git_preserves_resolver_failure_without_probing(tmp_path, monkeypatch, cause):
    monkeypatch.setattr(finish, "resolve_git", lambda *args, **kwargs: GitFailure(cause, "resolver refused"))
    result = finish.finish_git(tmp_path, "status")
    assert result.returncode is None
    assert result.cause == (cause if cause in {"timeout", "error"} else "missing")
    assert result.stderr == "resolver refused"


def test_squash_probe_reports_unsupported_git_version(tmp_path, monkeypatch):
    monkeypatch.setattr(finish, "finish_git", lambda *args, **kwargs: GitOutcome(1, "", "ok"))
    assert "git version unknown" in finish.merge_tree_unsupported(tmp_path)


@pytest.mark.parametrize(
    "content", [b"\xff", b"---\ntype: [broken\n---\n", b"---\ntype: Explanation\nreceipt_version: true\n---\n"]
)
def test_finish_receipt_rejects_invalid_bytes_or_schema(tmp_path, content):
    layout = workspace(tmp_path)
    path = layout.bundle_dir / "work/feature-x/references/04-finish-receipt.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    assert finish.read_finish_receipt(layout, "work/feature-x") == (None, (), "malformed finish receipt")


def test_finish_receipt_reports_filesystem_refusal(tmp_path, monkeypatch):
    layout = workspace(tmp_path)
    original_read = Path.read_bytes

    def read(path):
        if path.name == "04-finish-receipt.md":
            raise PermissionError("receipt denied")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    assert "receipt denied" in finish.read_finish_receipt(layout, "work/feature-x")[2]


@pytest.mark.parametrize("fault", ["missing-ref", "shape", "unreachable"])
def test_verify_integration_requires_current_refs_shape_and_reachability(tmp_path, monkeypatch, fault):
    target = finish.FinishTarget(
        ItemRepo("code", tmp_path, "frontmatter"), str(tmp_path), "source", "main", str(tmp_path)
    )
    entry = finish.VerifiedIntegration("code", "source", "a" * 40, "main", "b" * 40)
    monkeypatch.setattr(finish, "_commit", lambda repo, ref: None if fault == "missing-ref" else "a" * 40)
    monkeypatch.setattr(finish, "integration_shape", lambda *args: "invalid shape" if fault == "shape" else None)
    monkeypatch.setattr(finish, "_is_ancestor", lambda *args: False)
    reason = finish.verify_integration(target, entry)
    assert (
        reason
        == {
            "missing-ref": "cannot resolve the source or target branch",
            "shape": "invalid shape",
            "unreachable": "result is not reachable from the target tip",
        }[fault]
    )


def test_observe_integration_refuses_unresolved_current_tip(tmp_path, monkeypatch):
    target = finish.FinishTarget(
        ItemRepo("code", tmp_path, "frontmatter"), str(tmp_path), "source", "main", str(tmp_path)
    )
    monkeypatch.setattr(finish, "_commit", lambda *args: None)
    assert finish.observe_integration(target) is None
    assert finish.observe_integration(finish.FinishTarget(ItemRepo(None, None, "none"), "", "", "", None)) is None


@pytest.mark.parametrize("fault", ["history-unreadable", "no-match", "ambiguous"])
def test_rediscovery_never_invents_an_integration(tmp_path, monkeypatch, fault):
    target = finish.FinishTarget(
        ItemRepo("code", tmp_path, "frontmatter"), str(tmp_path), "source", "main", str(tmp_path)
    )
    monkeypatch.setattr(finish, "_commit", lambda *args: "a" * 40)
    monkeypatch.setattr(finish, "_is_ancestor", lambda *args: False)
    monkeypatch.setattr(finish, "finish_git", lambda *args, **kwargs: GitOutcome(0, "b" * 40, "ok"))
    log = (
        None
        if fault == "history-unreadable"
        else []
        if fault == "no-match"
        else [["c" * 40, "d" * 40], ["e" * 40, "f" * 40]]
    )
    monkeypatch.setattr(finish, "_first_parent_log", lambda *args: log)
    monkeypatch.setattr(finish, "_tree", lambda repo, ref: ref)
    monkeypatch.setattr(finish, "integration_shape", lambda *args: None)
    result = finish.discover_integration(target)
    assert isinstance(result, str)
    assert {"history-unreadable": "cannot read", "no-match": "no merge", "ambiguous": "ambiguous: 2"}[fault] in result


@pytest.mark.parametrize("mode", ["missing", "accepted", "unexpected"])
def test_finish_inspection_tracks_incomplete_accepted_and_unexpected_receipts(tmp_path, monkeypatch, mode):
    layout = workspace(tmp_path)
    page = layout.bundle_dir / "work/feature-x.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Feature\nwork_status: in-progress\nphase: finish\n---\n", encoding="utf-8", newline="\n"
    )
    (item,) = load_items(load_bundle(layout.bundle_dir))
    target = finish.FinishTarget(
        ItemRepo("code", tmp_path, "frontmatter"), str(tmp_path), "source", "main", str(tmp_path)
    )
    entry = finish.VerifiedIntegration("code", "source", "a" * 40, "main", "b" * 40, "attested", "accepted")
    entries = (
        ()
        if mode == "missing"
        else (entry,)
        if mode == "accepted"
        else (entry, finish.VerifiedIntegration("other", "s", "a" * 40, "m", "b" * 40))
    )
    monkeypatch.setattr(finish, "resolve_finish_targets", lambda *args: finish.FinishPlan((target,), ()))
    monkeypatch.setattr(finish, "read_finish_receipt", lambda *args: (None, entries, None))
    monkeypatch.setattr(finish, "verify_integration", lambda *args: None)
    result = finish.inspect_finish(layout, item.path)
    assert result.complete == (mode == "accepted")
    if mode == "missing":
        assert "no receipt entry" in result.blockers[0]
    elif mode == "accepted":
        assert result.accepted == ("code",)
    else:
        assert "unexpected repository" in result.blockers[-1]
