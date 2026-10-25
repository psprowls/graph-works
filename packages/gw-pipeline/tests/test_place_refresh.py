"""Parent refresh merges local ancestry and leaves refused merges clean."""

import pytest
from gw_kernel.workspace import provenance
from gw_pipeline import place


def _commit(env, worktree, name, content="change\n"):
    path = worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    env.git("add", name, cwd=worktree)
    env.git("commit", "-m", f"Change {name}", cwd=worktree)
    return env.git("rev-parse", "HEAD", cwd=worktree)


def _leaf(env):
    chain = env.chain_for(env.file())
    assert place.ensure(env.ctx(), chain).ok
    return chain


def _diverged(env):
    chain = _leaf(env)
    _commit(env, chain[-1].worktree, "leaf.txt")
    _commit(env, env.repo, "parent.txt")
    return chain


def _assert_clean(env, worktree):
    assert env.git("status", "--porcelain", cwd=worktree) == ""
    merge_head = env.git("rev-parse", "--git-path", "MERGE_HEAD", cwd=worktree)
    assert not (worktree / merge_head).exists()


@pytest.mark.parametrize("action", ["verified", "adopted", "reattached"])
def test_fast_forward_when_branch_has_no_commits(place_env, action):
    env = place_env
    chain = _leaf(env)
    old = env.git("rev-parse", "HEAD", cwd=chain[-1].worktree)
    if action == "adopted":
        env.git("worktree", "move", str(chain[-1].worktree), str(env.repo.parent / "legacy"))
    elif action == "reattached":
        env.git("worktree", "remove", str(chain[-1].worktree))
    tip = _commit(env, env.repo, "parent.txt")
    result = place.ensure(env.ctx(), chain)
    assert result.ok
    assert result.chain[-1].action == action
    assert result.chain[-1].refresh == place.Refresh("ff", old, tip)
    assert env.git("rev-parse", "HEAD", cwd=chain[-1].worktree) == tip
    assert env.git("rev-list", "--merges", "HEAD", cwd=chain[-1].worktree) == ""


def test_merge_commit_when_branch_has_commits(place_env):
    env = place_env
    chain = _diverged(env)
    old = env.git("rev-parse", "HEAD", cwd=chain[-1].worktree)
    parent = env.git("rev-parse", "main")
    result = place.ensure(env.ctx(), chain)
    assert result.ok
    refresh = result.chain[-1].refresh
    assert refresh.kind == "merge" and refresh.from_sha == old
    assert env.git("rev-list", "--parents", "-1", "HEAD", cwd=chain[-1].worktree).split() == [
        refresh.to_sha,
        old,
        parent,
    ]
    assert env.git("log", "-1", "--format=%s", cwd=chain[-1].worktree) == f"Merge main into {chain[-1].branch}"


@pytest.mark.parametrize("own_commit", [False, True])
def test_parent_named_tag_cannot_redirect_refresh(place_env, own_commit):
    env = place_env
    chain = _leaf(env)
    worktree = chain[-1].worktree
    if own_commit:
        _commit(env, worktree, "leaf.txt")
    before = env.git("rev-parse", "HEAD", cwd=worktree)
    # At the base this tag hides the parent's advance. At the item tip it also
    # makes a short-name rev-list count zero despite the branches diverging.
    env.git("tag", "main", "HEAD", cwd=worktree)
    parent = _commit(env, env.repo, "parent.txt")

    result = place.ensure(env.ctx(), chain)

    assert result.ok
    refresh = result.chain[-1].refresh
    assert refresh.kind == ("merge" if own_commit else "ff")
    assert refresh.from_sha == before
    if own_commit:
        assert env.git("rev-list", "--parents", "-1", "HEAD", cwd=worktree).split() == [
            refresh.to_sha,
            before,
            parent,
        ]
        assert env.git("log", "-1", "--format=%s", cwd=worktree) == f"Merge main into {chain[-1].branch}"
    else:
        assert refresh.to_sha == parent
        assert env.git("rev-parse", "HEAD", cwd=worktree) == parent
    assert (worktree / "parent.txt").read_text(encoding="utf-8") == "change\n"


def test_up_to_date_is_none(place_env):
    env = place_env
    chain = _leaf(env)
    tip = _commit(env, chain[-1].worktree, "leaf.txt")
    result = place.ensure(env.ctx(), chain)
    assert result.ok
    assert result.chain[-1].refresh == place.Refresh("none", tip, tip)


def _conflicting(env):
    _commit(env, env.repo, "src/a.py", "initial\n")
    chain = _leaf(env)
    _commit(env, chain[-1].worktree, "src/a.py", "leaf\n")
    _commit(env, env.repo, "src/a.py", "parent\n")
    return chain


def test_conflict_aborts_and_lists_paths(place_env):
    env = place_env
    chain = _conflicting(env)
    old = env.git("rev-parse", "HEAD", cwd=chain[-1].worktree)
    result = place.ensure(env.ctx(), chain)
    assert result.refusal.kind == "refresh-conflict"
    assert "src/a.py" in result.refusal.detail
    assert "resolve the merge in the worktree and rerun gw work place" in result.refusal.detail
    assert env.git("rev-parse", "HEAD", cwd=chain[-1].worktree) == old
    _assert_clean(env, chain[-1].worktree)


