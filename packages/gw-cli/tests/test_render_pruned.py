"""The work archive's prune section renders one line per worktree, branch and kept row."""

from gw_cli.work_cli import rendering


def test_render_pruned_lines(capsys) -> None:
    rendering.render_pruned(None)
    assert capsys.readouterr().out == ""
    rendering.render_pruned(
        {
            "removed_worktrees": ["/wt/a"],
            "deleted_branches": ["feature/a"],
            "kept": [{"path": "work/a", "repo": "code", "branch": "feature/b", "why": "dirty"}],
        }
    )
    assert capsys.readouterr().out.splitlines() == [
        "pruned worktree /wt/a",
        "pruned branch feature/a",
        "kept feature/b (code, work/a): dirty",
    ]
