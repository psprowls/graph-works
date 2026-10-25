"""Dry-run placement predicts local refreshes without changing checkout state."""

import os

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


def _snapshot(env):
    return (
        env.git("worktree", "list", "--porcelain"),
        env.git("for-each-ref", "refs/heads"),
        env.git("status", "--porcelain", cwd=env.layout.root),
        env.git("rev-parse", "HEAD", cwd=env.layout.root),
        (env.layout.root / ".git" / "index").read_bytes(),
    )


def test_dry_run_mutates_nothing(place_env, git_repo, monkeypatch):
    env = place_env
    owner = env.file("owner", kind="Epic")
    chain = env.chain_for(env.file(parent=owner))
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
    before = _snapshot(env)

    def forbidden(*args, **kwargs):
        pytest.fail("dry run must skip pre-commit and git mutation")

    monkeypatch.setattr(place, "commit_pending", forbidden)
    monkeypatch.setattr(place, "_mutate", forbidden)
    result = place.ensure(env.ctx(dry_run=True), chain)

    assert result.ok and result.dry_run and result.workspace_commit is None
    assert [step.action for step in result.chain] == ["created", "created"]
    parent = env.git("rev-parse", "refs/heads/main")
    assert [step.refresh for step in result.chain] == [place.Refresh("none", parent, parent)] * 2
    assert not any(member.worktree.exists() for member in chain)
    assert _snapshot(env) == before


@pytest.mark.parametrize("kind", ["foreign-checkout", "occupied", "parent-unresolved", "dirty-worktree"])
def test_dry_run_proven_refusals_match_live(place_env, kind):
    env = place_env
    chain = env.chain_for(env.file(), default_base="missing" if kind == "parent-unresolved" else "main")
    if kind == "foreign-checkout":
        chain[-1].worktree.mkdir(parents=True)
    elif kind == "occupied":
        legacy = env.repo.parent / "legacy"
        env.git("worktree", "add", "-b", chain[-1].branch, str(legacy), "main")
        (legacy / "dirty").touch()
    elif kind == "dirty-worktree":
        assert place.ensure(env.ctx(), chain).ok
        (chain[-1].worktree / "dirty").touch()
    predicted = place.ensure(env.ctx(dry_run=True), chain)
    live = place.ensure(env.ctx(), chain)
    assert predicted.refusal is not None and predicted.refusal.kind == kind
    assert live.refusal.kind == predicted.refusal.kind


@pytest.mark.parametrize("kind", ["ff", "merge", "conflict", "none"])
def test_dry_run_predicts_ff_merge_conflict(place_env, kind):
    env = place_env
    _commit(env, env.repo, "src/a.py", "initial\n")
    chain = _leaf(env)
    worktree = chain[-1].worktree
    if kind != "ff":
        _commit(env, worktree, "src/a.py" if kind == "conflict" else "leaf.txt", "leaf\n")
    before = env.git("rev-parse", "HEAD", cwd=worktree)
    parent = env.git("rev-parse", "refs/heads/main")
    if kind != "none":
        parent = _commit(env, env.repo, "src/a.py" if kind == "conflict" else "parent.txt", "parent\n")
    refs = env.git("for-each-ref", "refs/heads")
    index = (worktree / env.git("rev-parse", "--git-path", "index", cwd=worktree)).read_bytes()

    result = place.ensure(env.ctx(dry_run=True), chain)

    assert result.chain[-1].refresh == place.Refresh(
        kind, before, before if kind == "none" else parent, ("src/a.py",) if kind == "conflict" else ()
    )
    if kind == "conflict":
        assert result.refusal.kind == "refresh-conflict"
        assert "src/a.py" in result.refusal.detail
    else:
        assert result.ok
    assert env.git("for-each-ref", "refs/heads") == refs
    assert env.git("status", "--porcelain", cwd=worktree) == ""
    assert (worktree / env.git("rev-parse", "--git-path", "index", cwd=worktree)).read_bytes() == index


@pytest.mark.parametrize("own_commit", [False, True])
def test_dry_run_parent_named_tag_cannot_redirect_prediction(place_env, own_commit):
    env = place_env
    chain = _leaf(env)
    worktree = chain[-1].worktree
    if own_commit:
        _commit(env, worktree, "leaf.txt")
    before = env.git("rev-parse", "HEAD", cwd=worktree)
    env.git("tag", "main", "HEAD", cwd=worktree)
    parent = _commit(env, env.repo, "parent.txt")
    result = place.ensure(env.ctx(dry_run=True), chain)
    assert result.ok
    assert result.chain[-1].refresh == place.Refresh("merge" if own_commit else "ff", before, parent)


