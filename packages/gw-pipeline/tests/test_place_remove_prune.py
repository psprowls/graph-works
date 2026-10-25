"""Receipt-gated removal keeps branches, owners and unproven checkouts safe."""

import os
from dataclasses import replace

import pytest
from gw_kernel.workspace import provenance
from gw_kernel.workspace.commits import CommitOutcome
from gw_pipeline import place, repo_context
from gw_pipeline.finish import VerifiedIntegration, receipt_entry_data
from gw_pipeline.test_support.finish_repos import commit_file, write
from okf_io import load_bundle
from work_tracker_okf.items import load_items
from work_tracker_okf.paths import MANAGED_ARTIFACTS, artifact_ref


def _created(env, *, family=False):
    owner = env.file("owner", kind="Epic") if family else None
    chain = env.chain_for(env.file("x", parent=owner))
    assert place.ensure(env.ctx(), chain).ok
    tip = commit_file(chain[-1].worktree, "own.txt", "Finished work\n", "Own work")
    return chain, tip


def _receipt_path(env, member):
    return artifact_ref(member.path, MANAGED_ARTIFACTS["finish-receipt"]).path(env.layout.bundle_dir)


def _receipt(env, member, tip, *, evidence="verified", repo="code"):
    entry = VerifiedIntegration(
        repo,
        member.branch,
        tip,
        "main",
        env.git("rev-parse", "refs/heads/main"),
        strategy="attested" if evidence == "accepted" else "squash",
        evidence=evidence,
        target_before=None if evidence == "accepted" else env.git("rev-parse", "refs/heads/main"),
        accepted_by="Reviewer" if evidence == "accepted" else None,
        reason="Reviewed external integration" if evidence == "accepted" else None,
    )
    rows = [
        f"  {'- ' if index == 0 else '  '}{key}: {value}"
        for index, (key, value) in enumerate(receipt_entry_data(entry).items())
    ]
    path = _receipt_path(env, member)
    path.parent.mkdir(parents=True, exist_ok=True)
    write(
        path,
        f"---\ntype: Explanation\nreceipt_version: 2\nowner: {member.path}\nintegrations:\n"
        + "\n".join(rows)
        + "\n---\n",
    )
    return path


def test_remove_on_verified_receipt(place_env):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], tip)

    result = place.remove(env.ctx(), chain[-1])

    assert result.ok and result.mode == "remove" and result.path == chain[-1].path
    assert result.placement == chain[-1]
    assert result.chain == (place.PlaceStep(chain[-1], "removed", None, None),)
    assert not chain[-1].worktree.exists()
    assert str(chain[-1].worktree) not in env.git("worktree", "list", "--porcelain")
    assert env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip


def test_remove_on_accepted_receipt(place_env):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], tip, evidence="accepted")

    result = place.remove(env.ctx(), chain[-1])

    assert result.ok and result.chain[0].action == "removed"
    assert not chain[-1].worktree.exists()
    assert env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip


@pytest.mark.parametrize("dry_run", [False, True])
def test_remove_refuses_without_receipt(place_env, dry_run):
    env = place_env
    chain, tip = _created(env)

    result = place.remove(env.ctx(dry_run=dry_run), chain[-1])

    assert result.refusal.kind == "not-finished"
    assert all(value in result.refusal.detail for value in (chain[-1].branch, tip, str(_receipt_path(env, chain[-1]))))
    assert chain[-1].worktree.is_dir()


@pytest.mark.parametrize("dry_run", [False, True])
def test_remove_refuses_when_receipt_names_other_tip(place_env, dry_run):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], env.git("rev-parse", "refs/heads/main"))

    result = place.remove(env.ctx(dry_run=dry_run), chain[-1])

    assert result.refusal.kind == "not-finished" and tip in result.refusal.detail
    assert chain[-1].worktree.is_dir()


@pytest.mark.parametrize("dry_run", [False, True])
def test_remove_refuses_dirty(place_env, dry_run):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], tip)
    (chain[-1].worktree / "untracked.txt").touch()

    result = place.remove(env.ctx(dry_run=dry_run), chain[-1])

    assert result.refusal.kind == "dirty-worktree"
    assert "untracked.txt" in result.refusal.detail
    assert chain[-1].worktree.is_dir()


