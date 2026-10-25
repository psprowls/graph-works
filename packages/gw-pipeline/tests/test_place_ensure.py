"""Ensure creates and reconciles real Git worktrees without losing ownership."""

import shutil
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from gw_kernel.workspace import provenance
from gw_kernel.workspace.commits import CommitOutcome
from gw_pipeline import place
from gw_pipeline.placement import PlacementEvent


def _leaf(env):
    return env.chain_for(env.file())


def _family(env):
    owner = env.file("owner", kind="Epic")
    return env.chain_for(env.file(parent=owner))


def test_created_from_parent_tip(place_env):
    chain = _leaf(place_env)
    result = place.ensure(place_env.ctx(), chain)
    assert result.ok and result.refusal is None
    assert result.chain[0].action == "created"
    assert result.chain[0].refresh.kind == "none"
    assert place_env.git("rev-parse", "HEAD", cwd=chain[0].worktree) == place_env.git("rev-parse", "main")
    assert place_env.git("symbolic-ref", "--short", "HEAD", cwd=chain[0].worktree) == chain[0].branch


def test_verified_on_second_call(place_env, monkeypatch):
    chain = _leaf(place_env)
    assert place.ensure(place_env.ctx(), chain).ok

    def forbidden(*args, **kwargs):
        pytest.fail("verified worktree must not mutate git")

    monkeypatch.setattr(place, "_mutate", forbidden)
    result = place.ensure(place_env.ctx(), chain)
    assert result.ok and result.chain[0].action == "verified"


@pytest.mark.parametrize(
    "kind,detail",
    [
        ("branch", "other"),
        ("plain", "plain directory"),
        ("repo", "another repository"),
        ("detached", "detached HEAD"),
        ("file", "plain directory"),
    ],
)
def test_foreign_checkout(place_env, git_repo, kind, detail):
    chain = _leaf(place_env)
    worktree = chain[0].worktree
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if kind == "branch":
        place_env.git("worktree", "add", "-b", "other", str(worktree), "main")
    elif kind == "detached":
        place_env.git("worktree", "add", "--detach", str(worktree), "main")
    elif kind == "repo":
        git_repo(worktree, {"foreign.txt": "x"})
    elif kind == "file":
        worktree.touch()
    else:
        worktree.mkdir()
    result = place.ensure(place_env.ctx(), chain)
    assert result.refusal.kind == "foreign-checkout"
    assert detail in result.refusal.detail


def _legacy(env, chain):
    legacy = env.repo.parent / "legacy"
    env.git("worktree", "add", "-b", chain[-1].branch, str(legacy), "main")
    return legacy


def test_adopted_legacy_worktree(place_env):
    chain = _leaf(place_env)
    legacy = _legacy(place_env, chain)
    result = place.ensure(place_env.ctx(), chain)
    assert result.ok and result.chain[0].action == "adopted"
    listing = place_env.git("worktree", "list", "--porcelain")
    assert str(chain[0].worktree) in listing and str(legacy) not in listing
    assert not legacy.exists()


@pytest.mark.parametrize("dirty", [True, False])
def test_occupied_legacy_worktree_is_refused(place_env, dirty):
    chain = _leaf(place_env)
    legacy = _legacy(place_env, chain)
    if dirty:
        (legacy / "dirty").touch()
    ctx = place_env.ctx() if dirty else place_env.ctx(occupied=lambda path: "live dispatch")
    result = place.ensure(ctx, chain)
    assert result.refusal.kind == "occupied"
    assert ("dirty" if dirty else "live dispatch") in result.refusal.detail
    assert legacy.exists() and not chain[0].worktree.exists()


def test_reattached_existing_branch(place_env):
    chain = _leaf(place_env)
    place_env.git("branch", chain[0].branch, "main")
    result = place.ensure(place_env.ctx(), chain)
    assert result.ok and result.chain[0].action == "reattached"


def test_parent_unresolved(place_env):
    chain = place_env.chain_for(place_env.file(), default_base="nope")
    result = place.ensure(place_env.ctx(), chain)
    assert result.refusal.kind == "parent-unresolved"
    assert not chain[0].worktree.exists()


def test_owner_created_before_child_and_child_forks_from_owner(place_env):
    chain = _family(place_env)
    result = place.ensure(place_env.ctx(), chain)
    assert result.ok and [step.action for step in result.chain] == ["created", "created"]
    assert place_env.git("rev-parse", "HEAD", cwd=chain[-1].worktree) == place_env.git("rev-parse", chain[0].branch)


def test_owner_refusal_skips_child(place_env):
    chain = _family(place_env)
    chain[0].worktree.mkdir(parents=True)
    result = place.ensure(place_env.ctx(), chain)
    assert result.refusal.kind == "foreign-checkout"
    assert result.chain[1] == place.PlaceStep(chain[1], "skipped", None, None)
    assert not chain[1].worktree.exists()


