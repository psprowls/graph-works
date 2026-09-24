import subprocess
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from code_graph_io.handle import open_reader
from code_graph_io.update import run_workspace
from code_wiki_okf.config import RepoConfig
from code_wiki_okf.init import install_bundle
from code_wiki_okf.mirror.apply import apply_mirror
from code_wiki_okf.mirror.model import MirrorPlan, MirrorTarget
from code_wiki_okf.mirror.plan import plan_mirror
from code_wiki_okf.placement import PlacementError
from okf_ext.generators import Render
from okf_ext.moves import Move, MovePlan, MoveResult
from okf_ext.writing import ApplyResult, WriteFailure
from okf_io import load_bundle

_AT = datetime(2026, 1, 1, tzinfo=UTC)
_TODAY = date(2026, 1, 1)
_NOTES_HEADING = "## Notes"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _head(repo: Path) -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("VALUE = 1\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "remote", "add", "origin", "https://github.com/local/acme.git")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _notes_body(text: str) -> str:
    """The `## Notes` section body of a page's text, verbatim."""
    start = text.index(_NOTES_HEADING) + len(_NOTES_HEADING)
    rest = text[start:]
    next_heading = rest.find("\n## ")
    section = rest[:next_heading] if next_heading != -1 else rest
    return section.strip()


def _set_notes(target: Path, text: str) -> None:
    content = target.read_text(encoding="utf-8")
    start = content.index(_NOTES_HEADING) + len(_NOTES_HEADING)
    rest = content[start:]
    next_heading = rest.find("\n## ")
    tail = rest[next_heading:] if next_heading != -1 else ""
    new_content = content[:start] + "\n\n" + text + "\n" + tail
    target.write_text(new_content, encoding="utf-8")


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _empty_move_plan(bundle_root: Path, *, moves: tuple[Move, ...] = ()) -> MovePlan:
    return MovePlan(
        root=bundle_root,
        moves=moves,
        edits=(),
        refusals=(),
        unrebased=(),
        digests={},
    )


def test_apply_creates_and_fills_generated_sections_in_one_pass(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    run_workspace([repo_root], graph_dir=tmp_path / "graph", full=True)
    reader = open_reader(graph_dir=tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    result = apply_mirror(bundle_root, plan, today=_TODAY)

    assert result.created == ("a.py",)
    target = bundle_root / "code-graph" / "acme" / "file-system" / "a.py.md"
    assert target.exists()
    assert "not yet generated" not in target.read_text(encoding="utf-8")
    reader.close()


def test_a_fresh_mirror_reports_each_created_page_once(tmp_path: Path) -> None:
    """A create writes a skeleton and the regeneration pass fills it, but that
    is one page's worth of work, not two. The entity lane already de-overlaps
    created and updated in `entities/sync.py`; the mirror lane must agree, or
    a fresh scan reports twice the pages it touched.
    """
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    run_workspace([repo_root], graph_dir=tmp_path / "graph", full=True)
    reader = open_reader(graph_dir=tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    result = apply_mirror(bundle_root, plan, today=_TODAY)

    assert result.created == ("a.py",)
    assert result.regenerated == ()


def test_apply_regenerates_a_pre_existing_page_on_a_later_run(tmp_path: Path) -> None:
    """The `created_ids` filter in `apply_mirror` must only exclude pages
    created in *this* run -- a page created by an earlier run and now
    genuinely changed on disk still has to show up in `regenerated`. Without
    this test, a filter that over-matches (e.g. one that always empties
    `regenerated`, or one keyed on the wrong shape) would pass every other
    test in this file, since none of them drive a real second-run
    regeneration of an already-existing page.
    """
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    first_plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    first_result = apply_mirror(bundle_root, first_plan, today=_TODAY)
    assert first_result.created == ("a.py",)
    reader.close()

    # A real content change to the tracked source, re-scanned by the graph,
    # so the second plan proposes a genuine update rather than a no-op. A
    # bare top-level assignment (`VALUE = 1`) is content but not a *symbol*
    # the graph reader records; a function definition is, so this is what
    # actually moves `Symbols`/`Exports` in the regenerated page.
    (repo_root / "a.py").write_text("VALUE = 1\n\n\ndef helper() -> int:\n    return VALUE\n", encoding="utf-8")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "change a.py")
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)

    second_plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    assert tuple(second_plan.updates) == ("code-graph/acme/file-system/a.py",)

    second_result = apply_mirror(bundle_root, second_plan, today=_TODAY)
    reader.close()

    assert second_result.created == ()
    assert second_result.regenerated == ("code-graph/acme/file-system/a.py",)


def test_apply_reports_generator_write_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    failure = WriteFailure(path="code-graph/acme/file-system/a.py.md", kind="commit-error", error="disk full")
    monkeypatch.setattr(
        "code_wiki_okf.mirror.apply.apply_generators",
        lambda *_args, **_kwargs: ApplyResult(written=(), failed=(failure,), skipped=()),
    )

    result = apply_mirror(bundle_root, plan, today=_TODAY)

    assert result.failed == ("code-graph/acme/file-system/a.py.md: commit-error: disk full",)
    assert result.regenerated == ()


@pytest.mark.parametrize("conflict", ("occupied", "duplicate"))
def test_apply_live_preflight_refuses_late_conflict_without_partial_writes(tmp_path: Path, conflict: str) -> None:
    repo_root = _scratch_repo(tmp_path)
    (repo_root / "b.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "add b")
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    plan = plan_mirror(
        load_bundle(bundle_root),
        reader,
        repo,
        tracked=("a.py", "b.py"),
        sha=_head(repo_root),
        at=_AT,
    )

    b_target = bundle_root / "code-graph" / "acme" / "file-system" / "b.py.md"
    b_target.parent.mkdir(parents=True, exist_ok=True)
    if conflict == "occupied":
        b_target.write_text(
            "---\ntype: File\ntitle: other.py\nresource: file:local/acme/other.py\n---\n",
            encoding="utf-8",
        )
    else:
        b_target.write_text(
            "---\ntype: File\ntitle: b.py\nresource: file:local/acme/b.py\n---\n",
            encoding="utf-8",
        )
        duplicate = bundle_root / "duplicates" / "b.py.md"
        duplicate.parent.mkdir(parents=True)
        duplicate.write_text(b_target.read_text(encoding="utf-8"), encoding="utf-8")
    before = _file_bytes(bundle_root)

    with pytest.raises(PlacementError, match=r"duplicate resource|occupied"):
        apply_mirror(bundle_root, plan, today=_TODAY)

    assert _file_bytes(bundle_root) == before
    assert not (bundle_root / "code-graph" / "acme" / "file-system" / "a.py.md").exists()
    reader.close()


def test_apply_second_run_is_idempotent(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    run_workspace([repo_root], graph_dir=tmp_path / "graph", full=True)
    reader = open_reader(graph_dir=tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle_root, plan, today=_TODAY)

    bundle2 = load_bundle(bundle_root)
    second_plan = plan_mirror(bundle2, reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    assert second_plan.is_empty
    reader.close()


def test_apply_second_run_is_idempotent_with_a_fresh_timestamp_and_sha(tmp_path: Path) -> None:
    """The realistic shape of a second sync run: `at` is `datetime.now(UTC)`
    called fresh every invocation (Task 7's design), and `sha` moves forward
    with the repo even when the tracked file itself did not change. Neither
    provenance value can equal what a previous run already stamped -- so if
    `_render_matches_disk` compared them, this second `plan_mirror` call
    would propose `a.py`'s page as an update on every single run, forever,
    even though nothing about its *content* changed. `test_apply_second_run_is_idempotent`
    above reuses the same `_AT`/`sha` for both calls, which cannot catch that:
    this test is the one that would have.
    """
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle_root, plan, today=_TODAY)
    reader.close()

    # The repo advances -- an unrelated commit, nothing touching `a.py` --
    # and enough time passes for a fresh `datetime.now(UTC)` to differ from
    # `_AT`. Both provenance inputs a real second run would carry are
    # therefore different from what the first run stamped, while `a.py`'s
    # own content and the graph's view of it are unchanged.
    sha_before = _head(repo_root)
    (repo_root / "unrelated.txt").write_text("noise\n")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "unrelated change")
    sha_after = _head(repo_root)
    at_after = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
    assert sha_after != sha_before
    assert at_after != _AT

    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader2 = open_reader(graph_dir=graph_dir)
    bundle2 = load_bundle(bundle_root)
    second_plan = plan_mirror(bundle2, reader2, repo, tracked=("a.py",), sha=sha_after, at=at_after)

    assert second_plan.is_empty
    reader2.close()


def test_apply_guarded_deletion_removes_file_and_reconciles_index(tmp_path: Path) -> None:
    """A deletion that clears the prose guard (`## Notes` still at its
    placeholder) must actually land on disk, not just appear in the plan --
    and `apply_mirror`'s final reload must pick that removal up before
    `update_index()` runs, or the dead entry survives in `index.md`.

    `_render_matches_disk`'s idempotence fix (see the sibling commit) is
    about `updates`; this exercises the other write this task added: the
    `plan.deletions` loop's `target.unlink(...)` and the `final_bundle =
    load_bundle(...) if plan.deletions else reloaded` branch right after it.
    Neither had a test that drove an actual (not merely planned, not merely
    declined) deletion through `apply_mirror` before this.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "a.py").write_text("VALUE = 1\n")
    (repo_root / "b.py").write_text("VALUE = 2\n")
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@t")
    _git(repo_root, "config", "user.name", "t")
    _git(repo_root, "remote", "add", "origin", "https://github.com/local/acme.git")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py", "b.py"), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle_root, plan, today=_TODAY)
    reader.close()

    a_target = bundle_root / "code-graph" / "acme" / "file-system" / "a.py.md"
    index_target = bundle_root / "code-graph" / "acme" / "file-system" / "index.md"
    repo_index_target = bundle_root / "code-graph" / "acme" / "index.md"
    assert a_target.exists()
    assert "a.py.md" in index_target.read_text(encoding="utf-8")
    assert repo_index_target.exists()

    # `a.py`'s Notes section is untouched (still the placeholder), so the
    # deletion guard admits it -- unlike test_apply_declined_deletion, this
    # is the deletion actually going through.
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader2 = open_reader(graph_dir=graph_dir)
    bundle2 = load_bundle(bundle_root)
    plan2 = plan_mirror(bundle2, reader2, repo, tracked=("b.py",), sha=_head(repo_root), at=_AT)
    assert plan2.deletions == ("a.py",)
    assert plan2.declined_deletions == ()

    result2 = apply_mirror(bundle_root, plan2, today=_TODAY)
    reader2.close()

    assert result2.deleted == ("a.py",)
    assert not a_target.exists()
    index_text = index_target.read_text(encoding="utf-8")
    assert "a.py.md" not in index_text
    assert "b.py.md" in index_text


def test_apply_scopes_index_update_to_this_repos_mirror(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    unrelated = bundle_root / "packages" / "index.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("- [[packages/untouched]]\n", encoding="utf-8")
    (bundle_root / "packages" / "untouched.md").write_text(
        "---\ntype: Package\ntitle: untouched\nresource: pkg:untouched\n---\n", encoding="utf-8"
    )
    before = unrelated.read_text(encoding="utf-8")

    run_workspace([repo_root], graph_dir=tmp_path / "graph", full=True)
    reader = open_reader(graph_dir=tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle_root, plan, today=_TODAY)

    assert unrelated.read_text(encoding="utf-8") == before
    reader.close()


def test_apply_rename_preserves_notes_and_removes_old_path(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle_root, plan, today=_TODAY)
    reader.close()

    old_target = bundle_root / "code-graph" / "acme" / "file-system" / "a.py.md"
    custom_notes = "A human wrote this specific detail about a.py."
    _set_notes(old_target, custom_notes)

    _git(repo_root, "mv", "a.py", "renamed.py")
    _git(repo_root, "commit", "-q", "-m", "rename a.py")
    sha_after = _head(repo_root)

    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    bundle2 = load_bundle(bundle_root)
    plan2 = plan_mirror(bundle2, reader, repo, tracked=("renamed.py",), sha=sha_after, at=_AT)
    result2 = apply_mirror(bundle_root, plan2, today=_TODAY)

    new_target = bundle_root / "code-graph" / "acme" / "file-system" / "renamed.py.md"
    assert new_target.exists()
    assert _notes_body(new_target.read_text(encoding="utf-8")) == custom_notes
    assert not old_target.exists()
    assert result2.moved == (("code-graph/acme/file-system/a.py.md", "code-graph/acme/file-system/renamed.py.md"),)
    reader.close()


def test_apply_reports_move_write_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    with open_reader(graph_dir=graph_dir) as reader:
        first = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
        apply_mirror(bundle_root, first, today=_TODAY)
    _git(repo_root, "mv", "a.py", "renamed.py")
    _git(repo_root, "commit", "-q", "-m", "rename")
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_mirror(
            load_bundle(bundle_root),
            reader,
            repo,
            tracked=("renamed.py",),
            sha=_head(repo_root),
            at=_AT,
        )
    failure = WriteFailure(path="code-graph/acme/file-system/renamed.py.md", kind="commit-error", error="disk full")
    monkeypatch.setattr(
        "code_wiki_okf.mirror.apply.moves.apply",
        lambda *_args, **_kwargs: MoveResult(moved=(), written=(), failed=(failure,), pruned=()),
    )

    result = apply_mirror(bundle_root, plan, today=_TODAY)

    assert result.failed == ("code-graph/acme/file-system/renamed.py.md: commit-error: disk full",)


def test_apply_declined_deletion_leaves_file_and_is_reported(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "a.py").write_text("VALUE = 1\n")
    (repo_root / "b.py").write_text("VALUE = 2\n")
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@t")
    _git(repo_root, "config", "user.name", "t")
    _git(repo_root, "remote", "add", "origin", "https://github.com/local/acme.git")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py", "b.py"), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle_root, plan, today=_TODAY)
    reader.close()

    a_target = bundle_root / "code-graph" / "acme" / "file-system" / "a.py.md"
    _set_notes(a_target, "A human wrote this and does not want a.py's page deleted.")

    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    bundle2 = load_bundle(bundle_root)
    plan2 = plan_mirror(bundle2, reader, repo, tracked=("b.py",), sha=_head(repo_root), at=_AT)
    result2 = apply_mirror(bundle_root, plan2, today=_TODAY)

    assert a_target.exists()
    assert len(result2.declined_deletions) == 1
    assert result2.declined_deletions[0].path == "code-graph/acme/file-system/a.py.md"
    reader.close()


def test_apply_reconciles_every_policy_affected_directory(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    nested = repo_root / "src" / "pkg" / "nested.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "add nested file")

    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())

    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_mirror(
            load_bundle(bundle_root),
            reader,
            repo,
            tracked=("src/pkg/nested.py",),
            sha=_head(repo_root),
            at=_AT,
        )
        result = apply_mirror(bundle_root, plan, today=_TODAY)

    expected_directories = (
        "code-graph/acme",
        "code-graph/acme/file-system",
        "code-graph/acme/file-system/src",
        "code-graph/acme/file-system/src/pkg",
    )
    assert all((bundle_root / directory / "index.md").exists() for directory in expected_directories)
    assert {update.path for update in result.index_updates} == {
        f"{directory}/index.md" for directory in expected_directories
    }


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("source", "conflicts with resource identity"),
        ("member", "is not canonical"),
        ("repo", "conflicts with resource identity"),
        ("create", "no canonical mirror target"),
        ("update", "no canonical mirror target"),
        ("move", "leaves canonical mirror targets"),
    ],
)
def test_apply_refuses_tampered_plan_identity_before_writes(tmp_path: Path, defect: str, message: str) -> None:
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    target = MirrorTarget(
        resource="file:local/acme/a.py",
        source_path="a.py",
        member="code-graph/acme/file-system/a.py.md",
    )
    plan = MirrorPlan(
        repo="acme",
        targets=(target,),
        moves=_empty_move_plan(bundle_root),
        creates={},
        updates={},
        deletions=(),
        declined_deletions=(),
    )
    if defect == "source":
        plan = replace(plan, targets=(replace(target, source_path="b.py"),))
    elif defect == "member":
        plan = replace(plan, targets=(replace(target, member="code-graph/acme/file-system/wrong.py.md"),))
    elif defect == "repo":
        plan = replace(plan, repo="other")
    elif defect == "create":
        plan = replace(plan, targets=(), creates={"a.py": ({"type": "File"}, Render())})
    elif defect == "update":
        plan = replace(plan, targets=(), updates={"code-graph/acme/file-system/a.py": Render()})
    else:
        plan = replace(
            plan,
            moves=_empty_move_plan(
                bundle_root,
                moves=(
                    Move("code-graph/acme/file-system/a.py.md", "code-graph/acme/file-system/b.py.md", is_asset=False),
                ),
            ),
        )
    before = _file_bytes(bundle_root)

    with pytest.raises(PlacementError, match=message):
        apply_mirror(bundle_root, plan, today=_TODAY)

    assert _file_bytes(bundle_root) == before


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("misplaced", "found at"),
        ("missing", "no longer exists"),
        ("resource", "occupied by"),
        ("type", "declares type Package"),
    ],
)
def test_apply_refuses_live_update_drift_before_writes(tmp_path: Path, drift: str, message: str) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    with open_reader(graph_dir=graph_dir) as reader:
        first = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
        apply_mirror(bundle_root, first, today=_TODAY)

    target = bundle_root / "code-graph/acme/file-system/a.py.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace("language: python", "language: rust"),
        encoding="utf-8",
    )
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_mirror(load_bundle(bundle_root), reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    assert tuple(plan.updates) == ("code-graph/acme/file-system/a.py",)

    if drift == "misplaced":
        wrong = bundle_root / "misplaced/a.py.md"
        wrong.parent.mkdir(parents=True)
        target.rename(wrong)
    elif drift == "missing":
        target.unlink()
    elif drift == "resource":
        target.write_text(
            target.read_text(encoding="utf-8").replace("file:local/acme/a.py", "file:local/acme/other.py"),
            encoding="utf-8",
        )
    else:
        target.write_text(target.read_text(encoding="utf-8").replace("type: File", "type: Package"), encoding="utf-8")
    before = _file_bytes(bundle_root)

    with pytest.raises(PlacementError, match=message):
        apply_mirror(bundle_root, plan, today=_TODAY)

    assert _file_bytes(bundle_root) == before


def test_apply_refuses_filesystem_equivalent_targets_before_writes(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    targets = (
        MirrorTarget(
            resource="file:local/acme/Widget.py",
            source_path="Widget.py",
            member="code-graph/acme/file-system/Widget.py.md",
        ),
        MirrorTarget(
            resource="file:local/acme/widget.py",
            source_path="widget.py",
            member="code-graph/acme/file-system/widget.py.md",
        ),
    )
    plan = MirrorPlan(
        repo="acme",
        targets=targets,
        moves=_empty_move_plan(bundle_root),
        creates={},
        updates={},
        deletions=(),
        declined_deletions=(),
    )
    before = _file_bytes(bundle_root)

    with pytest.raises(PlacementError, match="filesystem-equivalent"):
        apply_mirror(bundle_root, plan, today=_TODAY)

    assert _file_bytes(bundle_root) == before


def test_apply_refuses_case_equivalent_ancestor_file_before_any_write(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    (repo_root / "b.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "add b")
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_mirror(
            load_bundle(bundle_root),
            reader,
            repo,
            tracked=("a.py", "b.py"),
            sha=_head(repo_root),
            at=_AT,
        )

    occupied = bundle_root / "code-graph" / "acme" / "FILE-SYSTEM"
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"pre-existing ancestor file\n")
    before = _file_bytes(bundle_root)

    with pytest.raises(PlacementError, match="filesystem-equivalent"):
        apply_mirror(bundle_root, plan, today=_TODAY)

    assert _file_bytes(bundle_root) == before
    assert all(not (bundle_root / target.member).exists() for target in plan.targets)


def test_apply_refuses_directory_at_target_member_before_any_write(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    target = MirrorTarget(
        resource="file:local/acme/a.py",
        source_path="a.py",
        member="code-graph/acme/file-system/a.py.md",
    )
    plan = MirrorPlan(
        repo="acme",
        targets=(target,),
        moves=_empty_move_plan(bundle_root),
        creates={"a.py": ({"type": "File"}, Render())},
        updates={},
        deletions=(),
        declined_deletions=(),
    )
    occupied = bundle_root / target.member
    occupied.mkdir(parents=True)
    before = _file_bytes(bundle_root)

    with pytest.raises(PlacementError, match="path type conflict"):
        apply_mirror(bundle_root, plan, today=_TODAY)

    assert _file_bytes(bundle_root) == before


def test_apply_refuses_directory_at_derived_index_before_any_write(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    install_bundle(bundle_root, today=_TODAY, dry_run=False)
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    with open_reader(graph_dir=graph_dir) as reader:
        plan = plan_mirror(
            load_bundle(bundle_root),
            reader,
            repo,
            tracked=("a.py",),
            sha=_head(repo_root),
            at=_AT,
        )

    occupied = bundle_root / "code-graph" / "acme" / "file-system" / "index.md"
    occupied.mkdir(parents=True)
    before = _file_bytes(bundle_root)

    with pytest.raises(PlacementError, match="path type conflict"):
        apply_mirror(bundle_root, plan, today=_TODAY)

    assert _file_bytes(bundle_root) == before
    assert all(not (bundle_root / target.member).exists() for target in plan.targets)