@pytest.mark.parametrize("dry_run", [False, True])
def test_remove_absent_is_kept(place_env, dry_run):
    env = place_env
    member = env.chain_for(env.file())[-1]

    result = place.remove(env.ctx(dry_run=dry_run), member)

    assert result.ok and result.chain == (place.PlaceStep(member, "kept", None, None, "absent"),)
    assert not member.worktree.exists()


def test_remove_dry_run(place_env, monkeypatch):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], tip)
    original = provenance.probe_git
    statuses = []

    def read(cwd, *args, **kwargs):
        if "status" in args:
            statuses.append(args)
        return original(cwd, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("dry-run removal must not mutate Git or pre-commit")

    monkeypatch.setattr(provenance, "probe_git", read)
    monkeypatch.setattr(place, "_mutate", forbidden)
    monkeypatch.setattr(place, "commit_pending", forbidden)
    result = place.remove(env.ctx(dry_run=True), chain[-1])

    assert result.ok and result.dry_run and result.chain[0].action == "removed"
    assert result.workspace_commit is None
    assert chain[-1].worktree.is_dir()
    assert env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip
    assert statuses and all(args[0] == "--no-optional-locks" for args in statuses)


@pytest.mark.parametrize("kind", ["branch", "detached", "plain", "repository"])
def test_remove_refuses_foreign_checkout(place_env, git_repo, kind):
    env = place_env
    chain, tip = _created(env)
    member = chain[-1]
    _receipt(env, member, tip)
    if kind == "branch":
        env.git("switch", "-c", "other", cwd=member.worktree)
    elif kind == "detached":
        env.git("switch", "--detach", cwd=member.worktree)
    else:
        env.git("worktree", "remove", str(member.worktree))
        if kind == "plain":
            member.worktree.mkdir()
        else:
            git_repo(member.worktree, {"foreign.txt": "foreign"})

    result = place.remove(env.ctx(), member)

    assert result.refusal.kind == "foreign-checkout"
    assert str(member.worktree) in result.refusal.detail and member.branch in result.refusal.detail
    assert member.worktree.is_dir()


def _owner_with_child(env):
    owner = env.file("owner", kind="Epic")
    child = env.file("kid", parent=owner)
    chain = env.chain_for(child)
    assert place.ensure(env.ctx(), chain).ok
    owner_tip = commit_file(chain[0].worktree, "owner.txt", "Owner work\n", "Owner work")
    child_tip = commit_file(chain[1].worktree, "kid.txt", "Child work\n", "Child work")
    return chain, owner_tip, child_tip


def test_remove_owner_removes_own_worktree(place_env):
    env = place_env
    chain, owner_tip, _ = _owner_with_child(env)
    _receipt(env, chain[0], owner_tip)

    result = place.remove(env.ctx(), chain[0])

    assert result.ok and result.chain == (place.PlaceStep(chain[0], "removed", None, None),)
    assert not chain[0].worktree.exists()
    assert env.git("rev-parse", f"refs/heads/{chain[0].branch}") == owner_tip


def test_remove_owner_keeps_children(place_env):
    env = place_env
    chain, owner_tip, child_tip = _owner_with_child(env)
    _receipt(env, chain[0], owner_tip)
    _receipt(env, chain[1], child_tip)

    result = place.remove(env.ctx(), chain[0])

    assert result.ok and not chain[0].worktree.exists()
    assert chain[1].worktree.is_dir()
    assert str(chain[1].worktree) in env.git("worktree", "list", "--porcelain")
    assert env.git("rev-parse", f"refs/heads/{chain[1].branch}") == child_tip


def test_remove_owner_without_receipt_is_not_finished(place_env):
    env = place_env
    chain, _, _ = _owner_with_child(env)

    result = place.remove(env.ctx(), chain[0])

    assert result.refusal is not None and result.refusal.kind == "not-finished"
    assert chain[0].worktree.is_dir()


def test_remove_keeps_owner_checkout_and_branch(place_env):
    env = place_env
    chain, tip = _created(env, family=True)
    _receipt(env, chain[-1], tip)
    owner_tip = env.git("rev-parse", f"refs/heads/{chain[0].branch}")

    result = place.remove(env.ctx(), chain[-1])

    assert result.ok and len(result.chain) == 1 and not chain[-1].worktree.exists()
    assert chain[0].worktree.is_dir()
    assert env.git("rev-parse", f"refs/heads/{chain[0].branch}") == owner_tip


@pytest.mark.parametrize("receipt", ["other-repo", "malformed", "invalid-evidence"])
def test_remove_requires_valid_receipt_for_matching_repository(place_env, receipt):
    env = place_env
    chain, tip = _created(env)
    path = _receipt(env, chain[-1], tip, repo="other" if receipt == "other-repo" else "code")
    if receipt == "malformed":
        write(path, "---\ntype: Explanation\nreceipt_version: 99\n---\n")
    elif receipt == "invalid-evidence":
        write(path, path.read_text(encoding="utf-8").replace("evidence: verified", "evidence: unverified"))

    result = place.remove(env.ctx(), chain[-1])

    assert result.refusal.kind == "not-finished" and str(path) in result.refusal.detail
    if receipt != "other-repo":
        assert "malformed finish receipt" in result.refusal.detail
    assert chain[-1].worktree.is_dir()


def test_remove_failed_precommit_stops_before_observation(place_env, monkeypatch):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], tip)
    failure = CommitOutcome("failed", None, "workspace: place feature-x", (), "commit rejected")
    monkeypatch.setattr(place, "commit_pending", lambda *args, **kwargs: failure)

    def forbidden(*args, **kwargs):
        pytest.fail("failed pre-commit must stop before observation or removal")

    monkeypatch.setattr(place, "observe_repository", forbidden)
    monkeypatch.setattr(place, "_mutate", forbidden)
    result = place.remove(env.ctx(), chain[-1])

    assert result.refusal.kind == "git-unavailable" and "commit rejected" in result.refusal.detail
    assert result.workspace_commit is failure and result.chain[0].action == "skipped"
    assert chain[-1].worktree.is_dir()


