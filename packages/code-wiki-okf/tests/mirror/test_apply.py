import subprocess
from datetime import UTC, datetime
from pathlib import Path

from code_graph_io.handle import open_reader
from code_graph_io.update import run_workspace
from code_wiki_okf.config import RepoConfig
from code_wiki_okf.mirror.apply import apply_mirror
from code_wiki_okf.mirror.plan import plan_mirror
from okf_ext.shape import load_sections
from okf_io import load_bundle

_AT = datetime(2026, 1, 1, tzinfo=UTC)
_SECTIONS_DIR = Path(__file__).parents[2] / "src" / "code_wiki_okf" / "assets" / "_sections"
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


def test_apply_creates_and_fills_generated_sections_in_one_pass(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    run_workspace([repo_root], graph_dir=tmp_path / "graph", full=True)
    reader = open_reader(graph_dir=tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    section_set = load_sections(_SECTIONS_DIR)

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    result = apply_mirror(bundle, plan, repo, section_set=section_set)

    assert result.created == ("a.py",)
    target = bundle_root / "repositories" / "acme" / "a.py.md"
    assert target.exists()
    assert "not yet generated" not in target.read_text(encoding="utf-8")
    reader.close()


def test_apply_second_run_is_idempotent(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    run_workspace([repo_root], graph_dir=tmp_path / "graph", full=True)
    reader = open_reader(graph_dir=tmp_path / "graph")
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    section_set = load_sections(_SECTIONS_DIR)

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle, plan, repo, section_set=section_set)

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
    bundle_root.mkdir()
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    section_set = load_sections(_SECTIONS_DIR)

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle, plan, repo, section_set=section_set)
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
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")

    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    section_set = load_sections(_SECTIONS_DIR)

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py", "b.py"), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle, plan, repo, section_set=section_set)
    reader.close()

    a_target = bundle_root / "repositories" / "acme" / "a.py.md"
    index_target = bundle_root / "repositories" / "acme" / "index.md"
    assert a_target.exists()
    assert "a.py.md" in index_target.read_text(encoding="utf-8")

    # `a.py`'s Notes section is untouched (still the placeholder), so the
    # deletion guard admits it -- unlike test_apply_declined_deletion, this
    # is the deletion actually going through.
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader2 = open_reader(graph_dir=graph_dir)
    bundle2 = load_bundle(bundle_root)
    plan2 = plan_mirror(bundle2, reader2, repo, tracked=("b.py",), sha=_head(repo_root), at=_AT)
    assert plan2.deletions == ("a.py",)
    assert plan2.declined_deletions == ()

    result2 = apply_mirror(bundle2, plan2, repo, section_set=section_set)
    reader2.close()

    assert result2.deleted == ("a.py",)
    assert not a_target.exists()
    index_text = index_target.read_text(encoding="utf-8")
    assert "a.py.md" not in index_text
    assert "b.py.md" in index_text


def test_apply_scopes_index_update_to_this_repos_mirror(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
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
    section_set = load_sections(_SECTIONS_DIR)

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle, plan, repo, section_set=section_set)

    assert unrelated.read_text(encoding="utf-8") == before
    reader.close()


def test_apply_rename_preserves_notes_and_removes_old_path(tmp_path: Path) -> None:
    repo_root = _scratch_repo(tmp_path)
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    section_set = load_sections(_SECTIONS_DIR)

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py",), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle, plan, repo, section_set=section_set)
    reader.close()

    old_target = bundle_root / "repositories" / "acme" / "a.py.md"
    custom_notes = "A human wrote this specific detail about a.py."
    _set_notes(old_target, custom_notes)

    _git(repo_root, "mv", "a.py", "renamed.py")
    _git(repo_root, "commit", "-q", "-m", "rename a.py")
    sha_after = _head(repo_root)

    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    bundle2 = load_bundle(bundle_root)
    plan2 = plan_mirror(bundle2, reader, repo, tracked=("renamed.py",), sha=sha_after, at=_AT)
    result2 = apply_mirror(bundle2, plan2, repo, section_set=section_set)

    new_target = bundle_root / "repositories" / "acme" / "renamed.py.md"
    assert new_target.exists()
    assert _notes_body(new_target.read_text(encoding="utf-8")) == custom_notes
    assert not old_target.exists()
    assert result2.moved == (("repositories/acme/a.py.md", "repositories/acme/renamed.py.md"),)
    reader.close()


def test_apply_declined_deletion_leaves_file_and_is_reported(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "a.py").write_text("VALUE = 1\n")
    (repo_root / "b.py").write_text("VALUE = 2\n")
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@t")
    _git(repo_root, "config", "user.name", "t")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")

    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    graph_dir = tmp_path / "graph"
    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    repo = RepoConfig(name="acme", path=repo_root, ignore=())
    section_set = load_sections(_SECTIONS_DIR)

    bundle = load_bundle(bundle_root)
    plan = plan_mirror(bundle, reader, repo, tracked=("a.py", "b.py"), sha=_head(repo_root), at=_AT)
    apply_mirror(bundle, plan, repo, section_set=section_set)
    reader.close()

    a_target = bundle_root / "repositories" / "acme" / "a.py.md"
    _set_notes(a_target, "A human wrote this and does not want a.py's page deleted.")

    run_workspace([repo_root], graph_dir=graph_dir, full=True)
    reader = open_reader(graph_dir=graph_dir)
    bundle2 = load_bundle(bundle_root)
    plan2 = plan_mirror(bundle2, reader, repo, tracked=("b.py",), sha=_head(repo_root), at=_AT)
    result2 = apply_mirror(bundle2, plan2, repo, section_set=section_set)

    assert a_target.exists()
    assert len(result2.declined_deletions) == 1
    assert result2.declined_deletions[0].path == "repositories/acme/a.py.md"
    reader.close()
