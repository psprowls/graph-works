import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from code_graph_io.records import GraphNode, GraphRecords
from code_graph_io.testing import open_store
from code_wiki_okf.cli import _echo_plan, _echo_result, app
from code_wiki_okf.init import SEED_ONLY, SEED_RELATIVE_PATHS, install_bundle
from code_wiki_okf.mirror.model import DeclinedDeletion, MirrorPlan, MirrorResult
from okf_ext.generators import Render
from okf_ext.moves.model import Move, MovePlan
from typer.testing import CliRunner

runner = CliRunner()

_TODAY = date(2026, 1, 1)
_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

#: `code-wiki-okf`'s own owned, non-human members -- picked from
#: `SEED_RELATIVE_PATHS` rather than named by literal filename, so a test
#: forcing a rewrite (deleting one) doesn't rot silently if that list's
#: membership ever changes.
_NON_SEED_ONLY_MEMBERS = [member for member in SEED_RELATIVE_PATHS if member not in SEED_ONLY]


def test_init_command_writes_bundle(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 0
    assert (root / "index.md").exists()
    # Refusals go to stderr, never stdout -- a clean run should carry none of
    # them, and the `wrote ...` lines belong on stdout so the command remains
    # pipeable.
    assert "wrote index.md" in result.stdout
    assert "refused" not in result.stderr


def test_init_command_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = runner.invoke(app, ["init", str(root), "--dry-run"])
    assert result.exit_code == 0
    assert not root.exists()
    assert "index.md" in result.output


def test_init_command_installs_into_a_non_empty_directory(tmp_path: Path) -> None:
    """The narrowed contract at the CLI: "not empty" is no longer a refusal."""
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "existing.txt").write_text("hi", encoding="utf-8")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 0
    assert (root / "index.md").is_file()
    assert (root / "existing.txt").read_text(encoding="utf-8") == "hi"


def test_init_command_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    assert runner.invoke(app, ["init", str(root)]).exit_code == 0
    second = runner.invoke(app, ["init", str(root)])
    assert second.exit_code == 0
    assert "wrote " not in second.output
    assert "skipped index.md" in second.output


def test_init_command_refuses_a_hand_edited_seed_by_name(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "_schema").mkdir(parents=True)
    (root / "_schema/File.schema.json").write_text('{"mine": true}\n', encoding="utf-8")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 1
    assert "_schema/File.schema.json" in result.output
    assert "foreign-content" in result.output
    # The refusal is a stderr-only concern: it must never leak onto stdout,
    # where a caller piping `wrote ...` lines elsewhere would see it.
    assert "refused" in result.stderr
    assert "refused" not in result.stdout


def test_init_command_rejects_a_config_dir_that_is_not_a_directory(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["init", str(tmp_path / "bundle"), "--config-dir", str(not_a_dir)])
    assert result.exit_code == 1
    assert "not a directory" in result.output


def test_sync_command_rejects_a_config_dir_that_is_not_a_directory(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    assert runner.invoke(app, ["init", str(root)]).exit_code == 0
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["sync", str(root), "--config-dir", str(not_a_dir)])
    assert result.exit_code == 1
    assert "not a directory" in result.output