@pytest.mark.parametrize("action", ["adopted", "reattached"])
def test_dry_run_predicts_refresh_without_computed_checkout(place_env, action):
    env = place_env
    chain = _leaf(env)
    worktree = chain[-1].worktree
    before = env.git("rev-parse", "HEAD", cwd=worktree)
    if action == "adopted":
        env.git("worktree", "move", str(worktree), str(env.repo.parent / "legacy"))
    else:
        env.git("worktree", "remove", str(worktree))
        (env.repo / "unrelated-dirty").touch()
    parent = _commit(env, env.repo, "parent.txt")
    result = place.ensure(env.ctx(dry_run=True), chain)
    assert result.ok and result.chain[-1].action == action
    assert result.chain[-1].refresh == place.Refresh("ff", before, parent)
    assert not worktree.exists()


@pytest.mark.parametrize("dry_run", [True, False])
def test_parent_named_tag_does_not_hide_creation_parent_tip(place_env, dry_run):
    env = place_env
    chain = env.chain_for(env.file())
    env.git("tag", "main", "HEAD")
    parent = _commit(env, env.repo, "parent.txt")
    result = place.ensure(env.ctx(dry_run=dry_run), chain)
    assert result.ok and result.chain[-1].action == "created"
    assert result.chain[-1].refresh == place.Refresh("none", parent, parent)
    if not dry_run:
        assert env.git("rev-parse", "HEAD", cwd=chain[-1].worktree) == parent


@pytest.mark.parametrize("existing_child", [False, True])
def test_dry_run_child_uses_predicted_owner_fast_forward(place_env, existing_child):
    env = place_env
    owner = env.file("owner", kind="Epic")
    chain = env.chain_for(env.file(parent=owner))
    assert place.ensure(env.ctx(), chain if existing_child else chain[:1]).ok
    parent = _commit(env, env.repo, "parent.txt")
    result = place.ensure(env.ctx(dry_run=True), chain)
    assert result.ok
    assert [step.refresh.kind for step in result.chain] == ["ff", "ff" if existing_child else "none"]
    assert result.chain[-1].action == ("verified" if existing_child else "created")
    assert result.chain[-1].refresh.to_sha == parent
    assert chain[-1].worktree.exists() is existing_child


@pytest.mark.parametrize("child", ["missing", "unchanged", "committed", "conflict"])
def test_dry_run_owner_merge_descendant_matches_live(place_env, child):
    env = place_env
    _commit(env, env.repo, "src/a.py", "initial\n")
    owner = env.file("owner", kind="Epic")
    chain = env.chain_for(env.file(parent=owner))
    assert place.ensure(env.ctx(), chain[:1] if child == "missing" else chain).ok
    _commit(env, chain[0].worktree, "owner.txt")
    if child in ("committed", "conflict"):
        _commit(env, chain[-1].worktree, "src/a.py" if child == "conflict" else "child.txt", "child\n")
    child_before = None if child == "missing" else env.git("rev-parse", "HEAD", cwd=chain[-1].worktree)
    parent = _commit(env, env.repo, "src/a.py", "parent\n")
    refs = env.git("for-each-ref", "refs/heads")

    predicted = place.ensure(env.ctx(dry_run=True), chain)

    want = {"missing": "none", "unchanged": "ff", "committed": "merge", "conflict": "conflict"}[child]
    assert predicted.chain[0].refresh.kind == "merge"
    assert predicted.chain[0].refresh.to_sha == parent
    assert predicted.chain[-1].action == ("created" if child == "missing" else "verified")
    assert predicted.chain[-1].refresh == place.Refresh(
        want, child_before, None, ("src/a.py",) if child == "conflict" else ()
    )
    assert predicted.warnings == ()
    assert env.git("for-each-ref", "refs/heads") == refs
    assert chain[-1].worktree.exists() is (child != "missing")

    live = place.ensure(env.ctx(), chain)
    if child == "conflict":
        assert predicted.refusal.kind == live.refusal.kind == "refresh-conflict"
        assert "src/a.py" in live.refusal.detail
    else:
        assert predicted.ok and live.ok
        assert [step.refresh.kind for step in predicted.chain] == [step.refresh.kind for step in live.chain]
        assert (chain[-1].worktree / "owner.txt").read_text(encoding="utf-8") == "change\n"
        assert (chain[-1].worktree / "src/a.py").read_text(encoding="utf-8") == "parent\n"


