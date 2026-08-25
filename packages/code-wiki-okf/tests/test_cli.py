import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from code_graph_io.records import GraphNode, GraphRecords
from code_graph_io.testing import open_store
from code_wiki_okf.cli import _echo_plan, _echo_result, app
from code_wiki_okf.init import SEED_RELATIVE_PATHS, install_bundle
from code_wiki_okf.mirror.model import DeclinedDeletion, MirrorPlan, MirrorResult
from code_wiki_okf.sync import SyncResult
from code_wiki_okf.sync import sync_bundle as run_sync_bundle
from okf_ext.generators import Render
from okf_ext.moves.model import Move, MovePlan, Stranded
from typer.testing import CliRunner

runner = CliRunner()

_TODAY = date(2026, 1, 1)
_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


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
    (root / "schema").mkdir(parents=True)
    (root / "schema/File.schema.json").write_text('{"mine": true}\n', encoding="utf-8")
    result = runner.invoke(app, ["init", str(root)])
    assert result.exit_code == 1
    assert "schema/File.schema.json" in result.output
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
    (root / "workspace.yaml").write_text("", encoding="utf-8")
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["sync", str(root), "--config-dir", str(not_a_dir)])
    assert result.exit_code == 1
    assert "not a directory" in result.output


def test_validate_command_rejects_a_config_dir_that_is_not_a_directory(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    assert runner.invoke(app, ["init", str(root)]).exit_code == 0
    (root / "workspace.yaml").write_text("", encoding="utf-8")
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
    # by literal filename -- see `SEED_RELATIVE_PATHS`.
    (root / SEED_RELATIVE_PATHS[0]).unlink()
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
    rewritten, foreign = SEED_RELATIVE_PATHS[0], SEED_RELATIVE_PATHS[1]
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
# `sync` plans every entity and File target together, then applies that one
# immutable composite plan. `--dry-run` defaults off, matching `init`.


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


def _write_config(bundle_root: Path, repo_names: list[str] | str) -> None:
    """Write a minimal `workspace.yaml` naming repos with no real checkout
    path -- fine for entity-only coverage: the mirror half's `head_commit`
    returns `None` for a non-git path and skips that repo, never raising.

    No `graph_dir` key: the standalone CLI always resolves `graph_dir` to
    `bundle_root` itself now (`cli.py` passes `graph_dir=bundle_root` to
    `load_config`), so the graph db lives in `bundle_root` directly and this
    file carries only the `repositories` block.
    """
    config_path = bundle_root / "workspace.yaml"
    if isinstance(repo_names, str):
        repo_names = [repo_names]
    repos_yaml = "\n".join(f"  {name}:\n    path: {bundle_root.parent / name}" for name in repo_names)
    config_path.write_text(f"repositories:\n{repos_yaml}\n", encoding="utf-8")


def test_sync_command_dry_run_touches_nothing(tmp_path: Path) -> None:
    """--dry-run should touch nothing."""
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    _seed_graph(bundle_root, "acme", "repo-a", ["widgets"])
    _write_config(bundle_root, "repo-a")

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
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    _seed_graph(bundle_root, "acme", "repo-a", ["widgets"])
    _write_config(bundle_root, "repo-a")

    result = runner.invoke(app, ["sync", str(bundle_root)])

    assert result.exit_code == 0, result.output
    assert (bundle_root / "repositories" / "repo-a" / "packages" / "widgets.md").exists()
    assert "entities: written" in result.output


def test_sync_command_config_error_exits_1(tmp_path: Path) -> None:
    """A malformed `workspace.yaml` should print to stderr and exit 1."""
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)

    config_path = bundle_root / "workspace.yaml"
    config_path.write_text("repositories: [this is invalid]\n", encoding="utf-8")

    result = runner.invoke(app, ["sync", str(bundle_root)])

    assert result.exit_code == 1
    assert "workspace.yaml" in result.output or "not a" in result.output.lower()


