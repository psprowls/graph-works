"""Finish evidence is established against real independent Git repositories."""

import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from _seed_copy import copy_tree_into, files_containing, rebase_layout
from _transaction_helpers import _git, _init_git
from graph_works_core import apply_init, plan_init
from graph_works_core.orchestrate.finish_receipt import run_record_finish
from graph_works_core.orchestrate.stage_advance import run_stage_advance
from graph_works_core.workspace.finish import inspect_finish
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import load_bundle
from work_tracker_okf.items import load_items

TODAY = date(2026, 9, 23)
OWNER = "work/epic-example"


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


@dataclass(frozen=True, slots=True)
class ReceiptSeed:
    root: Path
    layout: WorkspaceLayout


NAMES = ("code", "ui")


def build_receipt_seed(root):
    for name in NAMES:
        repo = root / name
        repo.mkdir()
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.test")
        git(repo, "commit", "--allow-empty", "-m", "base")
        worktree = root / (name + "-source")
        git(repo, "worktree", "add", "-b", "feature", str(worktree))
        git(worktree, "commit", "--allow-empty", "-m", "source")
    workspace = root / "workspace"
    workspace.mkdir()
    layout = apply_init(plan_init(workspace, today=TODAY, topic="Finish")).layout
    return ReceiptSeed(root=root, layout=layout)


@pytest.fixture(scope="module")
def receipt_seed(tmp_path_factory):
    return build_receipt_seed(tmp_path_factory.mktemp("receipt_seed"))


def setup(tmp_path, seed):
    repos = {}
    for name in NAMES:
        repo = copy_tree_into(seed.root / name, tmp_path / name)
        worktree = copy_tree_into(seed.root / (name + "-source"), tmp_path / (name + "-source"))
        git(repo, "worktree", "repair", str(worktree))
        repos[name] = (repo, worktree)
    copy_tree_into(seed.layout.root, tmp_path / "workspace")
    layout = rebase_layout(seed.layout, seed.root, tmp_path)
    # apply_init's disposable config projection names the seed root; nothing here reads it.
    (layout.cache_dir / "config.json").unlink(missing_ok=True)
    layout.manifest_path.write_text(
        "version: 1\nworkflow: {dispatch_rules: dispatch.yaml}\nrepositories:\n"
        + "".join(f"  {n}: {{path: {r}}}\n" for n, (r, _) in repos.items()),
        encoding="utf-8",
    )
    page = layout.bundle_dir / (OWNER + ".md")
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Epic\ntitle: Example\nwork_status: in-progress\nphase: finish\nrepo: code\n"
        f"worktree: {repos['code'][1]}\nbranch: feature\nrepo_stamps:\n"
        f"  ui: {{worktree: {repos['ui'][1]}, branch: feature}}\n---\n\nAuthored content.\n",
        encoding="utf-8",
    )
    return layout, repos


def record(layout, repo):
    return run_record_finish(layout, OWNER, repo_name=repo, today=TODAY)


def test_finish_receipt_commits_owned_files(tmp_path, receipt_seed):
    layout, repos = setup(tmp_path, receipt_seed)
    _init_git(layout.root)
    git(repos["code"][0], "merge", "feature")

    result = record(layout, "code")

    assert result.changed and result.refusal is None
    subject = "workspace: record epic-example finish receipt for code"
    assert _git(layout.root, "log", "-1", "--format=%s").strip() == subject
    assert _git(layout.root, "show", "--format=%B", "-s", "HEAD").strip() == subject
    assert _git(layout.root, "status", "--porcelain", "--", "okf") == ""


def test_failed_workspace_commit_preserves_receipt_and_warning(tmp_path, receipt_seed):
    layout, repos = setup(tmp_path, receipt_seed)
    _init_git(layout.root)
    git(repos["code"][0], "merge", "feature")
    (layout.root / ".git/index.lock").write_text("held\n", encoding="utf-8", newline="\n")

    result = record(layout, "code")

    assert result.refusal is None and result.changed
    assert receipt_path(layout).exists()
    assert [entry.repo for entry in inspect_finish(layout, OWNER).entries] == ["code"]
    assert result.commit is not None and result.commit.status == "failed"
    assert f"workspace commit failed: {result.commit.reason}" in result.warnings
    assert _git(layout.root, "log", "-1", "--format=%s").strip() == "workspace: seed"