def test_remove_failed_observation_refuses(place_env, monkeypatch):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], tip)
    original = place.observe_repository
    monkeypatch.setattr(
        place, "observe_repository", lambda *args, **kwargs: replace(original(*args, **kwargs), branch_tips_known=False)
    )

    result = place.remove(env.ctx(), chain[-1])

    assert result.refusal.kind == "git-unavailable" and chain[-1].worktree.is_dir()


def test_remove_failure_is_bounded_and_never_forced(place_env, monkeypatch):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], tip)
    original = provenance.probe_git
    removes = []

    def probe(cwd, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            removes.append((args, kwargs))
            return provenance.GitOutcome(None, "", "timeout")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = place.remove(env.ctx(), chain[-1])

    assert result.refusal.kind == "timeout" and chain[-1].worktree.is_dir()
    assert len(removes) == 1
    assert removes[0][0] == ("worktree", "remove", str(chain[-1].worktree))
    assert removes[0][1]["timeout"] == 120


def test_remove_tag_cannot_override_branch_tip(place_env):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], tip)
    env.git("tag", chain[-1].branch, "refs/heads/main")

    result = place.remove(env.ctx(), chain[-1])

    assert result.ok and not chain[-1].worktree.exists()
    assert env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip


def test_remove_absent_computed_checkout_keeps_legacy_checkout(place_env):
    env = place_env
    chain, tip = _created(env)
    legacy = env.repo.parent / "legacy"
    env.git("worktree", "move", str(chain[-1].worktree), str(legacy))

    result = place.remove(env.ctx(), chain[-1])

    assert result.ok and result.chain[0].action == "kept" and result.chain[0].detail == "absent"
    assert legacy.is_dir() and env.git("rev-parse", "HEAD", cwd=legacy) == tip


@pytest.mark.parametrize("read", ["identity", "status"])
def test_remove_failed_checkout_read_preserves_checkout(place_env, monkeypatch, read):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], tip)
    original = provenance.probe_git

    def probe(cwd, *args, **kwargs):
        if cwd == chain[-1].worktree and args[:1] == (("rev-parse",) if read == "identity" else ("status",)):
            return provenance.GitOutcome(128, "", "ok", "checkout evidence unavailable")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = place.remove(env.ctx(), chain[-1])

    assert result.refusal.kind == "git-unavailable"
    assert "checkout evidence unavailable" in result.refusal.detail and chain[-1].worktree.is_dir()