@pytest.mark.parametrize("middle_exists", [True, False])
def test_dry_run_multilevel_owner_merges_predict_child_conflict(place_env, monkeypatch, middle_exists):
    env = place_env
    _commit(env, env.repo, "src/a.py", "initial\n")
    outer = env.file("outer", kind="Epic")
    inner = env.file("inner", parent=outer)
    chain = env.chain_for(env.file("leaf", kind="Bug", parent=inner))
    assert len(chain) == 3 and place.ensure(env.ctx(), chain).ok
    _commit(env, chain[0].worktree, "outer.txt")
    if middle_exists:
        _commit(env, chain[1].worktree, "inner.txt")
    else:
        env.git("worktree", "remove", str(chain[1].worktree))
        env.git("branch", "-D", chain[1].branch)
    _commit(env, chain[2].worktree, "src/a.py", "leaf\n")
    _commit(env, env.repo, "src/a.py", "parent\n")
    indexes = [
        member.worktree / env.git("rev-parse", "--git-path", "index", cwd=member.worktree)
        for member in chain
        if member.worktree.exists()
    ]
    before = (env.git("for-each-ref", "refs/heads"), env.git("worktree", "list", "--porcelain"))
    index_bytes = [index.read_bytes() for index in indexes]

    def forbidden(*args, **kwargs):
        pytest.fail("dry run must not mutate checkout state")

    with monkeypatch.context() as patch:
        patch.setattr(place, "_mutate", forbidden)
        patch.setattr(place, "commit_pending", forbidden)
        predicted = place.ensure(env.ctx(dry_run=True), chain)

    assert predicted.refusal is not None and predicted.refusal.kind == "refresh-conflict"
    assert [step.refresh.kind for step in predicted.chain] == [
        "merge",
        "merge" if middle_exists else "none",
        "conflict",
    ]
    assert predicted.chain[-1].refresh.conflicts == ("src/a.py",)
    assert predicted.chain[-1].refresh.to_sha is None
    assert predicted.warnings == ()
    assert (env.git("for-each-ref", "refs/heads"), env.git("worktree", "list", "--porcelain")) == before
    assert [index.read_bytes() for index in indexes] == index_bytes

    live = place.ensure(env.ctx(), chain)
    assert live.refusal.kind == predicted.refusal.kind
    assert [step.refresh.kind for step in live.chain[:-1]] == ["merge", "merge" if middle_exists else "none"]
    assert "src/a.py" in live.refusal.detail


def test_dry_run_virtual_parent_refuses_multiple_merge_bases(place_env):
    env = place_env
    owner = env.file("owner", kind="Epic")
    chain = env.chain_for(env.file(parent=owner))
    assert place.ensure(env.ctx(), chain).ok
    first = _commit(env, chain[0].worktree, "owner.txt")
    second = _commit(env, chain[-1].worktree, "child.txt")
    env.git("merge", "--no-ff", "--no-edit", second, cwd=chain[0].worktree)
    env.git("merge", "--no-ff", "--no-edit", first, cwd=chain[-1].worktree)
    _commit(env, env.repo, "parent.txt")

    predicted = place.ensure(env.ctx(dry_run=True), chain)

    assert predicted.refusal is not None and predicted.refusal.kind == "git-unavailable"
    assert "multiple merge bases" in predicted.refusal.detail
    assert chain[-1].branch in predicted.refusal.detail


@pytest.mark.parametrize("read", ["merge-base", "merge-tree"])
def test_dry_run_virtual_parent_read_failure_is_refused(place_env, monkeypatch, read):
    env = place_env
    owner = env.file("owner", kind="Epic")
    chain = env.chain_for(env.file(parent=owner))
    assert place.ensure(env.ctx(), chain).ok
    _commit(env, chain[0].worktree, "owner.txt")
    _commit(env, chain[-1].worktree, "child.txt")
    _commit(env, env.repo, "parent.txt")
    original = provenance.probe_git

    def probe(cwd, *args, **kwargs):
        matches = (
            args[:2] == ("merge-base", "--all")
            if read == "merge-base"
            else args[0] == "merge-tree" and any(arg.startswith("--merge-base=") for arg in args)
        )
        if matches:
            return provenance.GitOutcome(128, "", "ok", "virtual ancestry diagnostic")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    predicted = place.ensure(env.ctx(dry_run=True), chain)
    assert predicted.refusal.kind == "git-unavailable"
    assert "virtual ancestry diagnostic" in predicted.refusal.detail


