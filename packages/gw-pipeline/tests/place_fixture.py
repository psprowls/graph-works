"""Real repositories and filed work items for the placement engine suites."""

from datetime import date

import pytest
from gw_kernel.workspace.provenance import GitFailure, gate_git, probe_git
from gw_pipeline.placement import NullNotifier, placement_chain
from okf_ext.shape import load_sections
from okf_io import load_bundle
from work_tracker_okf.filing import FilingSeed, apply, plan_filing
from work_tracker_okf.items import load_items

TODAY = date(2026, 10, 22)


class PlaceEnv:
    def __init__(self, layout, repo):
        self.layout = layout
        self.repo = repo

    def git(self, *args, cwd=None):
        result = probe_git(self.repo if cwd is None else cwd, *args)
        assert result.returncode == 0, result
        return result.stdout.strip()

    def ctx(self, **overrides):
        from gw_pipeline.place import PlaceContext

        git = gate_git(self.layout)
        assert not isinstance(git, GitFailure)
        return PlaceContext(
            **{"layout": self.layout, "git": git, "notifier": NullNotifier(), "today": TODAY, **overrides}
        )

    def file(self, name="leaf", *, kind="Feature", parent=None):
        items = load_items(load_bundle(self.layout.bundle_dir))
        seed = FilingSeed(kind, name, "Placement test", TODAY, name=name, parent_path=parent)
        plan = plan_filing(self.layout.bundle_dir, items, seed, load_sections(self.layout.config_dir / "sections"))
        assert plan.refusal is None, plan
        apply(plan)
        return plan.path

    def chain_for(self, path, *, default_base="main"):
        items = load_items(load_bundle(self.layout.bundle_dir)).by_path
        return placement_chain(
            items[path],
            items,
            repo_name="code",
            repo_root=self.repo,
            working_checkout=self.repo,
            worktrees_dir=self.layout.worktrees_dir,
            default_base=default_base,
        )


@pytest.fixture
def place_env(tmp_path, git_repo, code_layout):
    env = PlaceEnv(code_layout, tmp_path / "code")
    env.git("config", "user.email", "place@example.test")
    env.git("config", "user.name", "Placement Tests")
    env.git("checkout", "-B", "main")
    env.git("commit", "-m", "Initial code")
    return env