def test_remove_missing_branch_tip_preserves_checkout(place_env, monkeypatch):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], tip)
    original = place.observe_repository
    monkeypatch.setattr(
        place, "observe_repository", lambda *args, **kwargs: replace(original(*args, **kwargs), branch_tips={})
    )

    result = place.remove(env.ctx(), chain[-1])

    assert result.refusal.kind == "git-unavailable" and chain[-1].branch in result.refusal.detail
    assert chain[-1].worktree.is_dir()


def test_remove_lock_failure_preserves_checkout(place_env):
    env = place_env
    chain, tip = _created(env)
    _receipt(env, chain[-1], tip)
    lock = env.layout.cache_dir / "placement" / "code.lock"
    lock.unlink()
    lock.mkdir()

    result = place.remove(env.ctx(), chain[-1])

    assert result.refusal.kind == "git-unavailable" and chain[-1].worktree.is_dir()


def _candidate(env, member, *, status="resolved", archived=False):
    item = load_items(load_bundle(env.layout.bundle_dir)).by_path[member.path]
    if archived:
        path = member.path.replace("work/", "work/_archive/", 1)
        page = env.layout.bundle_dir / f"{path}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        (env.layout.bundle_dir / f"{member.path}.md").rename(page)
        owned = env.layout.bundle_dir / member.path
        if owned.exists():
            owned.rename(env.layout.bundle_dir / path)
        member = replace(member, path=path)
        item = replace(item, path=path, page_path=f"{path}.md", archived=True)
    return place.PruneCandidate(replace(item, work_status=status), member)


@pytest.mark.parametrize("dry_run", [False, True])
def test_prune_removes_resolved_worktree_keeps_branch(place_env, dry_run):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1])
    _receipt(env, candidate.placement, tip)

    result = place.prune(env.ctx(dry_run=dry_run), [candidate])

    assert result.ok and result.mode == "prune" and result.path is None
    assert result.pruned == place.PruneReport(
        (str(chain[-1].worktree),),
        (),
        (place.PruneKept(chain[-1].path, "code", chain[-1].branch, "not-archived"),),
    )
    assert chain[-1].worktree.exists() is dry_run
    assert env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip


@pytest.mark.parametrize("dry_run", [False, True])
def test_prune_keeps_dirty(place_env, dry_run):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1], archived=True)
    _receipt(env, candidate.placement, tip)
    (chain[-1].worktree / "untracked.txt").touch()

    result = place.prune(env.ctx(dry_run=dry_run), [candidate])

    assert result.ok and result.pruned.removed_worktrees == result.pruned.deleted_branches == ()
    assert [entry.why for entry in result.pruned.kept] == ["dirty"]
    assert chain[-1].worktree.is_dir()


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("evidence", ["verified", "accepted"])
def test_prune_deletes_archived_branch_on_receipt(place_env, dry_run, evidence):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1], archived=True)
    _receipt(env, candidate.placement, tip, evidence=evidence)

    result = place.prune(env.ctx(dry_run=dry_run), [candidate])

    assert result.ok and result.pruned == place.PruneReport((str(chain[-1].worktree),), (chain[-1].branch,), ())
    assert chain[-1].worktree.exists() is dry_run
    branches = env.git("for-each-ref", "--format=%(refname)", "refs/heads/").splitlines()
    assert (f"refs/heads/{chain[-1].branch}" in branches) is dry_run


@pytest.mark.parametrize("receipt", ["other-tip", "missing", "other-repo", "malformed"])
def test_prune_keeps_unmerged_archived_branch(place_env, receipt):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1], archived=True)
    env.git("worktree", "remove", str(chain[-1].worktree))
    if receipt != "missing":
        path = _receipt(
            env,
            candidate.placement,
            env.git("rev-parse", "refs/heads/main") if receipt == "other-tip" else tip,
            repo="other" if receipt == "other-repo" else "code",
        )
        if receipt == "malformed":
            write(path, "---\ntype: Explanation\nreceipt_version: 99\n---\n")

    result = place.prune(env.ctx(), [candidate])

    assert result.ok and result.pruned.removed_worktrees == result.pruned.deleted_branches == ()
    assert [entry.why for entry in result.pruned.kept] == ["unmerged"]
    assert env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip


