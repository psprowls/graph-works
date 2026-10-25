"""The finish -> done advance verifies every item that has a computed finish target."""

from __future__ import annotations

from datetime import date

import pytest
from gw_kernel.workspace.provenance import probe_git
from gw_orchestrate import placing
from gw_orchestrate.finish_receipt import run_record_finish
from gw_orchestrate.stage_advance import run_stage_advance
from gw_pipeline.placement import NullNotifier
from okf_ext.shape import load_sections
from okf_io import load_bundle, parse
from work_tracker_okf.filing import FilingSeed, apply, plan_filing
from work_tracker_okf.items import load_items

TODAY = date(2026, 10, 22)


class Env:
    def __init__(self, layout, repo):
        self.layout = layout
        self.repo = repo

    def git(self, *args, cwd=None):
        result = probe_git(self.repo if cwd is None else cwd, *args)
        assert result.returncode == 0, result
        return result.stdout.strip()

    def leaf(self, affects):
        items = load_items(load_bundle(self.layout.bundle_dir))
        seed = FilingSeed("Feature", "leaf", "Finish test", TODAY, name="leaf", parent_path=None)
        plan = plan_filing(self.layout.bundle_dir, items, seed, load_sections(self.layout.config_dir / "sections"))
        assert plan.refusal is None, plan
        apply(plan)
        page = self.layout.bundle_dir / f"{plan.path}.md"
        document = parse(page.read_text(encoding="utf-8"), path=page)
        document.set("phase", "finish")
        document.set("work_status", "in-progress")
        document.set("affects", list(affects))
        page.write_text(document.serialize(), encoding="utf-8", newline="\n")
        return plan.path

    def place(self, path):
        result = placing.realize(
            self.layout,
            path,
            phase="design",
            verify_only=False,
            notifier=NullNotifier(),
            occupied=lambda p: None,
            today=TODAY,
        )
        assert isinstance(result, placing.Realized), result
        return result.placement

    def phase(self, path):
        return load_items(load_bundle(self.layout.bundle_dir)).by_path[path].phase


@pytest.fixture
def env(tmp_path, git_repo, code_layout):
    e = Env(code_layout, tmp_path / "code")
    e.git("config", "user.email", "finish@example.test")
    e.git("config", "user.name", "Finish Tests")
    e.git("checkout", "-B", "main")
    e.git("commit", "-m", "Initial code")
    return e


def _advance(env, path, sha, **kw):
    return run_stage_advance(env.layout, path, today=TODAY, resolved_in=sha, dry_run=False, **kw)


def test_unstamped_code_item_without_receipt_is_refused(env):
    path = env.leaf(["src/a.py"])
    env.place(path)
    result = _advance(env, path, env.git("rev-parse", "HEAD"))
    assert result.outcome.plan.refusal == "finish-incomplete"
    assert "no receipt entry" in result.outcome.plan.detail
    assert env.phase(path) == "finish"


def test_unstamped_code_item_with_receipt_resolves_to_receipt(env):
    path = env.leaf(["src/a.py"])
    placement = env.place(path)
    env.git("merge", "--no-ff", "-m", "merge leaf", placement.branch)
    recorded = run_record_finish(env.layout, path, repo_name="code", today=TODAY)
    assert recorded.refusal is None, recorded
    sha = env.git("rev-parse", "HEAD")
    result = _advance(env, path, None)
    assert result.outcome.written, result
    assert env.phase(path) == "done"
    item = load_items(load_bundle(env.layout.bundle_dir)).by_path[path]
    assert item.resolved_in == sha


def test_workspace_only_item_resolves_on_flag(env):
    path = env.leaf(["gw:workspace"])
    result = _advance(env, path, "abc1234")
    assert result.outcome.written, result
    assert env.phase(path) == "done"
    assert load_items(load_bundle(env.layout.bundle_dir)).by_path[path].resolved_in == "abc1234"