def advance(layout, sha, **kwargs):
    return run_stage_advance(layout, OWNER, today=TODAY, resolved_in=sha, infer_worktree=False, **kwargs)


def test_partial_retry_and_final_advance(tmp_path, receipt_seed):
    layout, repos = setup(tmp_path, receipt_seed)
    git(repos["code"][0], "merge", "feature")
    first = record(layout, "code")
    assert first.refusal is None and first.changed
    observed = inspect_finish(layout, OWNER)
    assert not observed.complete
    assert [e.repo for e in observed.entries] == ["code"]
    assert any("ui" in b for b in observed.blockers)
    sha = git(repos["code"][0], "rev-parse", "HEAD")
    for dry_run in (True, False):
        refused = advance(layout, sha, dry_run=dry_run)
        assert refused.outcome.plan.refusal == "finish-incomplete"
    assert load_items(load_bundle(layout.bundle_dir))[0].phase == "finish"
    git(repos["ui"][0], "merge", "feature")
    assert record(layout, "ui").refusal is None
    verified = inspect_finish(layout, OWNER)
    assert verified.complete and verified.resolved_in == sha
    completed = advance(layout, sha, dry_run=False)
    assert completed.outcome.written, completed
    assert load_items(load_bundle(layout.bundle_dir))[0].phase == "done"
    assert not advance(layout, sha, dry_run=False).outcome.written


def receipt_path(layout):
    return layout.bundle_dir / OWNER / "references/04-finish-receipt.md"


def merge_all(repos):
    for repo, _ in repos.values():
        git(repo, "merge", "feature")


def test_merge_then_failed_receipt_is_recoverable_without_remerge(tmp_path, receipt_seed, monkeypatch):

    from graph_works_core.orchestrate import finish_receipt

    layout, repos = setup(tmp_path, receipt_seed)
    git(repos["code"][0], "merge", "feature")
    original = finish_receipt.apply_mutation

    def refuse(*args, **kwargs):
        # Simulate a write refusal before domain effects.
        from types import SimpleNamespace

        return SimpleNamespace(ok=False)

    monkeypatch.setattr(finish_receipt, "apply_mutation", refuse)
    assert record(layout, "code").refusal
    assert not receipt_path(layout).exists()
    sha = git(repos["code"][0], "rev-parse", "HEAD")
    monkeypatch.setattr(finish_receipt, "apply_mutation", original)
    assert record(layout, "code").changed
    assert git(repos["code"][0], "rev-parse", "HEAD") == sha
    assert not record(layout, "code").changed


def test_new_source_and_rewritten_target_invalidate_history(tmp_path, receipt_seed):
    layout, repos = setup(tmp_path, receipt_seed)
    merge_all(repos)
    assert record(layout, "code").changed
    assert record(layout, "ui").changed
    initial = receipt_path(layout).read_bytes()
    git(repos["code"][1], "commit", "--allow-empty", "-m", "later")
    assert not inspect_finish(layout, OWNER).complete
    assert record(layout, "code").refusal
    # Recording the other repository preserves the stale evidence as history.
    assert record(layout, "ui").refusal is None
    assert receipt_path(layout).read_bytes() == initial
    git(repos["code"][0], "merge", "feature")
    assert record(layout, "code").changed
    assert inspect_finish(layout, OWNER).complete
    git(repos["ui"][0], "reset", "--hard", "HEAD~1")
    assert not inspect_finish(layout, OWNER).complete
    assert record(layout, "ui").refusal