def test_sync_command_two_repos_same_package_name_no_longer_collides(tmp_path: Path) -> None:
    """Entity pages nest under `repositories/<repo>/<lane>/`, so two repos'
    same-named packages resolve to two distinct paths and no longer raise --
    the CLI-level counterpart to
    `entities/test_sync.py::test_sync_two_repos_same_package_name_no_longer_collides`.
    """
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    _seed_graph_multi(bundle_root, [("acme", "repo-a", ["shared"]), ("acme", "repo-b", ["shared"])])
    _write_config(bundle_root, ["repo-a", "repo-b"])

    result = runner.invoke(app, ["sync", str(bundle_root)])

    assert result.exit_code == 0, result.output
    assert (bundle_root / "repositories" / "repo-a" / "packages" / "shared.md").exists()
    assert (bundle_root / "repositories" / "repo-b" / "packages" / "shared.md").exists()


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

    bundle_root = tmp_path / "bundle"
    result = runner.invoke(app, ["init", str(bundle_root)])
    assert result.exit_code == 0
    # The standalone CLI always resolves `graph_dir` to `bundle_root` itself,
    # so the graph db is written there directly rather than to a separate
    # directory named in the config.
    run_workspace([repo_root], graph_dir=bundle_root, full=True)
    (bundle_root / "workspace.yaml").write_text(f"repositories:\n  repo:\n    path: {repo_root}\n", encoding="utf-8")
    return bundle_root


def test_sync_command_creates_file_pages(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)
    result = runner.invoke(app, ["sync", str(bundle_root)])
    assert result.exit_code == 0, result.output
    assert (bundle_root / "repositories" / "repo" / "files" / "a.py.md").exists()