def test_timeout_prunes_and_refuses(place_env, monkeypatch):
    chain = _leaf(place_env)
    original = provenance.probe_git
    calls = []

    def probe(cwd, *args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ("worktree", "add"):
            assert kwargs["timeout"] == 120
            return provenance.GitOutcome(None, "", "timeout")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = place.ensure(place_env.ctx(), chain)
    assert result.refusal.kind == "timeout"
    assert sum(args == ("worktree", "prune") for args, _ in calls) == 1


def test_stale_registration_is_pruned_then_reattached(place_env, monkeypatch):
    chain = _leaf(place_env)
    assert place.ensure(place_env.ctx(), chain).ok
    shutil.rmtree(chain[0].worktree)
    original = provenance.probe_git
    calls = []

    def probe(cwd, *args, **kwargs):
        calls.append(args)
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = place.ensure(place_env.ctx(), chain)
    assert result.ok and result.chain[0].action == "reattached"
    assert chain[0].worktree.is_dir()
    assert sum(args[:2] == ("worktree", "add") for args in calls) == 2
    assert calls.count(("worktree", "prune")) == 1


class RecordingNotifier:
    def __init__(self):
        self.events = []

    def placed(self, event):
        self.events.append(event)
        return "orca: recorded"


def test_notifier_gets_one_event_per_creation(place_env):
    chain = _family(place_env)
    notifier = RecordingNotifier()
    ctx = place_env.ctx(notifier=notifier)
    result = place.ensure(ctx, chain)
    assert result.ok
    assert notifier.events == [
        PlacementEvent(p.repo_root, p.worktree, p.parent_worktree, p.slug, ctx.layout.worktrees_dir) for p in chain
    ]
    assert all(step.orca == "orca: recorded" for step in result.chain)
    assert place.ensure(ctx, chain).ok and len(notifier.events) == 2


def test_raising_notifier_becomes_a_note(place_env):
    class RaisingNotifier:
        def placed(self, event):
            raise RuntimeError("x")

    result = place.ensure(place_env.ctx(notifier=RaisingNotifier()), _leaf(place_env))
    assert result.ok and result.chain[0].orca == "orca: notifier failed (x)"


def test_precommit_commits_only_item_paths(place_env, git_repo):
    env = place_env
    chain = _leaf(env)
    workflow = env.layout.root / "workflow.yaml"
    workflow.write_text(
        "workflow:\n  workspace_commits: on\n" + workflow.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    git_repo(env.layout.root, {})
    env.git("config", "user.email", "place@example.test", cwd=env.layout.root)
    env.git("config", "user.name", "Placement Tests", cwd=env.layout.root)
    env.git("commit", "-m", "Initial workspace", cwd=env.layout.root)
    page = env.layout.bundle_dir / f"{chain[-1].path}.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nItem edit\n", encoding="utf-8", newline="\n")
    log = env.layout.bundle_dir / "log.md"
    log.write_text(log.read_text(encoding="utf-8") + "\nUnrelated edit\n", encoding="utf-8", newline="\n")
    result = place.ensure(env.ctx(), chain)
    assert result.ok and result.workspace_commit.status == "committed"
    assert result.workspace_commit.paths
    assert all(
        path in {f"okf/{chain[-1].path}.md", f"okf/{chain[-1].path}"} or path.startswith(f"okf/{chain[-1].path}/")
        for path in result.workspace_commit.paths
    )
    assert result.dirty_workspace_paths == ("okf/log.md",)
    assert env.git("log", "-1", "--format=%s", cwd=env.layout.root) == "workspace: place feature-leaf"


@pytest.mark.parametrize("reason", ["commit hook rejected the item", None])
def test_failed_precommit_refuses_before_placement(place_env, monkeypatch, reason):
    chain = _family(place_env)
    notifier = RecordingNotifier()
    failed = CommitOutcome("failed", None, "workspace: place feature-leaf", (), reason)
    monkeypatch.setattr(place, "commit_pending", lambda *args, **kwargs: failed)
    observations = []
    mutations = []
    observe = place.observe_repository
    mutate = place._mutate

    def recording_observe(*args, **kwargs):
        observations.append(args)
        return observe(*args, **kwargs)

    def recording_mutate(*args, **kwargs):
        mutations.append(args)
        return mutate(*args, **kwargs)

    monkeypatch.setattr(place, "observe_repository", recording_observe)
    monkeypatch.setattr(place, "_mutate", recording_mutate)
    result = place.ensure(place_env.ctx(notifier=notifier), chain)
    assert not result.ok and result.refusal.kind == "git-unavailable"
    assert (reason or "workspace commit failed") in result.refusal.detail
    assert result.workspace_commit is failed
    assert [step.action for step in result.chain] == ["skipped", "skipped"]
    assert observations == [] and mutations == [] and notifier.events == []
    assert not any(member.worktree.exists() for member in chain)


def test_concurrent_siblings_create_owner_once(place_env):
    env = place_env
    owner = env.file("owner", kind="Epic")
    chains = [env.chain_for(env.file(name, parent=owner)) for name in ("one", "two")]
    barrier = Barrier(2)

    def run(chain):
        ctx = env.ctx()
        barrier.wait(timeout=10)
        return place.ensure(ctx, chain)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, chains))
    assert all(result.ok for result in results)
    assert sorted(result.chain[0].action for result in results) == ["created", "verified"]


