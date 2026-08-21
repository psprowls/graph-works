import importlib.resources
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from code_graph_io.handle import open_reader
from code_graph_io.update import run_workspace
from code_wiki_okf.config import RepoConfig
from code_wiki_okf.mirror import plan as plan_module
from code_wiki_okf.mirror.plan import plan_mirror
from okf_ext.sections import load_sections
from okf_io import load_bundle

_AT = datetime(2026, 1, 1, tzinfo=UTC)
_PLACEHOLDER = "> TODO: anything a reader should know about this file that the generated sections below don't capture."


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _head(repo: Path) -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("VALUE = 1\n")
    (repo / "b.py").write_text("VALUE = 2\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _write_page(bundle_root: Path, repo_name: str, rel_path: str, *, notes: str, last_commit: str | None) -> None:
    path = bundle_root / "repositories" / repo_name / "fs" / f"{rel_path}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    commit_line = f"last_updated_commit: {last_commit}\n" if last_commit else ""
    path.write_text(
        f"---\ntype: File\ntitle: {Path(rel_path).name}\nresource: file:{repo_name}/{rel_path}\n{commit_line}---\n\n"
        f"## Notes\n\n{notes}\n",
        encoding="utf-8",
    )


def _reader_for(repo_root: Path, graph_dir: Path):
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    return open_reader(graph_dir=graph_dir)


def test_new_tracked_path_becomes_a_create(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    reader = _reader_for(repo_root, tmp_path / "graph")
    bundle = load_bundle(bundle_root)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    plan = plan_mirror(bundle, reader, repo, tracked=("a.py", "b.py"), sha=_head(repo_root), at=_AT)

    assert set(plan.creates) == {"a.py", "b.py"}
    assert plan.moves.is_empty
    assert plan.deletions == ()
    reader.close()


def test_vanished_path_with_placeholder_notes_is_deleted(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    sha = _head(repo_root)
    bundle_root = tmp_path / "bundle"
    _write_page(bundle_root, "acme", "gone.py", notes=_PLACEHOLDER, last_commit=sha)
    reader = _reader_for(repo_root, tmp_path / "graph")
    bundle = load_bundle(bundle_root)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    plan = plan_mirror(bundle, reader, repo, tracked=("a.py", "b.py"), sha=sha, at=_AT)

    assert plan.deletions == ("gone.py",)
    assert plan.declined_deletions == ()
    reader.close()


def test_vanished_path_with_edited_notes_is_declined(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    sha = _head(repo_root)
    bundle_root = tmp_path / "bundle"
    _write_page(bundle_root, "acme", "gone.py", notes="A human wrote this.", last_commit=sha)
    reader = _reader_for(repo_root, tmp_path / "graph")
    bundle = load_bundle(bundle_root)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    plan = plan_mirror(bundle, reader, repo, tracked=("a.py", "b.py"), sha=sha, at=_AT)

    assert plan.deletions == ()
    assert len(plan.declined_deletions) == 1
    assert plan.declined_deletions[0].reason == "prose-edited"
    assert plan.declined_deletions[0].path == "repositories/acme/fs/gone.py.md"
    reader.close()


def test_git_rename_becomes_a_move_not_a_delete_plus_create(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    sha_before = _head(repo_root)
    bundle_root = tmp_path / "bundle"
    _write_page(bundle_root, "acme", "a.py", notes=_PLACEHOLDER, last_commit=sha_before)
    _write_page(bundle_root, "acme", "b.py", notes=_PLACEHOLDER, last_commit=sha_before)

    _git(repo_root, "mv", "a.py", "renamed.py")
    _git(repo_root, "commit", "-q", "-m", "rename a.py")
    sha_after = _head(repo_root)

    reader = _reader_for(repo_root, tmp_path / "graph")
    bundle = load_bundle(bundle_root)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    plan = plan_mirror(bundle, reader, repo, tracked=("renamed.py", "b.py"), sha=sha_after, at=_AT)

    assert len(plan.moves.moves) == 1
    move = plan.moves.moves[0]
    assert move.source == "repositories/acme/fs/a.py.md"
    assert move.dest == "repositories/acme/fs/renamed.py.md"
    assert "renamed.py" not in plan.creates
    assert plan.deletions == ()
    assert plan.declined_deletions == ()
    reader.close()


def test_vanished_path_with_no_last_updated_commit_falls_to_deletion_guard(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    sha = _head(repo_root)
    bundle_root = tmp_path / "bundle"
    _write_page(bundle_root, "acme", "gone.py", notes=_PLACEHOLDER, last_commit=None)
    reader = _reader_for(repo_root, tmp_path / "graph")
    bundle = load_bundle(bundle_root)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    plan = plan_mirror(bundle, reader, repo, tracked=("a.py", "b.py"), sha=sha, at=_AT)

    assert plan.deletions == ("gone.py",)
    reader.close()


def test_rename_is_found_when_another_deletion_candidates_stamp_is_more_recent(tmp_path: Path) -> None:
    """The diff base must be the commit that predates every candidate
    deletion's stamp, not whichever stamp happens to sort lowest as a hex
    string -- SHA-1 has no relation to commit order, so a naive `min()` over
    the stamps could pick a *more recent* commit than the one the rename
    actually needs, and silently miss it.

    `earliest_common_ancestor` avoids that failure mode structurally, not
    just for these particular SHAs: it asks git's own ancestry
    (`merge-base --octopus`) rather than ever comparing SHA strings, so its
    answer cannot depend on which of two hashes happens to sort lower --
    `test_earliest_common_ancestor_of_a_linear_pair_is_the_older_commit` in
    `test_git_state.py` proves that deterministically, for both argument
    orders. This test does not need to reproduce (let alone hunt for) an
    unlucky hash relationship to prove the integration still works; it just
    needs a real scenario where a second candidate deletion's stamp
    (`other.py`) is chronologically *after* the rename, which is the shape
    that would have tripped a lexicographic `min()`. `other.py`'s stamp is
    simply the rename commit's own SHA -- already later than `a.py`'s stamp
    by construction, no decoys or hunting required.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "a.py").write_text("VALUE = 1\n")
    (repo_root / "b.py").write_text("VALUE = 2\n")
    (repo_root / "other.py").write_text("VALUE = 3\n")
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@t")
    _git(repo_root, "config", "user.name", "t")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")
    stamp_a = _head(repo_root)  # a.py's page was last regenerated here, right before the rename

    _git(repo_root, "mv", "a.py", "renamed.py")
    _git(repo_root, "commit", "-q", "-m", "rename a.py")
    stamp_other = _head(repo_root)  # other.py's page was (re)regenerated here, after the rename

    (repo_root / "other.py").unlink()
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "delete other.py")
    head_sha = _head(repo_root)

    bundle_root = tmp_path / "bundle"
    _write_page(bundle_root, "acme", "a.py", notes=_PLACEHOLDER, last_commit=stamp_a)
    _write_page(bundle_root, "acme", "other.py", notes=_PLACEHOLDER, last_commit=stamp_other)
    reader = _reader_for(repo_root, tmp_path / "graph")
    bundle = load_bundle(bundle_root)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    plan = plan_mirror(bundle, reader, repo, tracked=("renamed.py", "b.py"), sha=head_sha, at=_AT)

    assert len(plan.moves.moves) == 1
    move = plan.moves.moves[0]
    assert move.source == "repositories/acme/fs/a.py.md"
    assert move.dest == "repositories/acme/fs/renamed.py.md"
    assert "renamed.py" not in plan.creates
    assert plan.deletions == ("other.py",)
    reader.close()
    reader.close()


def test_a_dot_nested_page_already_on_disk_is_not_re_created(tmp_path: Path) -> None:
    """The second-sync abort, pinned. `plan_mirror` builds `previous` from
    `resource_index`, which reads `bundle.concepts`, which comes from okf-io's
    one walk. While that walk dropped dot-nested members the page fell into
    `candidate_creates = tracked - previous` on every run, and
    `write_new_page` refused to overwrite the file already sitting there --
    FileExistsError, per-repo abort, exit 1.
    """
    repo_root = _scratch_repo(tmp_path)
    (repo_root / ".agents").mkdir()
    (repo_root / ".agents" / "SKILL.md").write_text("# Skill\n")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "skill")
    sha = _head(repo_root)

    bundle_root = tmp_path / "bundle"
    _write_page(bundle_root, "acme", "a.py", notes=_PLACEHOLDER, last_commit=sha)
    _write_page(bundle_root, "acme", "b.py", notes=_PLACEHOLDER, last_commit=sha)
    _write_page(bundle_root, "acme", ".agents/SKILL.md", notes=_PLACEHOLDER, last_commit=sha)

    reader = _reader_for(repo_root, tmp_path / "graph")
    bundle = load_bundle(bundle_root)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    plan = plan_mirror(bundle, reader, repo, tracked=("a.py", "b.py", ".agents/SKILL.md"), sha=sha, at=_AT)

    assert plan.creates == {}
    assert plan.deletions == ()
    reader.close()


def test_the_notes_placeholder_constant_matches_the_declared_file_notes_section() -> None:
    """`plan.py` hardcodes `_NOTES_PLACEHOLDER` rather than depending on a
    loaded `SectionSet` (see its own docstring), which means nothing ties it
    to `File.yaml` today. This guards the two from drifting apart silently."""
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "_sections"
    section_set = load_sections(str(assets))
    notes = next(s for s in section_set.types["File"].sections if s.heading == "Notes")
    assert plan_module._NOTES_PLACEHOLDER.strip() == notes.placeholder.strip()