def test_sync_command_dry_run_writes_nothing(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)
    result = runner.invoke(app, ["sync", str(bundle_root), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert not (bundle_root / "repositories").exists()
    assert "entities: would create repositories/repo/repository" in result.stdout
    assert "catalog: would create repositories/repo/packages/index.md" in result.stdout
    assert "repo: would create a.py" in result.stdout


def test_sync_command_idempotent_dry_run_reports_no_entity_or_catalog_changes(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)
    applied = runner.invoke(app, ["sync", str(bundle_root)])
    assert applied.exit_code == 0, applied.output

    result = runner.invoke(app, ["sync", str(bundle_root), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "entities: no changes" in result.stdout
    assert "catalog: no changes" in result.stdout
    assert "entities: would" not in result.stdout
    assert "catalog: would" not in result.stdout


def test_sync_command_reports_malformed_config(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)
    (bundle_root / "workspace.yaml").write_text("not: [valid, - yaml structure\n", encoding="utf-8")
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
    shape (`workspace.yaml` typically names several repos), which none of
    the single-repo scratch-workspace tests above exercise.
    """
    repo_a = tmp_path / "repo_a"
    _init_git_repo(repo_a, "a.py", "VALUE = 1\n")
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_b, "b.py", "VALUE = 2\n")

    from code_graph_io.update import run_workspace

    bundle_root = tmp_path / "bundle"
    result = runner.invoke(app, ["init", str(bundle_root)])
    assert result.exit_code == 0
    run_workspace([repo_a, repo_b], graph_dir=bundle_root, full=True)
    (bundle_root / "workspace.yaml").write_text(
        f"repositories:\n  repo_a:\n    path: {repo_a}\n  repo_b:\n    path: {repo_b}\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["sync", str(bundle_root)])
    assert result.exit_code == 0, result.output
    assert (bundle_root / "repositories" / "repo_a" / "files" / "a.py.md").exists()
    assert (bundle_root / "repositories" / "repo_b" / "files" / "b.py.md").exists()
    assert "repo_a: created 1" in result.output
    assert "repo_b: created 1" in result.output


def test_sync_command_exit_code_is_unchanged_by_stranded_wikilinks(tmp_path: Path) -> None:
    """`2026-08-21` spec: a stranded inbound `[[wikilink]]` is reported, never
    fatal -- the `sync` CLI counterpart to
    `mirror/test_plan.py::test_a_wikilink_into_a_renamed_page_is_stranded`."""
    repo_root = tmp_path / "repo"
    _init_git_repo(repo_root, "a.py", "VALUE = 1\n")

    from code_graph_io.update import run_workspace

    bundle_root = tmp_path / "bundle"
    assert runner.invoke(app, ["init", str(bundle_root)]).exit_code == 0
    run_workspace([repo_root], graph_dir=bundle_root, full=True)
    (bundle_root / "workspace.yaml").write_text(f"repositories:\n  repo:\n    path: {repo_root}\n", encoding="utf-8")

    quiet = runner.invoke(app, ["sync", str(bundle_root)])
    assert quiet.exit_code == 0, quiet.output
    assert "inbound [[wikilink]]" not in quiet.output

    # A concept page outside the mirror lane links into the freshly mirrored
    # file, then the source file is renamed -- stranding that wikilink.
    citing = bundle_root / "concepts" / "citing.md"
    citing.parent.mkdir(parents=True, exist_ok=True)
    citing.write_text(
        "---\ntype: Explanation\ntitle: Citing\ndescription: d\n---\n\n"
        "## Summary\n\nSee [[repositories/repo/files/a.py]] for the rest.\n",
        encoding="utf-8",
    )
    _git(repo_root, "mv", "a.py", "renamed.py")
    _git(repo_root, "commit", "-q", "-m", "rename a.py")
    run_workspace([repo_root], graph_dir=bundle_root, full=True)

    loud = runner.invoke(app, ["sync", str(bundle_root)])
    assert loud.exit_code == quiet.exit_code == 0, loud.output
    assert "repo: ! 1 inbound [[wikilink]]" in loud.output


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

    monkeypatch.setattr("code_wiki_okf.sync.run.apply_mirror", _boom)
    result = runner.invoke(app, ["sync", str(bundle_root)])
    assert result.exit_code == 1
    assert "repo: sync failed: disk full" in result.stderr
    assert "sync failed" not in result.stdout


def test_sync_command_prints_warnings_only_to_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle_root = _scratch_workspace(tmp_path)

    def warned(*args: object, **kwargs: object) -> SyncResult:
        return replace(run_sync_bundle(*args, **kwargs), warnings=("dependency has multiple implementations",))

    monkeypatch.setattr("code_wiki_okf.cli.sync_bundle", warned)
    result = runner.invoke(app, ["sync", str(bundle_root)])

    assert result.exit_code == 0, result.output
    assert "warning: dependency has multiple implementations" in result.stderr
    assert "warning:" not in result.stdout
    assert "entities: written" in result.stdout


def test_sync_command_reports_missing_sections_cleanly(tmp_path: Path) -> None:
    """`load_sections` propagates a bare `OSError` (`FileNotFoundError`) for a
    missing `sections` directory -- the mirror half's `load_sections` call
    reads `config.declarations_dir` now, not this package's own bundled
    assets, so a bundle whose `sections/` is missing or not yet populated
    (e.g. mid-`--config-dir` relocation) is a reachable state. `sync` must
    catch that and exit 1 with a clean message, not dump a raw traceback."""
    bundle_root = _scratch_workspace(tmp_path)
    shutil.rmtree(bundle_root / "sections")

    result = runner.invoke(app, ["sync", str(bundle_root)])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "sections" in result.output


def test_sync_command_dry_run_reports_missing_sections_cleanly(tmp_path: Path) -> None:
    """Accurate entity/catalog previews require the write declarations."""
    bundle_root = _scratch_workspace(tmp_path)
    shutil.rmtree(bundle_root / "sections")

    result = runner.invoke(app, ["sync", str(bundle_root), "--dry-run"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "sections" in result.output


def _empty_move_plan(bundle_root: Path) -> MovePlan:
    return MovePlan(root=bundle_root, moves=(), edits=(), refusals=(), unrebased=(), digests={})


def test_echo_plan_reports_no_changes_for_an_empty_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = MirrorPlan(
        repo="acme",
        targets=(),
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
        moves=(
            Move(
                source="repositories/acme/files/old.py.md",
                dest="repositories/acme/files/new.py.md",
                is_asset=False,
            ),
        ),
        edits=(),
        refusals=(),
        unrebased=(),
        digests={},
    )
    plan = MirrorPlan(
        repo="acme",
        targets=(),
        moves=move_plan,
        creates={"a.py": ({}, Render())},
        updates={"repositories/acme/files/b.py": Render()},
        deletions=("c.py",),
        declined_deletions=(
            DeclinedDeletion(
                resource="file:acme/d.py",
                path="repositories/acme/files/d.py.md",
                reason="prose-edited",
            ),
        ),
    )
    _echo_plan("acme", plan)
    output = capsys.readouterr().out
    assert "acme: would create a.py" in output
    assert "acme: would update repositories/acme/files/b.py" in output
    assert "acme: would move repositories/acme/files/old.py.md -> repositories/acme/files/new.py.md" in output
    assert "acme: would delete c.py" in output
    assert "acme: would decline deletion of repositories/acme/files/d.py.md (prose-edited)" in output


def _content_files(root: Path) -> dict[Path, bytes]:
    """*root*'s files, excluding sqlite's own `-shm`/`-wal` journal sidecars.

    `graph_dir` now resolves to `bundle_root` itself for the standalone CLI,
    so `code.db` lives inside the bundle -- merely *opening* it for a
    read-only pass makes sqlite create/touch those two journal files, which
    is sqlite's bookkeeping, not `validate` writing bundle content."""
    return {
        p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and p.suffix not in (".db-shm", ".db-wal")
    }


def test_validate_reports_missing_page_and_exits_nonzero(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)  # a bundle + one graphed repo, config already written -- untouched

    before = _content_files(bundle_root)
    result = runner.invoke(app, ["validate", str(bundle_root)])
    after = _content_files(bundle_root)

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
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    _seed_graph_multi(bundle_root, [("acme", "repo-a", ["shared"]), ("acme", "repo-b", ["shared"])])
    _write_config(bundle_root, ["repo-a", "repo-b"])

    result = runner.invoke(app, ["validate", str(bundle_root)])

    assert result.exit_code == 1
    assert "shared" in result.output.lower()
    assert "Traceback" not in result.output


def test_validate_reports_malformed_config(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)
    (bundle_root / "workspace.yaml").write_text("not: [valid, - yaml structure\n", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(bundle_root)])

    assert result.exit_code == 1


def test_validate_reports_missing_tags_vocabulary_cleanly(tmp_path: Path) -> None:
    """`load_vocabulary` propagates a bare `OSError` (`FileNotFoundError`) for
    a missing `tags.yaml` -- same family `load_schemas`/`load_sections`
    propagate for a missing `schema`/`sections` directory. `validate` must
    catch that and exit 1 with a clean message, not dump a raw traceback."""
    bundle_root = _scratch_workspace(tmp_path)
    (bundle_root / "tags.yaml").unlink()

    result = runner.invoke(app, ["validate", str(bundle_root)])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "tags.yaml" in result.output


def _init_empty_graph(bundle_root: Path) -> None:
    """The standalone CLI always resolves `graph_dir` to `bundle_root` itself
    (no repositories configured) -- `open_reader` still requires that db to
    exist, so tests that invoke `validate` against a bare `init` output need
    it seeded, even with zero nodes.

    `init` writes no config file any more, so `load_config`'s default address
    (`bundle_root/workspace.yaml`) must exist for a bare `validate`/`sync` to
    find anything at all -- an empty document is a valid one (no
    `repositories`, no `ignore`, no `state_gate`, every one defaulted).
    """
    open_store(bundle_root / "code.db", create=True).close()
    (bundle_root / "workspace.yaml").write_text("", encoding="utf-8")


def test_config_dir_relocates_the_declarations(tmp_path: Path) -> None:
    """`--config-dir` relocates `schema`/`sections`/`tags.yaml` at install
    time. Nothing persists the answer for a later command -- `--config-dir`
    must be passed again wherever it matters."""
    bundle_root = tmp_path / "bundle"
    declarations = tmp_path / "declarations"
    declarations.mkdir()
    init_result = runner.invoke(app, ["init", str(bundle_root), "--config-dir", str(declarations)])
    assert init_result.exit_code == 0, init_result.output
    assert (declarations / "schema" / "File.schema.json").is_file()
    assert not (bundle_root / "schema").exists()

    _init_empty_graph(bundle_root)
    validate_result = runner.invoke(app, ["validate", str(bundle_root), "--config-dir", str(declarations)])
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
        "## Public API\n\n> TODO: the main exports and when to use them. "
        "Link code with backticked `path:line` references.\n\n"
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
        "## Purpose\n\n> TODO: what this package does, who uses it, and why it exists, in one paragraph.\n\n"
        "## Public API\n\n> TODO: the main exports and when to use them. "
        "Link code with backticked `path:line` references.\n\n"
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

    The misplaced page is a `Dependency`, not a `Package`: narrowing (§5)
    drops the four repo-scoped types (Package/App/TestSuite/AgentPlugin) --
    now nested under `repositories/<repo>/<lane>/` -- from
    `placement.directory-mismatch`'s prefix check, but `Dependency` stays
    global and fully prefix-checkable, matching
    `test_placement_adoption.py::test_both_codes_fire_over_one_built_bundle`.
    """
    init_result = runner.invoke(app, ["init", str(tmp_path / "bundle")])
    assert init_result.exit_code == 0
    bundle_root = tmp_path / "bundle"
    _init_empty_graph(bundle_root)
    (bundle_root / "packages").mkdir()
    (bundle_root / "packages" / "httpx.md").write_text(
        '---\ntype: Dependency\ntitle: "httpx"\nresource: "dependency:pypi/httpx"\necosystem: "pypi"\n---\n\n'
        "## Why we depend on this\n\nSome real text goes here, filled in properly for this test.\n\n"
        "## Gotchas / workarounds\n\nSome real text goes here too.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(bundle_root)])

    assert result.exit_code == 1
    assert "found at packages/httpx.md" in result.output
    assert "expected dependencies/pypi/httpx" in result.output


def test_validate_still_reports_sync_findings_alongside_new_rules(tmp_path: Path) -> None:
    bundle_root = _scratch_workspace(tmp_path)  # a bundle + one graphed repo, never synced

    result = runner.invoke(app, ["validate", str(bundle_root)])

    assert result.exit_code == 1
    assert "sync.missing-page" in result.output


def test_echo_result_reports_every_kind_of_change(capsys: pytest.CaptureFixture[str]) -> None:
    result = MirrorResult(
        repo="acme",
        moved=(("repositories/acme/files/old.py.md", "repositories/acme/files/new.py.md"),),
        created=("a.py",),
        regenerated=("repositories/acme/files/b.py",),
        deleted=("c.py",),
        declined_deletions=(
            DeclinedDeletion(
                resource="file:acme/d.py",
                path="repositories/acme/files/d.py.md",
                reason="prose-edited",
            ),
        ),
        index_updates=(),
    )
    _echo_result("acme", result)
    output = capsys.readouterr().out
    assert "acme: created 1, updated 1, moved 1, deleted 1" in output
    assert "acme: created a.py" in output
    assert "acme: updated repositories/acme/files/b.py" in output
    assert "acme: moved repositories/acme/files/old.py.md -> repositories/acme/files/new.py.md" in output
    assert "acme: deleted c.py" in output
    assert "acme: declined deletion of repositories/acme/files/d.py.md (prose-edited)" in output


def test_echo_plan_reports_stranded_wikilinks_on_stderr(tmp_path, capsys):
    """One repo-prefixed line, on stderr -- `2026-08-21` spec §4.5: every one
    of these is stderr, and not one changes an exit code."""
    move_plan = MovePlan(
        root=tmp_path,
        moves=(
            Move(
                source="repositories/acme/files/old.py.md",
                dest="repositories/acme/files/new.py.md",
                is_asset=False,
            ),
        ),
        edits=(),
        refusals=(),
        unrebased=(),
        digests={},
        stranded=(Stranded(member="concepts/citing.md", target="repositories/acme/files/old.py.md", line=9),),
    )
    plan = MirrorPlan(
        repo="acme", targets=(), moves=move_plan, creates={}, updates={}, deletions=(), declined_deletions=()
    )
    _echo_plan("acme", plan)
    captured = capsys.readouterr()
    assert "acme: ! 1 inbound [[wikilink]]" in captured.err
    assert "inbound [[wikilink]]" not in captured.out


def test_echo_result_summary_line_is_byte_identical_with_stranded_present(tmp_path, capsys):
    """`_echo_result`'s summary line is a stable contract other tests match on
    -- append, never alter."""
    result = MirrorResult(
        repo="acme",
        moved=(("repositories/acme/files/old.py.md", "repositories/acme/files/new.py.md"),),
        created=(),
        regenerated=(),
        deleted=(),
        declined_deletions=(),
        index_updates=(),
    )
    _echo_result("acme", result, (Stranded(member="concepts/citing.md", target="x.md", line=9),))
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == "acme: created 0, updated 0, moved 1, deleted 0"
    assert "acme: ! 1 inbound [[wikilink]]" in captured.err
