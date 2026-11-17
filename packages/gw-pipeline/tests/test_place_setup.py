"""Placement prepares a fresh worktree: submodules always, configured setup commands in order."""

from gw_pipeline import place


def _in_git_dir(name):
    return f'touch "$(git rev-parse --absolute-git-dir)/{name}"'


def _git_dir_file(env, chain, name):
    from pathlib import Path

    return Path(env.git("rev-parse", "--absolute-git-dir", cwd=chain[0].worktree)) / name


def _setup(*commands):
    return lambda repo_name: tuple(commands)


def test_setup_runs_in_order_in_the_new_worktree(place_env):
    chain = place_env.chain_for(place_env.file())
    ctx = place_env.ctx(setup=_setup("pwd > first.txt", "echo two > second.txt"))
    result = place.ensure(ctx, chain)
    assert result.ok and result.chain[0].action == "created"
    worktree = chain[0].worktree
    assert (worktree / "first.txt").read_text().strip() == str(worktree.resolve())
    assert (worktree / "second.txt").exists()


def test_failure_refuses_setup_failed_and_keeps_the_worktree(place_env):
    chain = place_env.chain_for(place_env.file())
    ctx = place_env.ctx(setup=_setup("echo boom >&2; exit 3", "touch never.txt"))
    result = place.ensure(ctx, chain)
    assert result.refusal is not None and result.refusal.kind == "setup-failed"
    assert "exit 3" in result.refusal.detail and "boom" in result.refusal.detail
    assert chain[0].worktree.is_dir() and not (chain[0].worktree / "never.txt").exists()


def test_rerun_after_failure_repeats_setup_and_clears_marker(place_env):
    chain = place_env.chain_for(place_env.file())
    assert place.ensure(place_env.ctx(setup=_setup("exit 1")), chain).refusal.kind == "setup-failed"
    ran = _in_git_dir("ran")
    result = place.ensure(place_env.ctx(setup=_setup(ran)), chain)
    assert result.ok and _git_dir_file(place_env, chain, "ran").exists()
    _git_dir_file(place_env, chain, "ran").unlink()
    again = place.ensure(place_env.ctx(setup=_setup(ran)), chain)
    assert again.ok and not _git_dir_file(place_env, chain, "ran").exists()


def test_verified_worktree_never_runs_setup(place_env):
    chain = place_env.chain_for(place_env.file())
    assert place.ensure(place_env.ctx(), chain).ok
    result = place.ensure(place_env.ctx(setup=_setup("touch ran.txt")), chain)
    assert result.ok and not (chain[0].worktree / "ran.txt").exists()


def test_verify_only_never_runs_setup_even_after_failure(place_env):
    chain = place_env.chain_for(place_env.file())
    assert place.ensure(place_env.ctx(setup=_setup("exit 1")), chain).refusal.kind == "setup-failed"
    result = place.ensure(place_env.ctx(setup=_setup("touch ran.txt")), chain, refresh=False)
    assert result.ok and not (chain[0].worktree / "ran.txt").exists()


def test_dry_run_reports_setup_without_executing(place_env):
    chain = place_env.chain_for(place_env.file())
    result = place.ensure(place_env.ctx(dry_run=True, setup=_setup("touch ran.txt")), chain)
    assert result.ok and "touch ran.txt" in (result.chain[0].detail or "")
    assert not chain[0].worktree.exists()


def test_gitmodules_triggers_submodule_update_first(place_env):
    (place_env.repo / ".gitmodules").write_text("")
    place_env.git("add", ".gitmodules")
    place_env.git("commit", "-m", "Declare submodules")
    chain = place_env.chain_for(place_env.file())
    calls = []
    real = place._run_setup_command

    def spy(worktree, command):
        calls.append(command)
        return real(worktree, command)

    import pytest

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(place, "_run_setup_command", spy)
        result = place.ensure(place_env.ctx(setup=_setup("true")), chain)
    assert result.ok
    assert calls == ["git submodule update --init --recursive", "true"]