def test_validate_command_rejects_a_config_dir_that_is_not_a_directory(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    assert runner.invoke(app, ["init", str(root)]).exit_code == 0
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["validate", str(root), "--config-dir", str(not_a_dir)])
    assert result.exit_code == 1
    assert "not a directory" in result.output


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(_IS_ROOT, reason="root bypasses permission bits")
def test_init_command_reports_a_log_append_failure_and_exits_1(tmp_path: Path) -> None:
    """A `log.md` that scaffolds fine but rejects the append itself (here: a
    read-only file, an `OSError`) is `log_failure`, not `scaffold.failed` or
    `install.failed` -- it must still be reported on stderr and still fail the
    run, or the command exits 1 with nothing explaining why.
    """
    root = tmp_path / "bundle"
    assert runner.invoke(app, ["init", str(root)]).exit_code == 0
    log_path = root / "log.md"
    assert log_path.is_file()
    # Force the install half to write again, so `install_bundle` attempts the
    # append -- a re-run with nothing to install never touches `log.md`. Which
    # member is irrelevant, so it's picked programmatically rather than named
    # by literal filename -- see `_NON_SEED_ONLY_MEMBERS`.
    (root / _NON_SEED_ONLY_MEMBERS[0]).unlink()
    log_path.chmod(0o444)
    try:
        result = runner.invoke(app, ["init", str(root)])
    finally:
        log_path.chmod(0o644)
    assert result.exit_code == 1
    assert "log.md" in result.output
    assert "commit-error" in result.output


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(_IS_ROOT, reason="root bypasses permission bits")
def test_init_command_reports_both_a_file_refusal_and_a_log_failure(tmp_path: Path) -> None:
    """`BundleInstall` keeps `install.failed` and `log_failure` as two
    separate channels -- `init` renders them in two independent blocks, and
    nothing here forces the two to be tested together. A later "simplify
    these two loops into one" change could drop the `log_failure` line
    without any single-channel test noticing. This run produces both at once
    and checks both lines land.
    """
    root = tmp_path / "bundle"
    assert runner.invoke(app, ["init", str(root)]).exit_code == 0
    log_path = root / "log.md"
    assert log_path.is_file()

    # One member deleted, to force the install half to write again (so the
    # log append is attempted at all); a different member hand-edited, to
    # produce an independent `install.failed` foreign-content refusal in the
    # same run.
    rewritten, foreign = _NON_SEED_ONLY_MEMBERS[0], _NON_SEED_ONLY_MEMBERS[1]
    (root / rewritten).unlink()
    (root / foreign).write_text("not what this package wrote\n", encoding="utf-8")
    log_path.chmod(0o444)
    try:
        result = runner.invoke(app, ["init", str(root)])
    finally:
        log_path.chmod(0o644)

    assert result.exit_code == 1
    assert f"refused {foreign}" in result.stderr
    assert "foreign-content" in result.stderr
    assert "refused log.md" in result.stderr
    assert "commit-error" in result.stderr