@pytest.mark.parametrize("status,why", [("open", "open"), ("wontfix", "no-receipt"), ("superseded", "no-receipt")])
def test_prune_leaves_open_and_wontfix(place_env, status, why):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1], status=status, archived=True)
    # Even a receipt cannot authorize cleanup for non-resolved items.
    _receipt(env, candidate.placement, tip)

    result = place.prune(env.ctx(), [candidate])

    assert result.ok and result.pruned.removed_worktrees == result.pruned.deleted_branches == ()
    assert [entry.why for entry in result.pruned.kept] == [why]
    assert chain[-1].worktree.is_dir()


def test_prune_resolved_checkout_without_matching_receipt_is_kept(place_env):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1])
    _receipt(env, candidate.placement, env.git("rev-parse", "refs/heads/main"))

    result = place.prune(env.ctx(), [candidate])

    assert result.ok and [entry.why for entry in result.pruned.kept] == ["no-receipt"]
    assert result.pruned.removed_worktrees == result.pruned.deleted_branches == ()
    assert chain[-1].worktree.is_dir() and env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip


def test_prune_owner_after_descendants(place_env):
    env = place_env
    chain, _ = _created(env, family=True)
    candidates = [_candidate(env, member) for member in reversed(chain)]
    for candidate in candidates:
        _receipt(env, candidate.placement, env.git("rev-parse", f"refs/heads/{candidate.placement.branch}"))

    result = place.prune(env.ctx(), candidates)

    assert result.ok
    assert result.pruned.removed_worktrees == (str(chain[1].worktree), str(chain[0].worktree))
    assert not any(member.worktree.exists() for member in chain)


def test_prune_two_repositories(place_env, git_repo, declare_repos, monkeypatch):
    env = place_env
    chain, tip = _created(env)
    code = _candidate(env, chain[-1])
    _receipt(env, code.placement, tip)
    second = git_repo(env.repo.parent / "second", {"other.txt": "second\n"})
    env.git("config", "user.email", "place@example.test", cwd=second)
    env.git("config", "user.name", "Placement Tests", cwd=second)
    env.git("commit", "-m", "Initial second", cwd=second)
    env.git("checkout", "-B", "main", cwd=second)
    declare_repos(env.layout, {"code": (env.repo, []), "second": (second, [])})
    other_member = env.chain_for(env.file("second"))[-1]
    other_member = replace(
        other_member,
        repo_name="second",
        repo_root=second,
        worktree=env.layout.worktrees_dir / "second" / other_member.branch,
        parent_worktree=second,
    )
    assert place.ensure(env.ctx(), (other_member,)).ok
    other = _candidate(env, other_member)
    _receipt(env, other.placement, env.git("rev-parse", "HEAD", cwd=other_member.worktree), repo="second")
    # Remove ensure's lock files to prove prune creates both repository locks.
    for repo in ("code", "second"):
        (env.layout.cache_dir / "placement" / f"{repo}.lock").unlink()
    original = place.observe_repository
    observations = []

    def observe(repo, *, paths=(), **kwargs):
        paths = tuple(paths)
        observations.append((repo, paths))
        return original(repo, paths=paths, **kwargs)

    monkeypatch.setattr(place, "observe_repository", observe)

    result = place.prune(env.ctx(), [code, other])

    assert result.ok and result.pruned.removed_worktrees == (str(code.placement.worktree), str(other_member.worktree))
    assert all((env.layout.cache_dir / "placement" / f"{repo}.lock").is_file() for repo in ("code", "second"))
    assert not code.placement.worktree.exists() and not other_member.worktree.exists()
    assert observations == [(env.repo, (code.placement.worktree,)), (second, (other_member.worktree,))]


def test_prune_omits_candidates_without_branch_or_checkout(place_env):
    env = place_env
    candidate = _candidate(env, env.chain_for(env.file())[-1], status="open")

    result = place.prune(env.ctx(), [candidate])

    assert result.ok and result.pruned == place.PruneReport((), (), ())


