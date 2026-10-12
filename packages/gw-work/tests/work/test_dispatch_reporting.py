"""The two dispatch consumers resolve the same leaf snapshot."""

import subprocess
from pathlib import Path

from gw_work.commands import run_next
from test_run_next import _layout, _spec, _write


def test_terminal_has_no_profile_even_without_dispatch_config(tmp_path):
    layout = _layout(tmp_path)
    _write(layout, "work/feature-a", phase="done")
    (layout.root / "dispatch.yaml").unlink()
    result = run_next(layout, "work/feature-a")
    assert result.dispatch_resolution is None
    assert result.dispatch_preflight is None


def test_failed_normalization_resolves_persisted_route(tmp_path, monkeypatch):
    from gw_work import commands as work

    layout = _layout(tmp_path)
    path = "work/feature-a"
    _write(layout, path)
    _spec(layout, path)
    monkeypatch.setattr(work, "_apply_normalizations", lambda *args, **kwargs: (work.NextApplication(), ("failed",)))
    result = run_next(layout, path, dry_run=False)
    assert result.route.dispatch.stage == "design"
    assert result.dispatch_resolution.profile.skill == "superpowers:brainstorming"


def _init_git_repo(layout) -> Path:
    """Replace the workspace fixture's marker with a real tracked repository."""
    repo = layout.root.parent
    (repo / ".git").rmdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "T"),
        ("add", "."),
        ("commit", "-m", "fixture"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return repo