def test_malformed_and_forged_receipts_refuse_without_overwrite(tmp_path, receipt_seed):
    layout, repos = setup(tmp_path, receipt_seed)
    merge_all(repos)
    assert record(layout, "code").changed
    original = receipt_path(layout).read_bytes()
    for raw in (
        b"broken",
        original.replace(b"receipt_version: 2", b"receipt_version: true"),
        original.replace(b"source_commit: ", b"source_commit: invalid-"),
    ):
        receipt_path(layout).write_bytes(raw)
        assert not inspect_finish(layout, OWNER).complete
        assert record(layout, "code").refusal
        assert receipt_path(layout).read_bytes() == raw
    receipt_path(layout).write_bytes(original)
    # ui object is not present in code's object database (give ui a unique commit).
    git(repos["ui"][1], "commit", "--allow-empty", "-m", "ui only")
    foreign = git(repos["ui"][1], "rev-parse", "HEAD").encode()
    own = git(repos["code"][0], "rev-parse", "HEAD").encode()
    receipt_path(layout).write_bytes(original.replace(b"result_commit: " + own, b"result_commit: " + foreign))
    assert not inspect_finish(layout, OWNER).entries
    assert record(layout, "code").refusal


def test_timeout_is_unverified(tmp_path, receipt_seed, monkeypatch):
    from graph_works_core.workspace import finish
    from graph_works_core.workspace.provenance import GitOutcome

    layout, repos = setup(tmp_path, receipt_seed)
    merge_all(repos)
    assert record(layout, "code").changed
    monkeypatch.setattr(finish, "finish_git", lambda *a, **k: GitOutcome(None, "", "timeout"))
    assert not inspect_finish(layout, OWNER).entries
    assert record(layout, "code").refusal


def test_crlf_and_authored_receipt_content_preserved(tmp_path, receipt_seed):
    layout, repos = setup(tmp_path, receipt_seed)
    merge_all(repos)
    assert record(layout, "code").changed
    receipt = receipt_path(layout)
    raw = receipt.read_bytes().replace(b"receipt_version:", b"custom: retained\nreceipt_version:")
    raw += b"\nAuthored receipt notes.\n"
    receipt.write_bytes(raw.replace(b"\n", b"\r\n"))
    assert record(layout, "ui").changed
    updated = receipt.read_bytes()
    assert b"custom: retained\r\n" in updated
    assert b"Authored receipt notes.\r\n" in updated
    assert b"\n" not in updated.replace(b"\r\n", b"")
    page = (layout.bundle_dir / (OWNER + ".md")).read_text()
    assert "Authored content." in page and "[^finish-receipt]:" in page
    assert not (receipt.parent / "04-finish-results.md").exists()


def test_owner_change_at_transaction_boundary_refuses(tmp_path, receipt_seed, monkeypatch):
    from graph_works_core.orchestrate import finish_receipt

    layout, repos = setup(tmp_path, receipt_seed)
    merge_all(repos)
    original = finish_receipt.apply_mutation

    def change(*args, **kwargs):
        page = layout.bundle_dir / (OWNER + ".md")
        page.write_bytes(page.read_bytes().replace(b"phase: finish", b"phase: execute"))
        return original(*args, **kwargs)

    monkeypatch.setattr(finish_receipt, "apply_mutation", change)
    assert record(layout, "code").refusal
    assert not receipt_path(layout).exists()


def test_advance_rechecks_evidence_at_transaction_boundary(tmp_path, receipt_seed, monkeypatch):
    from graph_works_core.orchestrate import stage_advance

    layout, repos = setup(tmp_path, receipt_seed)
    merge_all(repos)
    record(layout, "code")
    record(layout, "ui")
    original = stage_advance.apply_mutation

    def change(*args, **kwargs):
        git(repos["ui"][1], "commit", "--allow-empty", "-m", "raced")
        return original(*args, **kwargs)

    monkeypatch.setattr(stage_advance, "apply_mutation", change)
    result = advance(layout, git(repos["code"][0], "rev-parse", "HEAD"), dry_run=False)
    assert not result.outcome.written
    assert load_items(load_bundle(layout.bundle_dir))[0].phase == "finish"