@pytest.mark.parametrize("dry_run", [False, True])
def test_prune_preserves_branch_held_by_legacy_worktree(place_env, dry_run):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1], archived=True)
    _receipt(env, candidate.placement, tip)
    legacy = env.repo.parent / "legacy"
    env.git("worktree", "move", str(chain[-1].worktree), str(legacy))

    result = place.prune(env.ctx(dry_run=dry_run), [candidate])

    assert result.ok and result.pruned.removed_worktrees == result.pruned.deleted_branches == ()
    assert result.pruned.kept == (place.PruneKept(candidate.item.path, "code", chain[-1].branch, "occupied"),)
    assert legacy.is_dir() and env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip


@pytest.mark.parametrize("path_supplied", [False, True])
def test_prune_dry_run_mutates_nothing(place_env, git_repo, monkeypatch, path_supplied):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1], archived=True)
    _receipt(env, candidate.placement, tip)
    git_repo(env.layout.root, {})
    env.git("config", "user.email", "place@example.test", cwd=env.layout.root)
    env.git("config", "user.name", "Placement Tests", cwd=env.layout.root)
    env.git("commit", "-m", "Initial workspace", cwd=env.layout.root)
    indexes = []
    for checkout, rel in ((env.layout.root, "log.md"), (env.repo, "src/a.py"), (chain[-1].worktree, "own.txt")):
        target = env.layout.bundle_dir / rel if checkout == env.layout.root else checkout / rel
        stat = target.stat()
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
        index = checkout / env.git("rev-parse", "--git-path", "index", cwd=checkout)
        indexes.append((index, index.read_bytes()))
    refs = env.git("for-each-ref", "refs/heads/")
    listing = env.git("worktree", "list", "--porcelain")
    workspace_head = env.git("rev-parse", "HEAD", cwd=env.layout.root)

    def forbidden(*args, **kwargs):
        pytest.fail("prune dry-run must not mutate Git or pre-commit")

    monkeypatch.setattr(place, "_mutate", forbidden)
    monkeypatch.setattr(place, "commit_pending", forbidden)
    result = place.prune(env.ctx(dry_run=True), [candidate], path=candidate.item.path if path_supplied else None)

    assert result.ok and result.dry_run and result.workspace_commit is None
    assert result.pruned.removed_worktrees == (str(chain[-1].worktree),)
    assert result.pruned.deleted_branches == (chain[-1].branch,)
    assert chain[-1].worktree.is_dir()
    assert env.git("for-each-ref", "refs/heads/") == refs
    assert env.git("worktree", "list", "--porcelain") == listing
    assert env.git("rev-parse", "HEAD", cwd=env.layout.root) == workspace_head
    assert all(index.read_bytes() == before for index, before in indexes)


@pytest.mark.parametrize("path_supplied", [False, True])
def test_prune_workspace_precommit_scope(place_env, git_repo, path_supplied):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1])
    _receipt(env, candidate.placement, tip)
    workflow = env.layout.root / "workflow.yaml"
    write(workflow, "workflow:\n  workspace_commits: on\n" + workflow.read_text(encoding="utf-8"))
    git_repo(env.layout.root, {})
    env.git("config", "user.email", "place@example.test", cwd=env.layout.root)
    env.git("config", "user.name", "Placement Tests", cwd=env.layout.root)
    env.git("commit", "-m", "Initial workspace", cwd=env.layout.root)
    page = env.layout.bundle_dir / f"{candidate.item.path}.md"
    write(page, page.read_text(encoding="utf-8") + "\nItem edit\n")
    log = env.layout.bundle_dir / "log.md"
    write(log, log.read_text(encoding="utf-8") + "\nUnrelated edit\n")
    head = env.git("rev-parse", "HEAD", cwd=env.layout.root)

    result = place.prune(env.ctx(), [candidate], path=candidate.item.path if path_supplied else None)

    assert result.ok and result.path == (candidate.item.path if path_supplied else None)
    if path_supplied:
        assert result.workspace_commit.status == "committed"
        assert result.dirty_workspace_paths == ("okf/log.md",)
        assert env.git("log", "-1", "--format=%s", cwd=env.layout.root) == "workspace: place feature-x"
    else:
        assert result.workspace_commit is None
        assert set(result.dirty_workspace_paths) == {"okf/log.md", f"okf/{candidate.item.path}.md"}
        assert env.git("rev-parse", "HEAD", cwd=env.layout.root) == head