def test_dirty_worktree_refuses_refresh(place_env):
    env = place_env
    chain = _leaf(env)
    (chain[-1].worktree / "untracked.txt").touch()
    result = place.ensure(env.ctx(), chain)
    assert result.refusal.kind == "dirty-worktree"
    assert str(chain[-1].worktree) in result.refusal.detail


@pytest.mark.parametrize("existing_child", [True, False])
def test_child_refreshes_after_owner(place_env, monkeypatch, existing_child):
    env = place_env
    owner = env.file("owner", kind="Epic")
    chain = env.chain_for(env.file(parent=owner))
    assert place.ensure(env.ctx(), chain if existing_child else chain[:1]).ok
    tip = _commit(env, env.repo, "parent.txt")
    original = place.observe_repository
    observations = []

    def observe(*args, **kwargs):
        observations.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(place, "observe_repository", observe)
    result = place.ensure(env.ctx(), chain)
    assert result.ok
    assert len(observations) == 1
    assert [step.refresh.kind for step in result.chain] == ["ff", "ff" if existing_child else "none"]
    assert result.chain[-1].action == ("verified" if existing_child else "created")
    assert result.chain[-1].refresh.to_sha == tip
    assert env.git("rev-parse", "HEAD", cwd=chain[-1].worktree) == tip


def test_merge_without_identity_refuses_cleanly(place_env, monkeypatch, tmp_path):
    env = place_env
    chain = _diverged(env)
    env.git("config", "--unset-all", "user.name")
    env.git("config", "--unset-all", "user.email")
    env.git("config", "user.useConfigOnly", "true")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "missing-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(key, raising=False)
    result = place.ensure(env.ctx(), chain)
    assert result.refusal.kind == "git-unavailable"
    assert "identity" in result.refusal.detail.lower()
    _assert_clean(env, chain[-1].worktree)


def test_refresh_timeout_stays_timeout(place_env, monkeypatch):
    env = place_env
    chain = _leaf(env)
    _commit(env, env.repo, "parent.txt")
    original = provenance.probe_git

    def probe(cwd, *args, **kwargs):
        if args[:2] == ("merge", "--ff-only"):
            assert kwargs["timeout"] == 120
            return provenance.GitOutcome(None, "", "timeout")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = place.ensure(env.ctx(), chain)
    assert result.refusal.kind == "timeout"
    _assert_clean(env, chain[-1].worktree)


def test_refresh_timeout_aborts_in_progress_merge(place_env, monkeypatch):
    env = place_env
    chain = _conflicting(env)
    original = provenance.probe_git

    def probe(cwd, *args, **kwargs):
        outcome = original(cwd, *args, **kwargs)
        if args[:2] == ("merge", "--no-ff"):
            assert outcome.returncode != 0
            return provenance.GitOutcome(None, "", "timeout")
        return outcome

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = place.ensure(env.ctx(), chain)
    assert result.refusal.kind == "timeout"
    _assert_clean(env, chain[-1].worktree)


def test_abort_failure_is_not_reported_as_clean_conflict(place_env, monkeypatch):
    env = place_env
    chain = _conflicting(env)
    original = provenance.probe_git

    def probe(cwd, *args, **kwargs):
        if args == ("merge", "--abort"):
            return provenance.GitOutcome(128, "", "ok", "cannot abort merge")
        return original(cwd, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(provenance, "probe_git", probe)
        result = place.ensure(env.ctx(), chain)
    assert result.refusal.kind == "git-unavailable"
    assert "cleanup" in result.refusal.detail
    assert env.git("status", "--porcelain", cwd=chain[-1].worktree)
    env.git("merge", "--abort", cwd=chain[-1].worktree)
    _assert_clean(env, chain[-1].worktree)


def test_refresh_false_skips_refresh_and_dirty_check(place_env):
    env = place_env
    chain = _leaf(env)
    worktree = chain[-1].worktree
    before = env.git("rev-parse", "HEAD", cwd=worktree)
    _commit(env, env.repo, "parent.txt")
    (worktree / "dirty.txt").write_text("x\n", encoding="utf-8", newline="\n")

    refused = place.ensure(env.ctx(), chain)
    assert refused.refusal is not None and refused.refusal.kind == "dirty-worktree"

    result = place.ensure(env.ctx(), chain, refresh=False)

    assert result.ok
    assert result.chain[-1].action == "verified"
    assert env.git("rev-parse", "HEAD", cwd=worktree) == before


def test_refresh_false_still_refuses_foreign_checkout(place_env):
    env = place_env
    chain = env.chain_for(env.file())
    chain[-1].worktree.mkdir(parents=True)
    result = place.ensure(env.ctx(), chain, refresh=False)
    assert result.refusal is not None and result.refusal.kind == "foreign-checkout"


def test_refresh_false_refuses_a_missing_worktree_and_never_creates_it(place_env):
    env = place_env
    chain = env.chain_for(env.file())
    assert not chain[-1].worktree.exists()
    result = place.ensure(env.ctx(), chain, refresh=False)
    assert result.refusal is not None and result.refusal.kind == "foreign-checkout"
    assert "verify-only" in result.refusal.detail
    assert not chain[-1].worktree.exists()