def test_foreign_only_owner_resolves_to_first_verified_entry(tmp_path, receipt_seed):
    layout, repos = setup(tmp_path, receipt_seed)
    page = layout.bundle_dir / (OWNER + ".md")
    page.write_bytes(page.read_bytes().replace(f"worktree: {repos['code'][1]}\nbranch: feature\n".encode(), b""))
    git(repos["ui"][0], "merge", "feature")
    assert record(layout, "code").refusal
    assert advance(layout, "false-claim", dry_run=True).outcome.plan.refusal == "finish-incomplete"
    assert record(layout, "ui").changed
    verified = inspect_finish(layout, OWNER)
    assert verified.complete
    assert verified.resolved_in == git(repos["ui"][0], "rev-parse", "HEAD")
    assert advance(layout, verified.resolved_in, dry_run=False).outcome.written


def test_resolved_in_mismatch_and_release_date_remain_gates(tmp_path, receipt_seed):
    layout, repos = setup(tmp_path, receipt_seed)
    merge_all(repos)
    record(layout, "code")
    record(layout, "ui")
    assert advance(layout, "incorrect", dry_run=False).outcome.plan.refusal == "finish-incomplete"
    page = layout.bundle_dir / (OWNER + ".md")
    page.write_bytes(page.read_bytes().replace(b"type: Epic", b"type: Release"))
    sha = git(repos["code"][0], "rev-parse", "HEAD")
    assert advance(layout, sha, dry_run=True).outcome.plan.refusal == "released-at-required"


