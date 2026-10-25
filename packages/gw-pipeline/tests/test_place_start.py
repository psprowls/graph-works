"""Start records the refreshed execute baseline through workspace transactions."""

from dataclasses import replace
from datetime import date

import pytest
from gw_kernel.workspace import provenance
from gw_pipeline import place, transactions
from okf_io import parse


def _page(env, path):
    return env.layout.bundle_dir / f"{path}.md"


def _edit(env, path, **fields):
    page = _page(env, path)
    document = parse(page.read_text(encoding="utf-8"), path=page)
    for name, value in fields.items():
        document.set(name, value)
    page.write_text(document.serialize(), encoding="utf-8", newline="\n")


def _item(env, *, phase="execute", parent=None):
    path = env.file(parent=parent)
    _edit(env, path, phase=phase, updated=date(2026, 10, 1))
    return env.chain_for(path)


def _commit(env, worktree, name):
    (worktree / name).write_text(f"{name}\n", encoding="utf-8", newline="\n")
    env.git("add", name, cwd=worktree)
    env.git("commit", "-m", f"Change {name}", cwd=worktree)
    return env.git("rev-parse", "HEAD", cwd=worktree)


def _workspace(env, git_repo):
    workflow = env.layout.root / "workflow.yaml"
    workflow.write_text(
        "workflow:\n  workspace_commits: on\n" + workflow.read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    git_repo(env.layout.root, {})
    env.git("config", "user.email", "place@example.test", cwd=env.layout.root)
    env.git("config", "user.name", "Placement Tests", cwd=env.layout.root)
    env.git("commit", "-m", "Initial workspace", cwd=env.layout.root)


def test_start_records_head_after_refresh(place_env, git_repo):
    env = place_env
    chain = _item(env)
    assert place.ensure(env.ctx(), chain).ok
    before = parse(_page(env, chain[-1].path).read_text(encoding="utf-8")).fm_data(dates="iso")
    _workspace(env, git_repo)
    commits = int(env.git("rev-list", "--count", "HEAD", cwd=env.layout.root))
    parent = _commit(env, env.repo, "parent.txt")

    result = place.start(env.ctx(), chain)

    assert result.ok and result.mode == "start"
    assert result.chain[-1].refresh.kind == "ff"
    assert result.start_sha == (None, parent)
    assert result.application.ok and result.application.commit.status == "committed"
    after = parse(_page(env, chain[-1].path).read_text(encoding="utf-8")).fm_data(dates="iso")
    assert after == {**before, "start_sha": parent, "updated": "2026-10-22"}
    assert int(env.git("rev-list", "--count", "HEAD", cwd=env.layout.root)) == commits + 1
    assert env.git("log", "-1", "--format=%s", cwd=env.layout.root) == (
        "workspace: record feature-leaf execute baseline"
    )


@pytest.mark.parametrize("advance", ["equal", "own-commit", "refresh-merge"])
def test_start_keeps_recorded_baseline(place_env, git_repo, advance):
    env = place_env
    chain = _item(env)
    _workspace(env, git_repo)
    first = place.start(env.ctx(), chain)
    assert first.ok
    baseline = first.start_sha[1]
    before = _page(env, chain[-1].path).read_bytes()
    workspace_head = env.git("rev-parse", "HEAD", cwd=env.layout.root)
    if advance != "equal":
        _commit(env, chain[-1].worktree, "own.txt")
    if advance == "refresh-merge":
        _commit(env, env.repo, "parent.txt")

    result = place.start(env.ctx(), chain)

    assert result.ok and result.start_sha == (baseline, baseline)
    assert result.application is None
    assert _page(env, chain[-1].path).read_bytes() == before
    assert env.git("rev-parse", "HEAD", cwd=env.layout.root) == workspace_head
    assert result.chain[-1].refresh.kind == ("merge" if advance == "refresh-merge" else "none")


@pytest.mark.parametrize("dry_run", [False, True])
def test_start_already_started_on_unrelated_sha(place_env, dry_run):
    env = place_env
    chain = _item(env)
    env.git("checkout", "--orphan", "unrelated")
    env.git("commit", "--allow-empty", "-m", "Unrelated root")
    unrelated = env.git("rev-parse", "HEAD")
    env.git("checkout", "main")
    _edit(env, chain[-1].path, start_sha=unrelated)
    before = _page(env, chain[-1].path).read_bytes()

    result = place.start(env.ctx(dry_run=dry_run), chain)

    assert result.refusal.kind == "already-started"
    assert unrelated in result.refusal.detail
    assert env.git("rev-parse", "main") in result.refusal.detail
    assert str(chain[-1].worktree) in result.refusal.detail
    assert _page(env, chain[-1].path).read_bytes() == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_start_wrong_phase_still_ensures(place_env, dry_run):
    chain = _item(place_env, phase="plan")
    result = place.start(place_env.ctx(dry_run=dry_run), chain)
    assert result.refusal.kind == "wrong-phase" and result.mode == "start"
    assert result.chain[-1].action == "created"
    assert chain[-1].worktree.exists() is not dry_run


@pytest.mark.parametrize("action", ["created", "verified", "adopted", "reattached"])
def test_start_dry_run_writes_nothing(place_env, git_repo, action):
    env = place_env
    chain = _item(env)
    worktree = chain[-1].worktree
    if action != "created":
        assert place.ensure(env.ctx(), chain).ok
    if action == "adopted":
        env.git("worktree", "move", str(worktree), str(env.repo.parent / "legacy"))
    elif action == "reattached":
        env.git("worktree", "remove", str(worktree))
    _workspace(env, git_repo)
    parent = _commit(env, env.repo, "parent.txt")
    before = _page(env, chain[-1].path).read_bytes()
    workspace_head = env.git("rev-parse", "HEAD", cwd=env.layout.root)
    refs = env.git("for-each-ref", "refs/heads")

    result = place.start(env.ctx(dry_run=True), chain)

    assert result.ok and result.mode == "start" and result.start_sha == (None, parent)
    assert result.chain[-1].action == action
    assert result.application is None
    assert _page(env, chain[-1].path).read_bytes() == before
    assert env.git("rev-parse", "HEAD", cwd=env.layout.root) == workspace_head
    assert env.git("for-each-ref", "refs/heads") == refs
    assert worktree.exists() is (action == "verified")


@pytest.mark.parametrize("existing_child", [False, True])
@pytest.mark.parametrize("recorded", [False, True])
def test_start_dry_run_handles_unknown_merge_commit(place_env, existing_child, recorded):
    env = place_env
    owner = env.file("owner", kind="Epic")
    chain = _item(env, parent=owner)
    assert place.ensure(env.ctx(), chain if existing_child else chain[:1]).ok
    baseline = env.git("rev-parse", "main") if recorded else None
    if recorded:
        _edit(env, chain[-1].path, start_sha=baseline)
    _commit(env, chain[0].worktree, "owner.txt")
    _commit(env, env.repo, "parent.txt")
    before = _page(env, chain[-1].path).read_bytes()
    refs = env.git("for-each-ref", "refs/heads")

    result = place.start(env.ctx(dry_run=True), chain)

    assert result.ok and result.start_sha == (baseline, baseline)
    assert result.chain[-1].refresh.to_sha is None
    assert bool(result.warnings) is not recorded
    if not recorded:
        assert "baseline" in result.warnings[0] and "SHA" in result.warnings[0]
    assert result.application is None
    assert _page(env, chain[-1].path).read_bytes() == before
    assert env.git("for-each-ref", "refs/heads") == refs
    assert chain[-1].worktree.exists() is existing_child


def test_start_records_own_merge_head_not_parent(place_env):
    env = place_env
    chain = _item(env)
    assert place.ensure(env.ctx(), chain).ok
    _commit(env, chain[-1].worktree, "own.txt")
    parent = _commit(env, env.repo, "parent.txt")

    result = place.start(env.ctx(), chain)

    head = env.git("rev-parse", "HEAD", cwd=chain[-1].worktree)
    assert result.ok and result.start_sha == (None, head)
    assert head != parent and result.chain[-1].refresh.kind == "merge"


def test_start_propagates_ensure_refusal(place_env):
    chain = _item(place_env)
    chain[-1].worktree.mkdir(parents=True)
    result = place.start(place_env.ctx(), chain)
    assert result.mode == "start" and result.refusal.kind == "foreign-checkout"
    assert result.start_sha is None and result.application is None


def test_start_invalid_baseline_refuses_without_overwriting(place_env):
    chain = _item(place_env)
    _edit(place_env, chain[-1].path, start_sha="malformed")
    result = place.start(place_env.ctx(), chain)
    assert result.refusal.kind == "git-unavailable"
    assert "malformed start_sha" in result.refusal.detail


def test_start_strict_head_failure(place_env, monkeypatch):
    chain = _item(place_env)
    real = provenance.strict_commit
    calls = 0

    def read(cwd, ref, **kwargs):
        nonlocal calls
        if cwd == chain[-1].worktree and ref == "HEAD":
            calls += 1
            return provenance.GitFailure("nonzero", "start HEAD diagnostic")
        return real(cwd, ref, **kwargs)

    monkeypatch.setattr(provenance, "strict_commit", read)
    result = place.start(place_env.ctx(), chain)
    assert calls == 1
    assert result.refusal.kind == "git-unavailable" and "start HEAD diagnostic" in result.refusal.detail


@pytest.mark.parametrize("dry_run", [False, True])
def test_start_strict_ancestry_failure(place_env, monkeypatch, dry_run):
    env = place_env
    chain = _item(env)
    baseline = env.git("rev-parse", "HEAD")
    _edit(env, chain[-1].path, start_sha=baseline)
    head = _commit(env, env.repo, "parent.txt")
    real = provenance.strict_is_ancestor

    def read(cwd, older, newer, **kwargs):
        if older == baseline and newer == head:
            return provenance.GitFailure("nonzero", "baseline ancestry diagnostic")
        return real(cwd, older, newer, **kwargs)

    monkeypatch.setattr(provenance, "strict_is_ancestor", read)
    result = place.start(env.ctx(dry_run=dry_run), chain)
    assert result.refusal.kind == "git-unavailable"
    assert "baseline ancestry diagnostic" in result.refusal.detail
    assert result.application is None


@pytest.mark.parametrize("failure", ["preimage-race", "rollback-only"])
def test_start_failed_mutation_retains_application_and_warnings(place_env, monkeypatch, failure):
    env = place_env
    chain = _item(env)
    real = place.apply_mutation

    def raced(layout, plan, **kwargs):
        if failure == "preimage-race":
            page = _page(env, chain[-1].path)
            page.write_bytes(page.read_bytes() + b"\nConcurrent edit\n")
        application = real(layout, plan, **kwargs)
        if failure == "rollback-only":
            assert application.rolled_back
            # Model the supported rollback outcome without a failure string,
            # after the real transaction has restored the baseline preimage.
            application = replace(application, failures=())
        return application

    if failure == "rollback-only":
        monkeypatch.setattr(transactions, "_validate_postconditions", lambda *a, **kw: ("injected validation failure",))
    monkeypatch.setattr(place, "apply_mutation", raced)
    result = place.start(env.ctx(), chain)
    assert result.application is not None and not result.application.ok
    assert bool(result.application.failures) is (failure == "preimage-race")
    assert not result.ok and result.refusal is None
    assert result.start_sha == (None, None)
    assert result.warnings == (*result.application.failures, *result.application.warnings)
    assert "start_sha" not in parse(_page(env, chain[-1].path).read_text(encoding="utf-8")).fm_data()


def test_start_unknown_after_ensure_refuses(place_env, monkeypatch):
    env = place_env
    chain = _item(env)
    real = place._ensure

    def removed(*args, **kwargs):
        result = real(*args, **kwargs)
        _page(env, chain[-1].path).unlink()
        return result

    monkeypatch.setattr(place, "_ensure", removed)
    result = place.start(env.ctx(), chain)
    assert result.refusal.kind == "unknown-item"
    assert result.application is None


@pytest.mark.parametrize("baseline", ["own", "parent", "unrelated", None])
def test_start_dry_run_own_merge_uses_real_ancestry_not_reported_target(place_env, baseline):
    env = place_env
    chain = _item(env)
    assert place.ensure(env.ctx(), chain).ok
    own = _commit(env, chain[-1].worktree, "own.txt")
    if baseline == "unrelated":
        env.git("checkout", "--orphan", "unrelated")
        env.git("commit", "--allow-empty", "-m", "Unrelated root")
        recorded = env.git("rev-parse", "HEAD")
        env.git("checkout", "main")
    parent = _commit(env, env.repo, "parent.txt")
    if baseline != "unrelated":
        recorded = {"own": own, "parent": parent, None: None}[baseline]
    if recorded is not None:
        _edit(env, chain[-1].path, start_sha=recorded)
    before = _page(env, chain[-1].path).read_bytes()

    result = place.start(env.ctx(dry_run=True), chain)

    assert result.chain[-1].refresh.to_sha == parent
    if baseline == "unrelated":
        assert result.refusal.kind == "already-started"
    else:
        assert result.ok and result.start_sha == (recorded, recorded)
        assert bool(result.warnings) is (baseline is None)
    assert _page(env, chain[-1].path).read_bytes() == before
    assert env.git("rev-parse", "HEAD", cwd=chain[-1].worktree) == own


def test_start_empty_chain_is_noop(place_env):
    result = place.start(place_env.ctx(), ())
    assert result.ok and result.mode == "start" and result.chain == () and result.start_sha is None