def test_prune_failed_precommit_stops_before_repository_observation(place_env, monkeypatch):
    env = place_env
    chain, _ = _created(env)
    candidate = _candidate(env, chain[-1])
    failure = CommitOutcome("failed", None, "workspace: place feature-x", (), "commit rejected")
    monkeypatch.setattr(place, "commit_pending", lambda *args, **kwargs: failure)

    def forbidden(*args, **kwargs):
        pytest.fail("failed precommit must stop before observation or cleanup")

    monkeypatch.setattr(place, "observe_repository", forbidden)
    monkeypatch.setattr(place, "_mutate", forbidden)

    result = place.prune(env.ctx(), [candidate], path=candidate.item.path)

    assert result.refusal.kind == "git-unavailable" and "commit rejected" in result.refusal.detail
    assert result.workspace_commit is failure and result.pruned == place.PruneReport((), (), ())
    assert chain[-1].worktree.is_dir()


@pytest.mark.parametrize(
    "when", ["before-removal", "after-removal", "after-removal-oserror", "after-removal-tip-failure"]
)
def test_prune_failed_observation_preserves_cleanup_evidence(place_env, monkeypatch, when):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1], archived=True)
    _receipt(env, candidate.placement, tip)
    original = place.observe_repository

    def observe(*args, **kwargs):
        observed = original(*args, **kwargs)
        return replace(observed, inventory_known=False)

    def inventory(*args, **kwargs):
        if when == "after-removal-oserror":
            raise OSError("cannot inspect worktree inventory")
        return None

    if when == "before-removal":
        monkeypatch.setattr(place, "observe_repository", observe)
    elif when == "after-removal-tip-failure":
        monkeypatch.setattr(place, "observe_branch_tips", lambda *args, **kwargs: None)
    else:
        monkeypatch.setattr(place, "observe_inventory", inventory)

    result = place.prune(env.ctx(), [candidate])

    assert result.refusal.kind == "git-unavailable"
    assert result.pruned.deleted_branches == ()
    assert result.pruned.removed_worktrees == (() if when == "before-removal" else (str(chain[-1].worktree),))
    assert chain[-1].worktree.exists() is (when == "before-removal")
    assert env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip


@pytest.mark.parametrize("kind", ["branch", "detached", "plain"])
def test_prune_refuses_foreign_computed_checkout(place_env, kind):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1], archived=True)
    _receipt(env, candidate.placement, tip)
    if kind == "branch":
        env.git("switch", "-c", "other", cwd=chain[-1].worktree)
    elif kind == "detached":
        env.git("switch", "--detach", cwd=chain[-1].worktree)
    else:
        env.git("worktree", "remove", str(chain[-1].worktree))
        chain[-1].worktree.mkdir()

    result = place.prune(env.ctx(), [candidate])

    assert result.refusal.kind == "foreign-checkout" and chain[-1].worktree.is_dir()
    assert result.pruned == place.PruneReport((), (), ())
    assert env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip


@pytest.mark.parametrize("change", ["tip", "holder", "deleted-branch"])
def test_prune_reobserves_branch_after_worktree_removal(place_env, monkeypatch, change):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1], archived=True)
    _receipt(env, candidate.placement, tip)
    original = place._mutate
    legacy = env.repo.parent / "legacy"
    newer = None

    def mutate(cwd, *args, **kwargs):
        nonlocal newer
        outcome = original(cwd, *args, **kwargs)
        if args[:2] == ("worktree", "remove") and not isinstance(outcome, place.PlaceRefusal):
            if change == "tip":
                newer = commit_file(env.repo, "newer.txt", "New work\n", "Unreceipted work")
                env.git("update-ref", f"refs/heads/{chain[-1].branch}", newer)
            elif change == "holder":
                env.git("worktree", "add", str(legacy), chain[-1].branch)
            else:
                env.git("branch", "-D", chain[-1].branch)
        return outcome

    monkeypatch.setattr(place, "_mutate", mutate)

    result = place.prune(env.ctx(), [candidate])

    assert result.ok and result.pruned.removed_worktrees == (str(chain[-1].worktree),)
    assert result.pruned.deleted_branches == ()
    if change == "tip":
        assert [entry.why for entry in result.pruned.kept] == ["unmerged"]
        assert env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == newer
    elif change == "holder":
        assert legacy.is_dir() and env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip
        assert result.pruned.kept == (place.PruneKept(candidate.item.path, "code", chain[-1].branch, "occupied"),)