def test_main_module_runs_init(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = subprocess.run(
        [sys.executable, "-m", "code_wiki_okf", "init", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (root / "index.md").exists()


# --- sync command: entity-lane coverage -----------------------------------
#
# `sync` runs the entity half (`entities.lanes.sync`, one call covering every
# configured repo) and the mirror half (one `plan_mirror`/`apply_mirror` pass
# per repo) in the same invocation. `--dry-run` defaults off, matching
# `init`'s own convention -- entity-lanes' original `--dry-run/--no-dry-run`
# (default on) was the outlier and lost the reconciliation.


def _repo_node(org: str, repo: str) -> GraphNode:
    return GraphNode(
        kind="repository",
        name=repo,
        path="",
        line=None,
        attrs={"uri": f"repo:{org}/{repo}", "owner": org, "name": repo, "url": "", "default_branch": "main"},
    )


def _package_node(org: str, repo: str, name: str) -> GraphNode:
    return GraphNode(
        kind="package",
        name=name,
        path=f"packages/{name}/pyproject.toml",
        line=None,
        attrs={"uri": f"pkg:{org}/{repo}/{name}", "language": "python", "version": "0.1.0"},
    )


def _seed_graph(graph_dir: Path, org: str, repo: str, packages: list[str]) -> None:
    """Write graph_dir/code.db with the given packages for one repo."""
    store = open_store(graph_dir / "code.db", create=True)
    try:
        store.set_current_repo(f"repo:{org}/{repo}")
        nodes: list[GraphNode] = [_repo_node(org, repo)]
        nodes += [_package_node(org, repo, name) for name in packages]
        with store.transaction() as tx:
            tx.upsert_records(GraphRecords(nodes=tuple(nodes), edges=()))
        store.set_current_repo(None)
    finally:
        store.close()


def _seed_graph_multi(graph_dir: Path, repos: list[tuple[str, str, list[str]]]) -> None:
    """Write graph_dir/code.db with multiple repos. Each repo tuple is (org, repo, packages)."""
    store = open_store(graph_dir / "code.db", create=True)
    try:
        for org, repo, packages in repos:
            store.set_current_repo(f"repo:{org}/{repo}")
            nodes: list[GraphNode] = [_repo_node(org, repo)]
            nodes += [_package_node(org, repo, name) for name in packages]
            with store.transaction() as tx:
                tx.upsert_records(GraphRecords(nodes=tuple(nodes), edges=()))
            store.set_current_repo(None)
    finally:
        store.close()


def _write_config(bundle_root: Path, graph_dir: Path, repo_names: list[str] | str) -> None:
    """Write a minimal _repositories.yaml naming repos with no real checkout
    path -- fine for entity-only coverage: the mirror half's `head_commit`
    returns `None` for a non-git path and skips that repo, never raising.
    """
    config_path = bundle_root / "_repositories.yaml"
    if isinstance(repo_names, str):
        repo_names = [repo_names]
    repos_yaml = "\n".join(f"  {name}:\n    path: {bundle_root.parent / name}" for name in repo_names)
    config_text = f"""graph_dir: {graph_dir}
repositories:
{repos_yaml}
"""
    config_path.write_text(config_text, encoding="utf-8")


def test_sync_command_dry_run_touches_nothing(tmp_path: Path) -> None:
    """--dry-run should touch nothing."""
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    _seed_graph(graph_dir, "acme", "repo-a", ["widgets"])

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    _write_config(bundle_root, graph_dir, "repo-a")

    before_index = (bundle_root / "index.md").read_text(encoding="utf-8")
    before_members = sorted(p.relative_to(bundle_root).as_posix() for p in bundle_root.rglob("*.md"))

    result = runner.invoke(app, ["sync", str(bundle_root), "--dry-run"])

    assert result.exit_code == 0
    assert "dry run" in result.output.lower()
    assert not (bundle_root / "packages" / "widgets.md").exists()
    assert (bundle_root / "index.md").read_text(encoding="utf-8") == before_index
    after_members = sorted(p.relative_to(bundle_root).as_posix() for p in bundle_root.rglob("*.md"))
    assert after_members == before_members


def test_sync_command_default_applies_entity_changes(tmp_path: Path) -> None:
    """No `--dry-run` (the default) applies the entity sync and reports counts."""
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    _seed_graph(graph_dir, "acme", "repo-a", ["widgets"])

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    _write_config(bundle_root, graph_dir, "repo-a")

    result = runner.invoke(app, ["sync", str(bundle_root)])

    assert result.exit_code == 0, result.output
    assert (bundle_root / "packages" / "widgets.md").exists()
    assert "entities: written" in result.output


def test_sync_command_config_error_exits_1(tmp_path: Path) -> None:
    """A malformed _repositories.yaml should print to stderr and exit 1."""
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)

    config_path = bundle_root / "_repositories.yaml"
    config_path.write_text("graph_dir: [this is invalid]\n", encoding="utf-8")

    result = runner.invoke(app, ["sync", str(bundle_root)])

    assert result.exit_code == 1
    assert "_repositories.yaml" in result.output or "not a" in result.output.lower()


def test_sync_command_collision_error_exits_1(tmp_path: Path) -> None:
    """A shared package name across repos should raise ValueError and exit 1."""
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    _seed_graph_multi(graph_dir, [("acme", "repo-a", ["shared"]), ("acme", "repo-b", ["shared"])])

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    _write_config(bundle_root, graph_dir, ["repo-a", "repo-b"])

    result = runner.invoke(app, ["sync", str(bundle_root)])

    assert result.exit_code == 1
    assert "shared" in result.output.lower()


# --- sync command: mirror-lane coverage ------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _scratch_workspace(tmp_path: Path) -> Path:
    """A minimal bundle + one graphed repo, config already pointing at both."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "a.py").write_text("VALUE = 1\n")
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@t")
    _git(repo_root, "config", "user.name", "t")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")

    from code_graph_io.update import run_workspace

    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)

    bundle_root = tmp_path / "bundle"
    result = runner.invoke(app, ["init", str(bundle_root)])
    assert result.exit_code == 0
    (bundle_root / "_repositories.yaml").write_text(
        f"graph_dir: {graph_dir}\nrepositories:\n  acme:\n    path: {repo_root}\n", encoding="utf-8"
    )
    return bundle_root


def test_sync_command_creates_file_pages(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)
    result = runner.invoke(app, ["sync", str(bundle_root)])
    assert result.exit_code == 0, result.output
    assert (bundle_root / "repositories" / "acme" / "a.py.md").exists()


def test_sync_command_dry_run_writes_nothing(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)
    result = runner.invoke(app, ["sync", str(bundle_root), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert not (bundle_root / "repositories").exists()
    assert "a.py" in result.output


def test_sync_command_reports_malformed_config(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)
    (bundle_root / "_repositories.yaml").write_text("not: [valid, - yaml structure\n", encoding="utf-8")
    result = runner.invoke(app, ["sync", str(bundle_root)])
    assert result.exit_code == 1


def test_sync_command_second_run_reports_no_changes(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)
    first = runner.invoke(app, ["sync", str(bundle_root)])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["sync", str(bundle_root)])
    assert second.exit_code == 0, second.output
    assert "created 0, updated 0, moved 0, deleted 0" in second.output


def _init_git_repo(repo_root: Path, filename: str, content: str) -> None:
    repo_root.mkdir()
    (repo_root / filename).write_text(content)
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@t")
    _git(repo_root, "config", "user.name", "t")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")


def test_sync_command_syncs_every_configured_repo(tmp_path: Path) -> None:
    """Two repos, one shared graph, one `sync` call -- the primary real-world
    shape (`_repositories.yaml` typically names several repos), which none of
    the single-repo scratch-workspace tests above exercise.
    """
    repo_a = tmp_path / "repo_a"
    _init_git_repo(repo_a, "a.py", "VALUE = 1\n")
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_b, "b.py", "VALUE = 2\n")

    from code_graph_io.update import run_workspace

    graph_dir = tmp_path / "graph"
    run_workspace([repo_a, repo_b], graph_dir=graph_dir, full=True)

    bundle_root = tmp_path / "bundle"
    result = runner.invoke(app, ["init", str(bundle_root)])
    assert result.exit_code == 0
    (bundle_root / "_repositories.yaml").write_text(
        f"graph_dir: {graph_dir}\nrepositories:\n  acme:\n    path: {repo_a}\n  beta:\n    path: {repo_b}\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["sync", str(bundle_root)])
    assert result.exit_code == 0, result.output
    assert (bundle_root / "repositories" / "acme" / "a.py.md").exists()
    assert (bundle_root / "repositories" / "beta" / "b.py.md").exists()
    assert "acme: created 1" in result.output
    assert "beta: created 1" in result.output


def test_sync_command_reports_failure_and_still_processes_other_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`apply_mirror` raising mid-loop (a filesystem error, say) must not
    crash the whole run with a raw traceback -- it should be reported per
    repo, with the command still exiting non-zero.
    """
    bundle_root = _scratch_workspace(tmp_path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("code_wiki_okf.cli.apply_mirror", _boom)
    result = runner.invoke(app, ["sync", str(bundle_root)])
    assert result.exit_code == 1
    assert "acme: sync failed: disk full" in result.output


def test_sync_command_reports_missing_sections_cleanly(tmp_path: Path) -> None:
    """`load_sections` propagates a bare `OSError` (`FileNotFoundError`) for a
    missing `_sections` directory -- the mirror half's `load_sections` call
    reads `config.declarations_dir` now, not this package's own bundled
    assets, so a bundle whose `_sections/` is missing or not yet populated
    (e.g. mid-`--config-dir` relocation) is a reachable state. `sync` must
    catch that and exit 1 with a clean message, not dump a raw traceback."""
    bundle_root = _scratch_workspace(tmp_path)
    shutil.rmtree(bundle_root / "_sections")

    result = runner.invoke(app, ["sync", str(bundle_root)])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "_sections" in result.output


def test_sync_command_dry_run_reports_missing_sections_cleanly(tmp_path: Path) -> None:
    """`--dry-run` short-circuits the entity half (`lanes.sync` returns before
    ever loading `_sections`) but still runs the mirror half's own preview,
    which has its own `load_sections(config.declarations_dir / "_sections")`
    call and its own guard -- this is the one path that actually exercises
    it, distinct from the entity half's guard the non-dry-run test above
    exercises."""
    bundle_root = _scratch_workspace(tmp_path)
    shutil.rmtree(bundle_root / "_sections")

    result = runner.invoke(app, ["sync", str(bundle_root), "--dry-run"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "_sections" in result.output


def _empty_move_plan(bundle_root: Path) -> MovePlan:
    return MovePlan(root=bundle_root, moves=(), edits=(), refusals=(), unrebased=(), digests={})


def test_echo_plan_reports_no_changes_for_an_empty_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = MirrorPlan(
        repo="acme",
        moves=_empty_move_plan(tmp_path),
        creates={},
        updates={},
        deletions=(),
        declined_deletions=(),
    )
    _echo_plan("acme", plan)
    assert "acme: no mirror changes" in capsys.readouterr().out


def test_echo_plan_reports_every_kind_of_change(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    move_plan = MovePlan(
        root=tmp_path,
        moves=(Move(source="repositories/acme/old.py.md", dest="repositories/acme/new.py.md", is_asset=False),),
        edits=(),
        refusals=(),
        unrebased=(),
        digests={},
    )
    plan = MirrorPlan(
        repo="acme",
        moves=move_plan,
        creates={"a.py": ({}, Render())},
        updates={"repositories/acme/b.py": Render()},
        deletions=("c.py",),
        declined_deletions=(
            DeclinedDeletion(resource="file:acme/d.py", path="repositories/acme/d.py.md", reason="prose-edited"),
        ),
    )
    _echo_plan("acme", plan)
    output = capsys.readouterr().out
    assert "acme: would create a.py" in output
    assert "acme: would update repositories/acme/b.py" in output
    assert "acme: would move repositories/acme/old.py.md -> repositories/acme/new.py.md" in output
    assert "acme: would delete c.py" in output
    assert "acme: would decline deletion of repositories/acme/d.py.md (prose-edited)" in output


def test_validate_reports_missing_page_and_exits_nonzero(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)  # a bundle + one graphed repo, config already written -- untouched

    before = {p: p.read_bytes() for p in sorted(bundle_root.rglob("*")) if p.is_file()}
    result = runner.invoke(app, ["validate", str(bundle_root)])
    after = {p: p.read_bytes() for p in sorted(bundle_root.rglob("*")) if p.is_file()}

    assert result.exit_code == 1
    assert "sync.missing-page" in result.output
    assert "warn" in result.output.lower()
    assert before == after  # nothing written


def test_validate_strict_promotes_to_error(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)

    result = runner.invoke(app, ["validate", str(bundle_root), "--strict"])

    assert result.exit_code == 1
    assert "sync.missing-page" in result.output
    assert "error" in result.output.lower()


def test_validate_synced_bundle_exits_zero(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)
    sync_result = runner.invoke(app, ["sync", str(bundle_root)])
    assert sync_result.exit_code == 0, sync_result.output

    result = runner.invoke(app, ["validate", str(bundle_root)])

    assert result.exit_code == 0, result.output
    assert "sync." not in result.output


def test_validate_command_collision_error_exits_1(tmp_path: Path) -> None:
    """`validate` shares `snapshot_bundle` -> `plan_entities` ->
    `_resolve_placements` with `sync`, so the same entity-name collision it
    raises `ValueError` for must be caught here too, not left to crash a
    read-only diagnostic command with a raw traceback."""
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    _seed_graph_multi(graph_dir, [("acme", "repo-a", ["shared"]), ("acme", "repo-b", ["shared"])])

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    _write_config(bundle_root, graph_dir, ["repo-a", "repo-b"])

    result = runner.invoke(app, ["validate", str(bundle_root)])

    assert result.exit_code == 1
    assert "shared" in result.output.lower()
    assert "Traceback" not in result.output


def test_validate_reports_malformed_config(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)
    (bundle_root / "_repositories.yaml").write_text("not: [valid, - yaml structure\n", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(bundle_root)])

    assert result.exit_code == 1


def test_validate_reports_missing_tags_vocabulary_cleanly(tmp_path: Path) -> None:
    """`load_vocabulary` propagates a bare `OSError` (`FileNotFoundError`) for
    a missing `_tags.yaml` -- same family `load_schemas`/`load_sections`
    propagate for a missing `_schema`/`_sections` directory. `validate` must
    catch that and exit 1 with a clean message, not dump a raw traceback."""
    bundle_root = _scratch_workspace(tmp_path)
    (bundle_root / "_tags.yaml").unlink()

    result = runner.invoke(app, ["validate", str(bundle_root)])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "_tags.yaml" in result.output


def _init_empty_graph(bundle_root: Path) -> None:
    """A freshly `init`ed bundle's default `_repositories.yaml` points at
    `../graphs/code` (no repositories configured) -- `open_reader` still
    requires that db to exist, so tests that invoke `validate` against a
    bare `init` output need it seeded, even with zero nodes.
    """
    graph_dir = bundle_root.parent / "graphs" / "code"
    graph_dir.mkdir(parents=True)
    open_store(graph_dir / "code.db", create=True).close()


def test_config_dir_relocates_the_declarations_and_validate_follows(tmp_path: Path) -> None:
    """The persistence half of S-F: the flag is given once at `init` and every
    later command reads the answer out of `_repositories.yaml`."""
    bundle_root = tmp_path / "bundle"
    declarations = tmp_path / "declarations"
    declarations.mkdir()
    init_result = runner.invoke(app, ["init", str(bundle_root), "--config-dir", str(declarations)])
    assert init_result.exit_code == 0, init_result.output
    assert (declarations / "_schema" / "File.schema.json").is_file()
    assert not (bundle_root / "_schema").exists()

    _init_empty_graph(bundle_root)
    validate_result = runner.invoke(app, ["validate", str(bundle_root)])
    assert validate_result.exit_code == 0, validate_result.output


def test_validate_reports_schema_violation(tmp_path: Path) -> None:
    """Isolated from `sync.*` and from `placement.*`: the fixture page sits in
    `packages/` -- its type's declared lane, so `placement.directory-mismatch`
    cannot fire -- and carries no `resource:`, so `resource_index` skips it,
    `_existing_entity_resources` never sees it, and it cannot be spuriously
    flagged `sync.orphan-page`. `schema_rule`/`section_rule` dispatch purely
    on frontmatter `type`, never on directory, so the move doesn't change
    what fires.

    Every okf-ext rule defaults to `severity="warn"` (house rules can't make
    a conformant-looking bundle fail `Report.ok`), so a plain `validate` run
    exits 0 while still printing the finding; `--strict` promotes it to
    error and is the pass that must actually hit zero.
    """
    init_result = runner.invoke(app, ["init", str(tmp_path / "bundle")])
    assert init_result.exit_code == 0
    bundle_root = tmp_path / "bundle"
    _init_empty_graph(bundle_root)
    (bundle_root / "packages").mkdir()
    (bundle_root / "packages" / "bad.md").write_text(
        '---\ntype: Package\ntitle: "bad"\nversion: 123\n---\n\n'
        "## Purpose\n\nSome real purpose text goes here, filled in properly for this test.\n\n"
        "## Public API\n\n> TODO: <Main exports and when to use them. "
        "Link code with backticked `path:line` references.>\n\n"
        "## Files\n\n_(populated by `code-wiki-okf sync` — not yet generated)_\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(bundle_root)])
    assert result.exit_code == 0, result.output
    assert "schemas.invalid" in result.output

    strict_result = runner.invoke(app, ["validate", str(bundle_root), "--strict"])
    assert strict_result.exit_code == 1
    assert "schemas.invalid" in strict_result.output


def test_validate_reports_unfilled_required_section(tmp_path: Path) -> None:
    """Same isolation as `test_validate_reports_schema_violation`: the page
    sits in its declared lane and carries no `resource:`, so neither
    `sync.*` nor `placement.*` fires, and the same two-tier (plain exits 0,
    `--strict` exits 1) behavior applies.
    """
    result_init = runner.invoke(app, ["init", str(tmp_path / "bundle")])
    assert result_init.exit_code == 0
    bundle_root = tmp_path / "bundle"
    _init_empty_graph(bundle_root)
    (bundle_root / "packages").mkdir()
    (bundle_root / "packages" / "stub.md").write_text(
        '---\ntype: Package\ntitle: "stub"\nversion: "0.1.0"\n---\n\n'
        "## Purpose\n\n> TODO: <One paragraph: what this package does, who uses it, why it exists.>\n\n"
        "## Public API\n\n> TODO: <Main exports and when to use them. "
        "Link code with backticked `path:line` references.>\n\n"
        "## Files\n\n_(populated by `code-wiki-okf sync` — not yet generated)_\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(bundle_root)])
    assert result.exit_code == 0, result.output
    assert "sections.unfilled" in result.output

    strict_result = runner.invoke(app, ["validate", str(bundle_root), "--strict"])
    assert strict_result.exit_code == 1
    assert "sections.unfilled" in strict_result.output


def test_validate_reports_undeclared_tag(tmp_path: Path) -> None:
    """Same isolation and two-tier shape, for `vocabulary_rule`: an
    undeclared tag is `tags.unknown`, reported but not failing a plain
    `validate` run, and failing under `--strict`. This criterion had zero
    committed coverage before this test.
    """
    init_result = runner.invoke(app, ["init", str(tmp_path / "bundle")])
    assert init_result.exit_code == 0
    bundle_root = tmp_path / "bundle"
    _init_empty_graph(bundle_root)
    (bundle_root / "packages").mkdir()
    (bundle_root / "packages" / "tagged.md").write_text(
        '---\ntype: Package\ntitle: "tagged"\nversion: "0.1.0"\n'
        'tags: ["nonexistent-tag"]\n---\n\n'
        "## Purpose\n\nSome real purpose text goes here, filled in properly for this test.\n\n"
        "## Public API\n\nSome real API docs go here too.\n\n"
        "## Files\n\n_(populated by `code-wiki-okf sync` — not yet generated)_\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(bundle_root)])
    assert result.exit_code == 0, result.output
    assert "tags.unknown" in result.output

    strict_result = runner.invoke(app, ["validate", str(bundle_root), "--strict"])
    assert strict_result.exit_code == 1
    assert "tags.unknown" in strict_result.output


def test_validate_reports_a_misplaced_page_as_an_error(tmp_path: Path) -> None:
    """Unlike every other house rule wired into this command, `placement.*` is
    passed at `error`: a page outside its declared lane is a state the
    reconciler cannot leave, not advisory drift. (The tier-2 factory itself
    defaults to `warn`; the argument for raising it belongs to this package --
    design spec §5.2.) So a plain `validate` run already exits 1, and `cli.py`
    needs no `has_placement_finding` counterpart to its `has_sync_finding`
    special case.
    """
    init_result = runner.invoke(app, ["init", str(tmp_path / "bundle")])
    assert init_result.exit_code == 0
    bundle_root = tmp_path / "bundle"
    _init_empty_graph(bundle_root)
    (bundle_root / "dependencies").mkdir()
    (bundle_root / "dependencies" / "widgets.md").write_text(
        '---\ntype: Package\ntitle: "widgets"\n---\n\n'
        "## Purpose\n\nSome real purpose text goes here, filled in properly for this test.\n\n"
        "## Public API\n\nSome real API docs go here too.\n\n"
        "## Files\n\n_(none)_\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(bundle_root)])

    assert result.exit_code == 1
    assert "placement.directory-mismatch" in result.output
    assert "error" in result.output.lower()


def test_validate_still_reports_sync_findings_alongside_new_rules(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)  # a bundle + one graphed repo, never synced

    result = runner.invoke(app, ["validate", str(bundle_root)])

    assert result.exit_code == 1
    assert "sync.missing-page" in result.output


def test_echo_result_reports_every_kind_of_change(capsys: pytest.CaptureFixture[str]) -> None:
    result = MirrorResult(
        repo="acme",
        moved=(("repositories/acme/old.py.md", "repositories/acme/new.py.md"),),
        created=("a.py",),
        regenerated=("repositories/acme/b.py",),
        deleted=("c.py",),
        declined_deletions=(
            DeclinedDeletion(resource="file:acme/d.py", path="repositories/acme/d.py.md", reason="prose-edited"),
        ),
        index_updates=(),
    )
    _echo_result("acme", result)
    output = capsys.readouterr().out
    assert "acme: created 1, updated 1, moved 1, deleted 1" in output
    assert "acme: created a.py" in output
    assert "acme: updated repositories/acme/b.py" in output
    assert "acme: moved repositories/acme/old.py.md -> repositories/acme/new.py.md" in output
    assert "acme: deleted c.py" in output
    assert "acme: declined deletion of repositories/acme/d.py.md (prose-edited)" in output