def test_observation_uses_gate_executable(place_env, monkeypatch):
    from gw_pipeline import repo_context

    chain = _leaf(place_env)
    ctx = place_env.ctx()
    original = repo_context.probe_git
    seen = []

    def probe(cwd, *args, **kwargs):
        seen.append(kwargs.get("executable"))
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(repo_context, "probe_git", probe)
    result = place.ensure(ctx, chain)
    assert result.ok and seen and set(seen) == {ctx.git.path}


def test_repository_lock_failure_refuses(place_env):
    chain = _leaf(place_env)
    (place_env.layout.cache_dir / "placement").touch()
    result = place.ensure(place_env.ctx(), chain)
    assert result.refusal.kind == "git-unavailable"
    assert not chain[0].worktree.exists()


def test_checkout_identity_read_failure_refuses_git_unavailable(place_env, monkeypatch):
    chain = _leaf(place_env)
    ctx = place_env.ctx()
    assert place.ensure(ctx, chain).ok
    original = provenance.probe_git

    def probe(cwd, *args, **kwargs):
        if cwd == chain[0].worktree and args[:1] == ("rev-parse",):
            return provenance.GitOutcome(None, "", "error")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = place.ensure(ctx, chain)
    assert result.refusal.kind == "git-unavailable"


def test_checkout_head_read_error_is_not_reported_as_detached(place_env, monkeypatch):
    chain = _leaf(place_env)
    ctx = place_env.ctx()
    assert place.ensure(ctx, chain).ok
    original = provenance.probe_git

    def probe(cwd, *args, **kwargs):
        if cwd == chain[0].worktree and args[:1] == ("symbolic-ref",):
            return provenance.GitOutcome(128, "", "ok", "HEAD read failed")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = place.ensure(ctx, chain)
    assert result.refusal.kind == "git-unavailable"
    assert "HEAD read failed" in result.refusal.detail


def test_nested_workspace_excludes_own_dirty_paths(place_env, git_repo):
    env = place_env
    chain = _leaf(env)
    git_repo(env.layout.root.parent, {})
    env.git("config", "user.email", "place@example.test", cwd=env.layout.root.parent)
    env.git("config", "user.name", "Placement Tests", cwd=env.layout.root.parent)
    env.git("commit", "-m", "Initial host", cwd=env.layout.root.parent)
    # Auto commits intentionally skip a workspace embedded in another repository.
    page = env.layout.bundle_dir / f"{chain[-1].path}.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nOwned edit\n", encoding="utf-8", newline="\n")
    result = place.ensure(env.ctx(), chain)
    assert result.ok and result.workspace_commit.status == "skipped"
    assert result.dirty_workspace_paths == ()


def test_git_mutation_failure_reports_stderr(place_env, monkeypatch):
    chain = _leaf(place_env)
    original = provenance.probe_git

    def probe(cwd, *args, **kwargs):
        if args[:2] == ("worktree", "add"):
            return provenance.GitOutcome(128, "", "ok", "placement test failure")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = place.ensure(place_env.ctx(), chain)
    assert result.refusal.kind == "git-unavailable" and "placement test failure" in result.refusal.detail


def test_unknown_item_refuses(place_env):
    chain = _leaf(place_env)
    (place_env.layout.bundle_dir / f"{chain[-1].path}.md").unlink()
    result = place.ensure(place_env.ctx(), chain)
    assert result.refusal.kind == "unknown-item"
    assert not chain[0].worktree.exists()


def test_empty_chain_is_noop(place_env):
    result = place.ensure(place_env.ctx(), ())
    assert result.ok and result.path is None and result.placement is None and result.chain == ()


def test_dry_run_predicts_chain_without_creating_worktrees(place_env):
    chain = _family(place_env)
    notifier = RecordingNotifier()
    result = place.ensure(place_env.ctx(dry_run=True, notifier=notifier), chain)
    assert result.ok and result.dry_run and result.workspace_commit is None
    assert [step.action for step in result.chain] == ["created", "created"]
    assert not any(member.worktree.exists() for member in chain)
    assert notifier.events == []