def test_dry_run_skips_notifier(place_env):
    class RecordingNotifier:
        def __init__(self):
            self.events = []

        def placed(self, event):
            self.events.append(event)

    notifier = RecordingNotifier()
    chain = place_env.chain_for(place_env.file())
    result = place.ensure(place_env.ctx(dry_run=True, notifier=notifier), chain)
    assert result.ok and notifier.events == [] and result.chain[-1].orca is None


@pytest.mark.parametrize("scope", ["worktree", "workspace", "repository", "legacy"])
def test_dry_run_does_not_refresh_index_stat_cache(place_env, git_repo, scope):
    env = place_env
    chain = _leaf(env)
    if scope == "workspace":
        git_repo(env.layout.root, {})
        env.git("config", "user.email", "place@example.test", cwd=env.layout.root)
        env.git("config", "user.name", "Placement Tests", cwd=env.layout.root)
        env.git("commit", "-m", "Initial workspace", cwd=env.layout.root)
    if scope == "legacy":
        checkout = env.repo.parent / "legacy"
        env.git("worktree", "add", "-b", "unrelated-legacy", str(checkout), "main")
    else:
        checkout = {"worktree": chain[-1].worktree, "workspace": env.layout.root, "repository": env.repo}[scope]
    _commit(env, checkout, "tracked.txt")
    # Make the index's file-stat cache stale while preserving identical content.
    tracked = checkout / "tracked.txt"
    stat = tracked.stat()
    os.utime(tracked, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    index = checkout / env.git("rev-parse", "--git-path", "index", cwd=checkout)
    before = index.read_bytes()

    result = place.ensure(env.ctx(dry_run=True), chain)

    assert result.ok
    assert index.read_bytes() == before


@pytest.mark.parametrize(
    "outcome",
    [
        provenance.GitOutcome(128, "", "ok", "merge-tree diagnostic"),
        provenance.GitOutcome(None, "", "timeout"),
        provenance.GitOutcome(None, "", "missing"),
        provenance.GitOutcome(None, "", "error"),
    ],
)
def test_dry_run_merge_tree_failure_is_not_a_conflict(place_env, monkeypatch, outcome):
    env = place_env
    chain = _leaf(env)
    _commit(env, chain[-1].worktree, "leaf.txt")
    _commit(env, env.repo, "parent.txt")
    original = provenance.probe_git

    def probe(cwd, *args, **kwargs):
        if args[:1] == ("merge-tree",):
            return outcome
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = place.ensure(env.ctx(dry_run=True), chain)
    assert result.refusal is not None and result.refusal.kind == "git-unavailable"
    assert "merge-tree" in result.refusal.detail
    if outcome.stderr:
        assert outcome.stderr in result.refusal.detail


@pytest.mark.parametrize("read", ["dirty", "parent", "head", "ancestor", "count"])
def test_dry_run_preserves_strict_refresh_read_failures(place_env, monkeypatch, read):
    env = place_env
    chain = _leaf(env)
    worktree = chain[-1].worktree
    _commit(env, env.repo, "parent.txt")
    original = provenance.probe_git
    failed_args = {
        "dirty": ("--no-optional-locks", "status", "--porcelain", "-z", "--"),
        "parent": ("rev-parse", "--verify", "--end-of-options", "refs/heads/main^{commit}"),
        "head": ("rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"),
        "count": ("rev-list", "--count", "refs/heads/main..HEAD"),
    }

    def probe(cwd, *args, **kwargs):
        matches = args[:2] == ("merge-base", "--is-ancestor") if read == "ancestor" else args == failed_args[read]
        if cwd == worktree and matches:
            return provenance.GitOutcome(128, "", "ok", f"{read} diagnostic")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(provenance, "probe_git", probe)
    result = place.ensure(env.ctx(dry_run=True), chain)
    assert result.refusal.kind == ("parent-unresolved" if read == "parent" else "git-unavailable")
    assert f"{read} diagnostic" in result.refusal.detail
