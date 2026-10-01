from __future__ import annotations

from pathlib import Path

import pytest
from gitrepo import Upstream, git, make_upstream
from graph_works_core.repositories import commands
from graph_works_core.repositories.commands import run_repo_add
from graph_works_core.workspace.layout import WorkspaceLayout
from okf_io import load, parse
from repositories_okf.pin import read_pin
from workspace_fixture import NOW, bundle_bytes, make_workspace, porcelain


@pytest.fixture
def layout(tmp_path: Path) -> WorkspaceLayout:
    return make_workspace(tmp_path)


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    (tmp_path / "up").mkdir()
    up = make_upstream(tmp_path / "up")
    up.commit({"README.md": "# demo\n", "src/a.py": "a = 1\n"}, "c1", tag="v1.0.0")
    return up


def test_add_clones_pins_writes_and_commits(layout: WorkspaceLayout, upstream: Upstream) -> None:
    head = git(upstream.work, "rev-parse", "HEAD")
    result = run_repo_add(layout, upstream.url, name="demo", now=NOW, nonce="t1")
    assert result.ok, result.refusal
    assert (result.name, result.track, result.ref, result.commit, result.describe) == (
        "demo",
        "main",
        "main",
        head,
        "v1.0.0",
    )
    clone = layout.bundle_dir / "repositories" / "demo" / "references" / "git"
    assert git(clone, "rev-parse", "HEAD") == head
    page = load(layout.bundle_dir / "repositories" / "demo.md")
    pin = read_pin(page)
    assert pin is not None and (pin.commit, pin.ref, pin.describe, pin.previous) == (head, "main", "v1.0.0", None)
    assert page.fm_data(dates="iso")["track"] == "main"
    snapshot = layout.bundle_dir / "repositories" / "demo" / "snapshots" / f"2026-09-29-{head[:7]}.md"
    assert parse(snapshot.read_text(encoding="utf-8")).fm_data(dates="iso")["description"] == "baseline"
    changelog = (layout.bundle_dir / "repositories" / "demo" / "changelog.md").read_text(encoding="utf-8")
    assert f"(/repositories/demo/snapshots/2026-09-29-{head[:7]}.md) — baseline" in changelog
    assert "demo.md" in (layout.bundle_dir / "repositories" / "index.md").read_text(encoding="utf-8")
    assert "**repo-add** demo — " in (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
    assert result.commit_outcome is not None and result.commit_outcome.status == "committed"
    assert git(layout.root, "log", "-1", "--format=%s") == "workspace: add reference repository demo"
    assert porcelain(layout) == ""  # everything committed, and the clone is ignored
    committed = git(layout.root, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert not any("/references/git/" in path for path in committed)
    assert not (layout.cache_dir / "repo-incoming" / "demo-t1").exists()


def test_the_name_defaults_to_the_url_stem(layout: WorkspaceLayout, upstream: Upstream) -> None:
    result = run_repo_add(layout, upstream.url, now=NOW)
    assert result.ok and result.name == "upstream"


def test_an_annotated_tag_ref_pins_the_peeled_commit(layout: WorkspaceLayout, upstream: Upstream) -> None:
    tagged = git(upstream.work, "rev-parse", "HEAD")
    upstream.commit({"src/a.py": "a = 2\n"}, "c2")
    result = run_repo_add(layout, upstream.url, name="demo", ref="v1.0.0", now=NOW)
    assert result.ok and (result.commit, result.ref, result.track) == (tagged, "v1.0.0", "main")


def test_dry_run_resolves_the_commit_and_writes_nothing(layout: WorkspaceLayout, upstream: Upstream) -> None:
    before = bundle_bytes(layout)
    result = run_repo_add(layout, upstream.url, name="demo", now=NOW, dry_run=True)
    assert result.ok and result.dry_run and result.commit == git(upstream.work, "rev-parse", "HEAD")
    assert "repositories/demo.md" in result.paths and "log.md" in result.paths
    assert bundle_bytes(layout) == before
    assert porcelain(layout) == ""


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"url": "--upload-pack=evil"}, "invalid-url"),
        ({"url": ""}, "invalid-url"),
        ({"name": "Bad Name"}, "invalid-name"),
        ({"ref": "no-such-ref"}, "ref-not-found"),
        ({"ref": "0123456789abcdef0123456789abcdef01234567"}, "ref-not-found"),
    ],
)
def test_refusals_leave_nothing_behind(
    layout: WorkspaceLayout, upstream: Upstream, kwargs: dict[str, str], code: str
) -> None:
    before = bundle_bytes(layout)
    arguments = {"url": upstream.url, "name": "demo", **kwargs}
    result = run_repo_add(layout, arguments.pop("url"), now=NOW, **arguments)  # type: ignore[arg-type]
    assert result.refusal is not None and result.refusal.code == code
    assert bundle_bytes(layout) == before
    assert not (layout.bundle_dir / "repositories" / "demo").exists()