@pytest.mark.parametrize("command", ["remove", "delete-branch"])
def test_prune_failed_mutation_reports_partial_progress(place_env, monkeypatch, command):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1], archived=True)
    _receipt(env, candidate.placement, tip)
    original = provenance.probe_git
    attempts = []
    expected = (
        ("worktree", "remove", str(chain[-1].worktree)) if command == "remove" else ("branch", "-D", chain[-1].branch)
    )

    def probe(cwd, *args, **kwargs):
        if args == expected:
            attempts.append(kwargs)
            return provenance.GitOutcome(None, "", "timeout")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)

    result = place.prune(env.ctx(), [candidate])

    assert result.refusal.kind == "timeout" and len(attempts) == 1 and attempts[0]["timeout"] == 120
    assert result.pruned.removed_worktrees == (() if command == "remove" else (str(chain[-1].worktree),))
    assert result.pruned.deleted_branches == ()
    assert chain[-1].worktree.exists() is (command == "remove")
    assert env.git("rev-parse", f"refs/heads/{chain[-1].branch}") == tip


@pytest.mark.parametrize("failure", ["lock", "checkout-read"])
def test_prune_io_failure_refuses_without_cleanup(place_env, monkeypatch, failure):
    env = place_env
    chain, tip = _created(env)
    candidate = _candidate(env, chain[-1])
    _receipt(env, candidate.placement, tip)
    if failure == "lock":
        lock = env.layout.cache_dir / "placement" / "code.lock"
        lock.unlink()
        lock.mkdir()
    else:
        original = provenance.probe_git

        def probe(cwd, *args, **kwargs):
            if cwd == chain[-1].worktree and args[:1] == ("rev-parse",):
                return provenance.GitOutcome(128, "", "ok", "checkout evidence unavailable")
            return original(cwd, *args, **kwargs)

        monkeypatch.setattr(provenance, "probe_git", probe)

    result = place.prune(env.ctx(), [candidate])

    assert result.refusal.kind == "git-unavailable" and chain[-1].worktree.is_dir()
    assert result.pruned == place.PruneReport((), (), ())


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("scenario", ["open-and-absent", "archived"])
def test_prune_observes_repository_once_without_quadratic_status_reads(place_env, monkeypatch, dry_run, scenario):
    env = place_env
    members = [env.chain_for(env.file(f"candidate-{index}"))[-1] for index in range(3)]
    candidates = []
    for index, member in enumerate(members):
        if index < 2 or scenario == "archived":
            assert place.ensure(env.ctx(), (member,)).ok
        candidate = _candidate(
            env, member, status="resolved" if scenario == "archived" else "open", archived=scenario == "archived"
        )
        candidates.append(candidate)
        if scenario == "archived":
            _receipt(env, candidate.placement, env.git("rev-parse", f"refs/heads/{member.branch}"))
    original_observe = place.observe_repository
    original_probe = provenance.probe_git
    observations, status_reads = [], []
    checkouts = {env.repo, *(member.worktree for member in members)}

    def observe(repo, *, paths=(), **kwargs):
        paths = tuple(paths)
        observations.append((repo, paths))
        return original_observe(repo, paths=paths, **kwargs)

    def probe(cwd, *args, **kwargs):
        if cwd in checkouts and "status" in args:
            status_reads.append((cwd, args))
        return original_probe(cwd, *args, **kwargs)

    monkeypatch.setattr(place, "observe_repository", observe)
    monkeypatch.setattr(provenance, "probe_git", probe)
    monkeypatch.setattr(repo_context, "probe_git", probe)

    result = place.prune(env.ctx(dry_run=dry_run), candidates)

    assert result.ok
    assert observations == [(env.repo, tuple(member.worktree for member in members))]
    # One initial status per inventoried checkout, then one cleanliness check
    # per removed worktree; fresh branch evidence must not scan statuses.
    assert len(status_reads) == (7 if scenario == "archived" else 3)
    if scenario == "archived":
        assert result.pruned.removed_worktrees == tuple(str(member.worktree) for member in members)
        assert result.pruned.deleted_branches == tuple(member.branch for member in members)
    else:
        assert result.pruned.kept == tuple(
            place.PruneKept(member.path, "code", member.branch, "open") for member in members[:2]
        )
