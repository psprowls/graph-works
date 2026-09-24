import importlib.resources
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from code_graph_io.handle import GraphReader, open_reader
from code_graph_io.update import run_workspace
from code_wiki_okf.config import RepoConfig
from code_wiki_okf.mirror import plan as plan_module
from code_wiki_okf.mirror.plan import plan_mirror
from code_wiki_okf.placement import PlacementError
from okf_ext.moves import MovePlan
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
    _git(repo, "remote", "add", "origin", "https://github.com/local/acme.git")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _write_page(bundle_root: Path, repo_name: str, rel_path: str, *, notes: str, last_commit: str | None) -> None:
    path = bundle_root / "code-graph" / repo_name / "file-system" / f"{rel_path}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    commit_line = f"last_updated_commit: {last_commit}\n" if last_commit else ""
    path.write_text(
        f"---\ntype: File\ntitle: {Path(rel_path).name}\n"
        f"resource: file:local/{repo_name}/{rel_path}\n{commit_line}---\n\n"
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
    assert plan.target_for("a.py").member == "code-graph/acme/file-system/a.py.md"
    with pytest.raises(KeyError, match=r"missing\.py"):
        plan.target_for("missing.py")
    assert plan.moves.is_empty
    assert plan.deletions == ()
    reader.close()


@pytest.mark.parametrize(
    ("resources", "message"),
    [
        ((), "no graph Repository resource"),
        (("repo:local/acme", "repo:other/acme"), "is claimed by"),
    ],
)
def test_repository_identity_must_resolve_exactly_once(
    tmp_path: Path, resources: tuple[str, ...], message: str
) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    reader = cast(
        GraphReader,
        SimpleNamespace(
            list_repositories=lambda: tuple(
                SimpleNamespace(name="acme", attrs={"uri": resource}) for resource in resources
            )
        ),
    )
    repo = RepoConfig(name="acme", path=tmp_path / "repo", ignore=())

    with pytest.raises(PlacementError, match=message):
        plan_mirror(load_bundle(bundle_root), reader, repo, tracked=(), sha="abc123", at=_AT)


def test_mirror_plan_recursively_freezes_move_digests(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    reader = _reader_for(repo_root, tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    source_digests = {"code-graph/acme/file-system/a.py.md": "before"}
    plan = replace(
        plan,
        moves=MovePlan(
            root=bundle_root,
            moves=(),
            edits=(),
            refusals=(),
            unrebased=(),
            digests=source_digests,
        ),
    )
    source_digests["code-graph/acme/file-system/a.py.md"] = "after"
    digests = cast(dict[str, str], plan.moves.digests)

    with pytest.raises(TypeError):
        digests["code-graph/acme/file-system/a.py.md"] = "changed"
    assert plan.moves.digests["code-graph/acme/file-system/a.py.md"] == "before"
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
    assert plan.declined_deletions[0].path == "code-graph/acme/file-system/gone.py.md"
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
    assert move.source == "code-graph/acme/file-system/a.py.md"
    assert move.dest == "code-graph/acme/file-system/renamed.py.md"
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
    _git(repo_root, "remote", "add", "origin", "https://github.com/local/acme.git")
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
    assert move.source == "code-graph/acme/file-system/a.py.md"
    assert move.dest == "code-graph/acme/file-system/renamed.py.md"
    assert "renamed.py" not in plan.creates
    assert plan.deletions == ("other.py",)
    reader.close()
    reader.close()


def test_a_wikilink_into_a_renamed_page_is_stranded(tmp_path: Path) -> None:
    """The item's done-when for the mirror lane: a wikilink-only bundle and a
    quiet one produce different plans."""
    repo_root = _scratch_repo(tmp_path)
    sha_before = _head(repo_root)
    bundle_root = tmp_path / "bundle"
    _write_page(bundle_root, "acme", "a.py", notes=_PLACEHOLDER, last_commit=sha_before)
    _write_page(bundle_root, "acme", "b.py", notes=_PLACEHOLDER, last_commit=sha_before)
    # Outside the mirror lane, so it is never a deletion candidate and never
    # trips the prose-edited guard on a mirrored page.
    citing = bundle_root / "concepts" / "citing.md"
    citing.parent.mkdir(parents=True, exist_ok=True)
    citing.write_text(
        "---\ntype: Explanation\ntitle: Citing\ndescription: d\n---\n\n"
        "## Summary\n\nSee [[code-graph/acme/file-system/a.py]] for the rest.\n",
        encoding="utf-8",
    )

    _git(repo_root, "mv", "a.py", "renamed.py")
    _git(repo_root, "commit", "-q", "-m", "rename a.py")
    sha_after = _head(repo_root)

    reader = _reader_for(repo_root, tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("renamed.py", "b.py"), sha=sha_after, at=_AT)

    assert [entry.member for entry in plan.moves.stranded] == ["concepts/citing.md"]
    assert plan.moves.stranded[0].target == "code-graph/acme/file-system/a.py.md"
    reader.close()


def test_a_quiet_bundle_strands_nothing_on_the_same_rename(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    sha_before = _head(repo_root)
    bundle_root = tmp_path / "bundle"
    _write_page(bundle_root, "acme", "a.py", notes=_PLACEHOLDER, last_commit=sha_before)
    _git(repo_root, "mv", "a.py", "renamed.py")
    _git(repo_root, "commit", "-q", "-m", "rename a.py")
    sha_after = _head(repo_root)

    reader = _reader_for(repo_root, tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("renamed.py",), sha=sha_after, at=_AT)

    assert plan.moves.stranded == ()


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
    assets = importlib.resources.files("code_wiki_okf") / "assets" / "sections"
    section_set = load_sections(str(assets))
    notes = next(s for s in section_set.types["File"].sections if s.heading == "Notes")
    assert plan_module._NOTES_PLACEHOLDER.strip() == notes.placeholder.strip()


def test_file_targets_are_policy_members_and_reserved_indexes_are_refused(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    reader = _reader_for(repo_root, tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    plan = plan_mirror(
        load_bundle(bundle_root),
        reader,
        repo,
        tracked=("src/main.py", "src/index.md"),
        sha=_head(repo_root),
        at=_AT,
    )

    assert plan.target_for("src/main.py").resource == "file:local/acme/src/main.py"
    assert plan.target_for("src/main.py").member == "code-graph/acme/file-system/src/main.py.md"
    assert plan.target_for("src/index.md").member == "code-graph/acme/file-system/src/index.md.md"

    with pytest.raises(PlacementError, match=r"index.md"):
        plan_mirror(
            load_bundle(bundle_root),
            reader,
            repo,
            tracked=("src/index",),
            sha=_head(repo_root),
            at=_AT,
        )
    reader.close()


@pytest.mark.parametrize("unsafe", ("../secret.py", "src/CON.txt", "src/bad:name.py"))
def test_unsafe_tracked_path_refuses_during_planning(tmp_path: Path, unsafe: str) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    reader = _reader_for(repo_root, tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    before = tuple(bundle_root.rglob("*"))
    with pytest.raises(PlacementError, match="file:local/acme"):
        plan_mirror(load_bundle(bundle_root), reader, repo, tracked=(unsafe,), sha=_head(repo_root), at=_AT)
    assert tuple(bundle_root.rglob("*")) == before
    reader.close()


def test_misplaced_file_resource_is_not_moved_or_updated(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    misplaced = bundle_root / "misplaced" / "a.py.md"
    misplaced.parent.mkdir(parents=True)
    misplaced.write_text(
        f"---\ntype: File\ntitle: a.py\nresource: file:local/acme/a.py\n---\n\n## Notes\n\n{_PLACEHOLDER}\n",
        encoding="utf-8",
    )
    reader = _reader_for(repo_root, tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    before = {
        path.relative_to(bundle_root).as_posix(): path.read_bytes() for path in bundle_root.rglob("*") if path.is_file()
    }

    with pytest.raises(PlacementError, match=r"code-graph/acme/file-system/a.py"):
        plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)

    after = {
        path.relative_to(bundle_root).as_posix(): path.read_bytes() for path in bundle_root.rglob("*") if path.is_file()
    }
    assert after == before
    reader.close()


def test_occupied_canonical_member_with_another_resource_refuses(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    occupied = bundle_root / "code-graph" / "acme" / "file-system" / "a.py.md"
    occupied.parent.mkdir(parents=True)
    occupied.write_text(
        "---\ntype: File\ntitle: other.py\nresource: file:local/acme/other.py\n---\n",
        encoding="utf-8",
    )
    reader = _reader_for(repo_root, tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    with pytest.raises(PlacementError, match=r"a.py.md"):
        plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    reader.close()


def test_filesystem_equivalent_tracked_paths_collide_before_mirror_writes(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    reader = _reader_for(repo_root, tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    with pytest.raises(PlacementError, match="filesystem-equivalent"):
        plan_mirror(
            load_bundle(bundle_root),
            reader,
            repo,
            tracked=("Caf\N{LATIN SMALL LETTER E WITH ACUTE}.py", "cafe\N{COMBINING ACUTE ACCENT}.py"),
            sha=_head(repo_root),
            at=_AT,
        )

    assert not tuple(bundle_root.rglob("*.md"))
    reader.close()


def test_filesystem_equivalent_existing_member_refuses_mirror_target(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    occupied = bundle_root / "code-graph" / "ACME" / "file-system" / "A.py.md"
    occupied.parent.mkdir(parents=True)
    occupied.write_text("---\ntype: Note\ntitle: occupied\n---\n", encoding="utf-8")
    reader = _reader_for(repo_root, tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    with pytest.raises(PlacementError, match="filesystem-equivalent"):
        plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    reader.close()


def test_existing_file_page_requires_exact_declared_type(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    _write_page(bundle_root, "acme", "a.py", notes=_PLACEHOLDER, last_commit=_head(repo_root))
    page = bundle_root / "code-graph" / "acme" / "file-system" / "a.py.md"
    page.write_text(page.read_text(encoding="utf-8").replace("type: File", 'type: " File "'), encoding="utf-8")
    reader = _reader_for(repo_root, tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    with pytest.raises(PlacementError, match=r"declares type .* instead of File"):
        plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    reader.close()