def test_receipt_stamp_or_configuration_race_refuses(tmp_path, receipt_seed, monkeypatch):
    from graph_works_core.orchestrate import finish_receipt

    layout, repos = setup(tmp_path, receipt_seed)
    merge_all(repos)
    original = finish_receipt.apply_mutation

    def change(*args, **kwargs):
        layout.manifest_path.write_bytes(layout.manifest_path.read_bytes() + b"# external change\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(finish_receipt, "apply_mutation", change)
    assert record(layout, "code").refusal
    assert not receipt_path(layout).exists()


def test_unknown_and_wrong_phase_are_refusals(tmp_path, receipt_seed):
    layout, _ = setup(tmp_path, receipt_seed)
    assert record(layout, "missing").refusal
    assert run_record_finish(layout, "work/epic-missing", repo_name="code", today=TODAY).refusal
    assert not inspect_finish(layout, "work/epic-missing").complete
    page = layout.bundle_dir / (OWNER + ".md")
    page.write_bytes(page.read_bytes().replace(b"phase: finish", b"phase: done"))
    assert record(layout, "code").refusal


def test_squash_is_rediscovered_as_integration(tmp_path, receipt_seed):
    layout, repos = setup(tmp_path, receipt_seed)
    repo, source = repos["code"]
    (source / "change.txt").write_text("content", encoding="utf-8")
    git(source, "add", "change.txt")
    git(source, "commit", "-m", "source change")
    git(repo, "merge", "--squash", "feature")
    git(repo, "commit", "-m", "squashed change")
    assert record(layout, "code").refusal is None
    assert "strategy: squash\n" in receipt_path(layout).read_text(encoding="utf-8")


def test_receipt_and_stamp_preimage_races_refuse(tmp_path, receipt_seed, monkeypatch):
    from graph_works_core.orchestrate import finish_receipt

    layout, repos = setup(tmp_path, receipt_seed)
    merge_all(repos)
    assert record(layout, "code").changed
    original = finish_receipt.apply_mutation
    receipt = receipt_path(layout)

    def change(*args, **kwargs):
        receipt.write_bytes(receipt.read_bytes() + b"\nExternal author note.\n")
        return original(*args, **kwargs)

    monkeypatch.setattr(finish_receipt, "apply_mutation", change)
    assert record(layout, "ui").refusal
    assert "External author note." in receipt.read_text()
    assert [e.repo for e in inspect_finish(layout, OWNER).entries] == ["code"]

    def stamp_change(*args, **kwargs):
        page = layout.bundle_dir / (OWNER + ".md")
        page.write_bytes(page.read_bytes().replace(b"branch: feature}", b"branch: changed}"))
        return original(*args, **kwargs)

    monkeypatch.setattr(finish_receipt, "apply_mutation", stamp_change)
    assert record(layout, "ui").refusal


def test_malformed_entry_and_unexpected_repository_are_not_overwritten(tmp_path, receipt_seed):
    from okf_io import parse

    layout, repos = setup(tmp_path, receipt_seed)
    merge_all(repos)
    record(layout, "code")
    receipt = receipt_path(layout)
    original = receipt.read_bytes()
    for variant in ("boolean", "duplicate", "unexpected", "encoding"):
        doc = parse(original.decode("utf-8"))
        entries = doc.fm_data()["integrations"]
        if variant == "boolean":
            entries[0]["result_commit"] = True
        elif variant == "duplicate":
            entries.append(dict(entries[0]))
        elif variant == "unexpected":
            entries[0]["repo"] = "foreign"
        doc.set("integrations", entries)
        raw = b"\xff" if variant == "encoding" else doc.serialize().encode("utf-8")
        receipt.write_bytes(raw)
        assert record(layout, "ui").refusal
        assert not inspect_finish(layout, OWNER).complete
        assert receipt.read_bytes() == raw


def test_invalid_commit_config_rejects_valid_finish_before_lock_and_preserves_preview(tmp_path, receipt_seed):
    import pytest
    from graph_works_core.workspace.errors import WorkspaceError

    layout, repos = setup(tmp_path, receipt_seed)
    git(repos["code"][0], "merge", "feature")
    git(repos["ui"][0], "merge", "feature")
    layout.local_manifest_path.write_text("workflow:\n  workspace_commits: invalid\n", encoding="utf-8", newline="\n")
    before = {p.relative_to(layout.root): p.read_bytes() for p in layout.root.rglob("*") if p.is_file()}
    with pytest.raises(WorkspaceError, match="workspace_commits"):
        record(layout, "code")
    assert {p.relative_to(layout.root): p.read_bytes() for p in layout.root.rglob("*") if p.is_file()} == before
    # Receipt verification previews still report domain blockers with invalid commit config.
    preview = advance(layout, git(repos["code"][0], "rev-parse", "HEAD"), dry_run=True)
    assert preview.outcome.plan.refusal == "finish-incomplete"


def test_first_v2_write_upgrades_a_v1_receipt_without_rewriting_other_entries(tmp_path, receipt_seed):
    layout, repos = setup(tmp_path, receipt_seed)
    git(repos["code"][0], "merge", "feature")
    code_source = git(repos["code"][0], "rev-parse", "refs/heads/feature")
    code_tip = git(repos["code"][0], "rev-parse", "refs/heads/main")
    code_lines = (
        "  - repo: code\n    source_branch: feature\n"
        f"    source_commit: {code_source}\n    target_branch: main\n    result_commit: {code_tip}\n"
    )
    receipt_path(layout).parent.mkdir(parents=True, exist_ok=True)
    receipt_path(layout).write_text(
        f"---\ntype: Explanation\nreceipt_version: 1\nowner: {OWNER}\nintegrations:\n{code_lines}---\n",
        encoding="utf-8",
        newline="\n",
    )
    git(repos["ui"][0], "merge", "feature")

    assert record(layout, "ui").refusal is None

    text = receipt_path(layout).read_text(encoding="utf-8")
    assert "receipt_version: 2\n" in text and code_lines in text
    assert inspect_finish(layout, OWNER).complete


def test_copied_worktree_is_detached_from_seed(tmp_path, receipt_seed):
    _, repos = setup(tmp_path, receipt_seed)
    repo, worktree = repos["code"]
    common = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    assert common.resolve() == (repo / ".git").resolve()
    seed_head = git(receipt_seed.root / "code-source", "rev-parse", "HEAD")
    git(worktree, "commit", "--allow-empty", "-m", "private")
    assert git(receipt_seed.root / "code-source", "rev-parse", "HEAD") == seed_head
    listed = [Path(line.split()[0]).resolve() for line in git(repo, "worktree", "list").splitlines()]
    assert (tmp_path / "code-source").resolve() in listed


def test_receipt_copy_contains_no_seed_path(tmp_path, receipt_seed):
    setup(tmp_path, receipt_seed)
    for needle in {str(receipt_seed.root), str(receipt_seed.root.resolve())}:
        assert files_containing(tmp_path, needle) == []