def test_an_unreachable_remote(layout: WorkspaceLayout, tmp_path: Path) -> None:
    result = run_repo_add(layout, (tmp_path / "nope.git").as_uri(), name="demo", now=NOW)
    assert result.refusal is not None and result.refusal.code == "remote-unreachable"


def test_name_taken_by_a_page_or_a_directory(layout: WorkspaceLayout, upstream: Upstream) -> None:
    assert run_repo_add(layout, upstream.url, name="demo", now=NOW).ok
    again = run_repo_add(layout, upstream.url, name="demo", now=NOW)
    assert again.refusal is not None and again.refusal.code == "name-taken"
    (layout.bundle_dir / "repositories" / "other").mkdir(parents=True)
    taken = run_repo_add(layout, upstream.url, name="other", now=NOW)
    assert taken.refusal is not None and taken.refusal.code == "name-taken"


def test_git_unavailable(layout: WorkspaceLayout, upstream: Upstream, tmp_path: Path) -> None:
    result = run_repo_add(
        layout, upstream.url, name="demo", now=NOW, environ={"GW_GIT": str(tmp_path / "no-git"), "PATH": ""}
    )
    assert result.refusal is not None and result.refusal.code == "git-unavailable"


def test_a_write_failure_rolls_everything_back_and_a_rerun_succeeds(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = bundle_bytes(layout)

    def boom(*_args: object, **_kwargs: object) -> str | None:
        raise OSError("disk full")

    monkeypatch.setattr(commands, "_append_log", boom)
    result = run_repo_add(layout, upstream.url, name="demo", now=NOW)
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert bundle_bytes(layout) == before
    assert not (layout.bundle_dir / "repositories" / "demo").exists()
    assert porcelain(layout) == ""
    monkeypatch.undo()
    assert run_repo_add(layout, upstream.url, name="demo", now=NOW).ok


def test_a_naive_now_is_a_caller_error(layout: WorkspaceLayout, upstream: Upstream) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        run_repo_add(layout, upstream.url, now=NOW.replace(tzinfo=None))


def test_a_competing_add_during_resolution_keeps_the_winners_files(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = commands.resolve_ref
    raced = False
    winner: dict[str, bytes] = {}

    def resolve(*args: object, **kwargs: object) -> object:
        nonlocal raced, winner
        if not raced:
            raced = True
            assert run_repo_add(layout, upstream.url, name="demo", now=NOW, nonce="winner").ok
            winner = bundle_bytes(layout)
        return original(*args, **kwargs)

    monkeypatch.setattr(commands, "resolve_ref", resolve)
    result = run_repo_add(layout, upstream.url, name="demo", now=NOW, nonce="loser")
    assert result.refusal is not None and result.refusal.code == "name-taken"
    assert bundle_bytes(layout) == winner
    assert (layout.bundle_dir / "repositories/demo/references/git/.git").is_dir()
    assert porcelain(layout) == ""


def test_add_publishes_across_filesystems(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    clone = layout.bundle_dir / "repositories/demo/references/git"
    original = Path.replace

    def replace(source: Path, target: Path) -> Path:
        if target == clone:
            raise OSError(errno.EXDEV, "cross-device link")
        return original(source, target)

    monkeypatch.setattr(Path, "replace", replace)
    result = run_repo_add(layout, upstream.url, name="demo", now=NOW, nonce="cross-device")
    assert result.ok, result.refusal
    assert git(clone, "rev-parse", "HEAD") == result.commit
    assert (clone / "src/a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert not (layout.cache_dir / "repo-incoming/demo-cross-device-ready").exists()
    assert porcelain(layout) == ""


def test_cross_filesystem_copy_failure_cleans_only_the_owned_add(
    layout: WorkspaceLayout, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    before = bundle_bytes(layout)
    clone = layout.bundle_dir / "repositories/demo/references/git"
    original = Path.replace

    def replace(source: Path, target: Path) -> Path:
        if target == clone:
            raise OSError(errno.EXDEV, "cross-device link")
        return original(source, target)

    def copytree(source, target, **kwargs):
        target.mkdir()
        (target / "partial").write_bytes(b"partial copy")
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(commands.shutil, "copytree", copytree)
    result = run_repo_add(layout, upstream.url, name="demo", now=NOW, nonce="copy-failed")
    assert result.refusal is not None and result.refusal.code == "write-failed"
    assert bundle_bytes(layout) == before
    assert not (layout.bundle_dir / "repositories/demo").exists()
    assert not (layout.cache_dir / "repo-incoming/demo-copy-failed-ready").exists()
