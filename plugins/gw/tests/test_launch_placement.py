#!/usr/bin/env python3
"""Placement never depends on where the auto-drive coordinator runs.

Every Orca answer here is synthetic, shaped like `orca repo list --json`,
`orca worktree show --json` and `orca worktree set --json` on 1.4.203. The
location matrix runs each scenario from three coordinator contexts (the code
repo's primary checkout, the separate wiki repository, the epic worktree) by
changing cwd and ORCA_TERMINAL_HANDLE; the calls and outcome must be
identical, and a caller-relative selector fails the test outright.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HELPER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "skills/auto-drive/references/launch-worker.py"))

CODE, WIKI = "/code/gw", "/code/gw-workspace"
CODE_ID, WIKI_ID = "repo-code", "repo-wiki"
EPIC, CHILD = "/wt/epic", "/wt/child"
EPIC_ID, CHILD_ID, MAIN_ID = f"{CODE_ID}::{EPIC}", f"{CODE_ID}::{CHILD}", f"{CODE_ID}::{CODE}"
KEY = "gw-execute-bug-child-0123abcd"
LOCATIONS = {"primary": CODE, "wiki": WIKI, "epic": EPIC}


def fork(parent_path=EPIC, action="fork-child"):
    return {
        "key": KEY,
        "worktree": {
            "action": action,
            "path": None,
            "branch": "bug/child",
            "base_branch": "psprowls/epic",
            "exists": None,
            "parent_path": parent_path,
        },
    }


class FakeOrca:
    def __init__(self, *, repos=None, epic_repo=CODE_ID, child_repo=CODE_ID, child_parent=None, set_fails=False):
        self.calls: list[list[str]] = []
        self.set_fails = set_fails
        self.repos = repos if repos is not None else [{"id": CODE_ID, "path": CODE}, {"id": WIKI_ID, "path": WIKI}]
        self.worktrees = {
            EPIC: {"id": EPIC_ID, "repoId": epic_repo, "path": EPIC, "isMainWorktree": False,
                   "parentWorktreeId": None, "branch": "refs/heads/psprowls/epic", "displayName": "epic"},
            CODE: {"id": MAIN_ID, "repoId": CODE_ID, "path": CODE, "isMainWorktree": True,
                   "parentWorktreeId": None, "branch": "refs/heads/main", "displayName": "gw"},
            CHILD: {"id": CHILD_ID, "repoId": child_repo, "path": CHILD, "isMainWorktree": False,
                    "parentWorktreeId": child_parent, "branch": "refs/heads/psprowls/child",
                    "displayName": "bug/child"},
        }

    def select(self, selector):
        kind, _, value = selector.partition(":")
        if kind == "path":
            return self.worktrees.get(value)
        if kind == "id":
            return next((row for row in self.worktrees.values() if row["id"] == value), None)
        raise AssertionError(f"placement used a caller-relative selector: {selector!r}")

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv[1:])
        assert argv[-1] == "--json", argv
        body = argv[1:-1]
        if body[:2] == ["repo", "list"]:
            return self.ok({"repos": self.repos})
        if body[:2] == ["worktree", "show"]:
            row = self.select(body[body.index("--worktree") + 1])
            return self.ok({"worktree": dict(row)}) if row else self.error()
        if body[:2] == ["worktree", "set"]:
            row = self.select(body[body.index("--worktree") + 1])
            parent = self.select(body[body.index("--parent-worktree") + 1])
            if self.set_fails or row is None or parent is None or parent["repoId"] != row["repoId"]:
                return self.error()
            row["parentWorktreeId"] = parent["id"]
            return self.ok({"worktree": dict(row)})
        if body[:2] == ["orchestration", "worker-show"]:
            return self.ok({"worker": {"worktreeId": CHILD_ID}})
        raise AssertionError(f"unexpected orca call {argv!r}")

    @staticmethod
    def ok(result):
        return subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "result": result}), "")

    @staticmethod
    def error():
        return subprocess.CompletedProcess([], 1, json.dumps({"ok": False, "error": {"code": "not_found"}}), "no")


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def write(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8", newline="")
        return str(path)

    def run_helper(self, command, fake, **fields):
        output = io.StringIO()
        error = io.StringIO()
        with (
            patch("subprocess.run", side_effect=fake),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(error),
        ):
            HELPER[command](argparse.Namespace(orca="orca", **fields))
        self.last_stderr = error.getvalue()
        return json.loads(output.getvalue())

    def place(self, fake, dispatch, repo_path=CODE):
        return self.run_helper(
            "place", fake, dispatch=self.write("dispatch.json", dispatch), repo_path=repo_path,
            out_placement=str(self.root / "placement.json"),
        )

    def settle(self, fake, dispatch, placed, start=None):
        return self.run_helper(
            "settle_placement", fake, dispatch=self.write("dispatch.json", dispatch),
            placement_result=self.write("placed.json", placed),
            start=self.write("start.json", start or {"ok": True, "result": {"dispatchId": "ctx_1"}}),
        )

    def refused(self, command, *args, **kwargs):
        with self.assertRaises(SystemExit) as caught:
            getattr(self, command)(*args, **kwargs)
        return str(caught.exception.code)

    def test_a_fork_launches_top_level_in_the_code_repo_from_every_coordinator_location(self):
        seen = []
        for location, cwd in LOCATIONS.items():
            with self.subTest(location=location), tempfile.TemporaryDirectory() as elsewhere:
                fake = FakeOrca()
                previous = os.getcwd()
                os.chdir(elsewhere)
                try:
                    # `os.chdir`s to a neutral temp dir every arm, so without
                    # this patch `os.getcwd()` is constant across arms and a
                    # regression reading it would fail as a refusal (the
                    # selector match breaks) rather than as the divergence
                    # this matrix claims to prove.
                    with (
                        patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": f"term-{location}", "PWD": cwd}),
                        patch("os.getcwd", return_value=cwd),
                    ):
                        placed = self.place(fake, fork())
                finally:
                    os.chdir(previous)
                seen.append((placed, fake.calls))
        placed, calls = seen[0]
        self.assertEqual(placed["placement_argv"], [
            "--worktree", "new-top-level", "--name", "bug/child", "--base-branch", "psprowls/epic",
            "--repo", f"id:{CODE_ID}",
        ])
        self.assertEqual(placed["repo_id"], CODE_ID)
        self.assertEqual(placed["parent_worktree_id"], EPIC_ID)
        self.assertTrue(all(entry == seen[0] for entry in seen))
        self.assertFalse(any("new-child" in call for call in calls))
        self.assertEqual(json.loads((self.root / "placement.json").read_text(encoding="utf-8")),
                         placed["placement_argv"])

    def test_a_top_level_creation_names_the_repo_and_no_parent(self):
        placed = self.place(FakeOrca(), fork(parent_path=None, action="create-top-level"))
        self.assertEqual(placed["placement_argv"][-2:], ["--repo", f"id:{CODE_ID}"])
        self.assertIsNone(placed["parent_worktree_id"])

    def test_reuse_and_main_place_by_path_with_no_orca_call(self):
        for action in ("reuse", "main"):
            with self.subTest(action=action):
                fake = FakeOrca()
                dispatch = {"key": KEY, "worktree": {"action": action, "path": EPIC, "branch": "b",
                                                     "base_branch": None, "exists": True, "parent_path": None}}
                placed = self.place(fake, dispatch, repo_path=None)
                self.assertEqual(placed["placement_argv"], ["--worktree", f"path:{EPIC}"])
                self.assertEqual(fake.calls, [])

    def test_ui_dispatch_uses_ui_repository_and_refuses_root_override(self):
        dispatch = fork(parent_path=None, action="create-top-level")
        dispatch["repo"] = {"name": "ui", "path": WIKI, "source": "frontmatter"}
        placed = self.place(FakeOrca(), dispatch, repo_path=WIKI)
        self.assertEqual(placed["placement_argv"][-2:], ["--repo", f"id:{WIKI_ID}"])
        self.assertIn("differs from dispatch", self.refused("place", FakeOrca(), dispatch, repo_path=CODE))

    def test_a_creation_with_no_code_repo_refuses(self):
        message = self.refused("place", FakeOrca(), fork(), repo_path=None)
        self.assertIn(f"PLACEMENT REFUSED {KEY}", message)

    def test_zero_or_several_matching_orca_repos_refuse(self):
        for repos in ([{"id": WIKI_ID, "path": WIKI}],
                      [{"id": CODE_ID, "path": CODE}, {"id": "repo-dup", "path": CODE + "/"}]):
            with self.subTest(count=len([r for r in repos if r["path"].rstrip("/") == CODE])):
                self.assertIn("PLACEMENT REFUSED", self.refused("place", FakeOrca(repos=repos), fork()))

    def test_a_parent_in_another_repository_refuses_before_any_start(self):
        fake = FakeOrca(epic_repo=WIKI_ID)
        self.assertIn("PLACEMENT REFUSED", self.refused("place", fake, fork()))
        self.assertTrue(all(call[0] in ("repo", "worktree") for call in fake.calls))

    def test_a_main_checkout_or_missing_parent_refuses(self):
        # Renamed in spirit only: an Orca-unknown parent, or a parent that is
        # the repository's own checkout, is no longer a hard refusal (fix 2).
        # Lineage is presentational; the repository -- resolved independently
        # above -- is what's load-bearing, so these degrade to a parentless
        # top-level creation with a note on stderr rather than blocking.
        for parent in (CODE, "/wt/gone"):
            with self.subTest(parent=parent):
                fake = FakeOrca()
                placed = self.place(fake, fork(parent_path=parent))
                self.assertEqual(placed["placement_argv"], [
                    "--worktree", "new-top-level", "--name", "bug/child", "--base-branch", "psprowls/epic",
                    "--repo", f"id:{CODE_ID}",
                ])
                self.assertIsNone(placed["parent_worktree_id"])
                self.assertIn(parent, self.last_stderr)

    def test_a_degraded_placement_settles_cleanly_with_no_lineage(self):
        fake = FakeOrca()
        placed = self.place(fake, fork(parent_path=CODE))
        self.assertIsNone(placed["parent_worktree_id"])
        observed = self.settle(fake, fork(parent_path=CODE), placed)
        self.assertFalse(observed["lineage_set"])
        self.assertIsNone(observed["parent_worktree_id"])
        self.assertFalse(any(call[:2] == ["worktree", "set"] for call in fake.calls))

    def test_settle_links_a_parentless_child_exactly_once_and_is_idempotent(self):
        fake = FakeOrca()
        placed = self.place(fake, fork())
        observed = self.settle(fake, fork(), placed)
        self.assertTrue(observed["lineage_set"])
        self.assertEqual(observed["parent_worktree_id"], EPIC_ID)
        self.assertEqual(observed["branch"], "psprowls/child")
        sets = [call for call in fake.calls if call[:2] == ["worktree", "set"]]
        self.assertEqual(sets, [["worktree", "set", "--worktree", f"id:{CHILD_ID}",
                                 "--parent-worktree", f"id:{EPIC_ID}", "--json"]])
        again = self.settle(fake, fork(), placed)
        self.assertFalse(again["lineage_set"])
        self.assertEqual(len([call for call in fake.calls if call[:2] == ["worktree", "set"]]), 1)

    def test_settle_reads_the_worktree_from_start_effects_without_worker_show(self):
        fake = FakeOrca(child_parent=EPIC_ID)
        placed = self.place(fake, fork())
        start = {"ok": True, "result": {"dispatchId": "ctx_1",
                                        "effects": [{"kind": "worktree", "action": "created", "id": CHILD_ID}]}}
        self.settle(fake, fork(), placed, start=start)
        self.assertFalse(any(call[:2] == ["orchestration", "worker-show"] for call in fake.calls))

    def test_settle_reports_a_wrong_repository_or_parent(self):
        cases = {
            "repository": FakeOrca(child_repo=WIKI_ID),
            "parent": FakeOrca(child_parent=MAIN_ID),
            "unlinkable": FakeOrca(set_fails=True),
        }
        for name, fake in cases.items():
            with self.subTest(case=name):
                placed = {"action": "fork-child", "placement_argv": [], "repo_id": CODE_ID,
                          "parent_worktree_id": EPIC_ID}
                self.assertIn(f"PLACEMENT MISMATCH {KEY}", self.refused("settle", fake, fork(), placed))

    def test_settle_requires_no_parent_when_none_was_planned(self):
        fake = FakeOrca(child_parent=EPIC_ID)
        placed = {"action": "create-top-level", "placement_argv": [], "repo_id": CODE_ID,
                  "parent_worktree_id": None}
        message = self.refused("settle", fake, fork(parent_path=None, action="create-top-level"), placed)
        self.assertIn("PLACEMENT MISMATCH", message)
        self.assertFalse(any(call[:2] == ["worktree", "set"] for call in fake.calls))

    def test_settle_for_reuse_compares_paths_only(self):
        fake = FakeOrca()
        dispatch = {"key": KEY, "worktree": {"action": "reuse", "path": CHILD, "branch": "b",
                                             "base_branch": None, "exists": True, "parent_path": None}}
        placed = {"action": "reuse", "placement_argv": ["--worktree", f"path:{CHILD}"], "repo_id": None,
                  "parent_worktree_id": None}
        self.assertEqual(self.settle(fake, dispatch, placed)["path"], CHILD)
        dispatch["worktree"]["path"] = EPIC
        self.assertIn("PLACEMENT MISMATCH", self.refused("settle", fake, dispatch, placed))


class PreparationTests(unittest.TestCase):
    setUp = PlacementTests.setUp
    tearDown = PlacementTests.tearDown
    write = PlacementTests.write
    run_helper = PlacementTests.run_helper

    def preparation(self):
        return {"owner_path": "work/epic", "owner_phase": "execute",
                "repo": {"name": "ui", "path": WIKI, "source": "frontmatter"},
                "branch": "epic/integration", "base_branch": "main",
                "worktree": {"action": "create-top-level", "path": None, "branch": "epic/integration",
                             "base_branch": "main", "parent_path": None, "exists": None}}

    def test_prepare_requires_a_fresh_selected_preparation_before_creation(self):
        plan = {"path": "work/epic", "live": [], "preparations": [self.preparation()]}
        def fake(argv, **kwargs):
            if "snapshot" in argv:
                return subprocess.CompletedProcess(argv, 0, '{"guard":"guard"}', "")
            if argv[:3] == ["gw", "work", "orchestrate"]:
                return subprocess.CompletedProcess(argv, 0, json.dumps({**plan, "preparations": []}), "")
            raise AssertionError(argv)
        with self.assertRaisesRegex(SystemExit, "preparation changed"):
            self.run_helper("prepare", fake, plan_file=self.write("plan.json", plan),
                            owner="work/epic", repo_name="ui", workspace=WIKI)

class PreparationLifecycleTests(unittest.TestCase):
    write = PlacementTests.write
    run_helper = PlacementTests.run_helper

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.repo = self.root / "ui"
        self.repo.mkdir()
        self.real_run = subprocess.run
        self.git(self.repo, "init", "-b", "main")
        self.git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "initial")
        self.prep = {"owner_path": "work/epic", "owner_phase": "execute",
                     "repo": {"name": "ui", "path": str(self.repo), "source": "frontmatter"},
                     "branch": "epic/integration", "base_branch": "main",
                     "worktree": {"action": "create-top-level", "path": None, "branch": "epic/integration",
                                  "base_branch": "main", "parent_path": None, "exists": None}}
        self.rows = []
        self.calls = []
        self.create_error = False
        self.record_error = False
        self.after_create = None
        self.prefix = True
        self.hide_comments = False

    def tearDown(self):
        self.directory.cleanup()

    def git(self, path, *args):
        result = self.real_run(["git", "-C", str(path), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def plan(self):
        return {"path": "work/epic", "live": ["live-key"], "preparations": [self.prep], "dispatches": []}

    def fake(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] == "git":
            return self.real_run(argv, **kwargs)
        if "snapshot" in argv:
            return subprocess.CompletedProcess(argv, 0, '{"guard":"guard"}', "")
        if "record" in argv:
            if self.record_error:
                return subprocess.CompletedProcess(argv, 1, "", "preparation changed")
            return subprocess.CompletedProcess(argv, 0, '{"written":true}', "")
        if argv[:3] == ["gw", "work", "orchestrate"]:
            self.assertEqual(argv[-2:], ["--live", "live-key"])
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.plan()), "")
        if argv[1:3] == ["repo", "list"]:
            return FakeOrca.ok({"repos": [{"id": "ui-id", "path": str(self.repo)}]})
        if argv[1:3] == ["worktree", "list"]:
            rows = [{k: v for k, v in row.items() if k != "comment"} for row in self.rows] if self.hide_comments else self.rows
            return FakeOrca.ok({"worktrees": rows, "totalCount": len(rows), "truncated": False,
                                "hostScope": {"hostIds": ["local"], "omittedHostIds": []}})
        if argv[1:3] == ["worktree", "show"]:
            path = argv[argv.index("--worktree") + 1].removeprefix("path:")
            return FakeOrca.ok({"worktree": next(row for row in self.rows if row["path"] == path)})
        if argv[1:3] == ["worktree", "create"]:
            self.assertIn("--no-parent", argv)
            self.assertEqual(argv[argv.index("--setup") + 1], "skip")
            branch = "configured/integration" if self.prefix else self.prep["branch"]
            path = self.root / "anchor"
            self.git(self.repo, "worktree", "add", "-b", branch, str(path), "main")
            row = {"path": str(path), "branch": "refs/heads/" + branch, "repoId": "ui-id",
                   "comment": argv[argv.index("--comment") + 1]}
            self.rows.append(row)
            if self.after_create:
                self.after_create(row)
            if self.create_error:
                return FakeOrca.error()
            return FakeOrca.ok({"worktree": row})
        raise AssertionError(argv)

    def prepare(self):
        return self.run_helper("prepare", self.fake, plan_file=self.write("plan.json", self.plan()),
                               owner="work/epic", repo_name="ui", workspace=WIKI)

    def test_create_renames_only_owned_checkout_and_records_owner_foreign_stamp(self):
        self.hide_comments = True
        result = self.prepare()
        self.assertEqual(result["branch"], "epic/integration")
        self.assertTrue(result["replan_required"])
        argv = next(call for call in self.calls if "record" in call)
        for flag, expected in (("--root", "work/epic"), ("--phase", "execute"), ("--repo", "ui")):
            self.assertEqual(argv[argv.index(flag) + 1], expected)
        self.assertEqual(self.git(self.root / "anchor", "branch", "--show-current"), "epic/integration")
        self.assertFalse(any("worker-start" in call or "task-create" in call for call in self.calls))

    def test_failed_stamp_then_restart_adopts_without_second_create(self):
        self.record_error = True
        with self.assertRaisesRegex(SystemExit, "preparation changed"):
            self.prepare()
        self.record_error = False
        self.prepare()
        self.assertEqual(sum(call[1:3] == ["worktree", "create"] for call in self.calls), 1)

    def test_create_error_reobserves_unique_owned_result(self):
        self.create_error = True
        self.prepare()
        self.assertEqual(sum(call[1:3] == ["worktree", "create"] for call in self.calls), 1)

    def test_second_creator_discovers_first_deterministic_checkout(self):
        self.prefix = False
        self.create_error = True
        self.prepare()
        self.prepare()
        self.assertEqual(sum(call[1:3] == ["worktree", "create"] for call in self.calls), 1)

    def test_phase_terminal_assignment_and_stamp_changes_never_record(self):
        for kind in ("phase", "terminal", "assignment", "stamp"):
            with self.subTest(kind=kind):
                # Separate filesystem per scenario, including after a refused create.
                self.tearDown()
                self.setUp()
                def change(row):
                    if kind == "phase":
                        self.prep["owner_phase"] = "finish"
                    elif kind == "terminal":
                        self.plan = lambda: {"path": "work/epic", "live": [], "preparations": []}
                    elif kind == "assignment":
                        self.prep["repo"]["source"] = "sole"
                    else:
                        self.record_error = True
                self.after_create = change
                with self.assertRaisesRegex(SystemExit, "preparation changed"):
                    self.prepare()
                self.assertFalse(any("worker-start" in call for call in self.calls))

    def test_crash_before_rename_recovers_marker_without_second_create(self):
        self.after_create = lambda row: Path(row["path"], "dirty").write_text("dirty", encoding="utf-8", newline="")
        with self.assertRaises(SystemExit):
            self.prepare()
        (self.root / "anchor" / "dirty").unlink()
        self.after_create = None
        self.prepare()
        self.assertEqual(sum(call[1:3] == ["worktree", "create"] for call in self.calls), 1)

    def test_wrong_returned_path_is_never_stamped(self):
        self.after_create = lambda row: row.update(path=str(self.repo))
        with self.assertRaisesRegex(SystemExit, "branch"):
            self.prepare()
        self.assertFalse(any("record" in call for call in self.calls))

    def test_ambiguous_markers_refuse_without_second_create(self):
        self.after_create = lambda row: self.rows.append(dict(row))
        with self.assertRaisesRegex(SystemExit, "ambiguous"):
            self.prepare()
        with self.assertRaisesRegex(SystemExit, "ambiguous"):
            self.prepare()
        self.assertEqual(sum(call[1:3] == ["worktree", "create"] for call in self.calls), 1)

    def test_competing_creator_wins_nonforce_rename_and_is_adopted(self):
        original = self.fake
        raced = False
        def race(argv, **kwargs):
            nonlocal raced
            if argv[0] == "git" and argv[3:5] == ["branch", "-m"] and not raced:
                raced = True
                self.git(self.repo, "worktree", "add", "-b", self.prep["branch"], str(self.root / "winner"), "main")
            return original(argv, **kwargs)
        self.fake = race
        result = self.prepare()
        self.assertTrue(raced)
        self.assertEqual(os.path.realpath(result["path"]), os.path.realpath(self.root / "winner"))
        self.assertEqual(self.git(self.root / "anchor", "branch", "--show-current"), "configured/integration")
        self.assertEqual(sum(call[1:3] == ["worktree", "create"] for call in self.calls), 1)

    def test_owned_checkout_with_work_beyond_base_is_never_renamed(self):
        def commit(row):
            self.git(row["path"], "-c", "user.name=Test", "-c", "user.email=test@example.com",
                     "commit", "--allow-empty", "-m", "unexpected work")
        self.after_create = commit
        with self.assertRaisesRegex(SystemExit, "base tip"):
            self.prepare()
        self.assertFalse(any("record" in call for call in self.calls))

    def test_incomplete_orca_inventory_never_creates(self):
        original = self.fake
        def incomplete(argv, **kwargs):
            if argv[1:3] == ["worktree", "list"]:
                return FakeOrca.ok({"worktrees": [], "totalCount": 1, "truncated": True,
                                    "hostScope": {"omittedHostIds": []}})
            return original(argv, **kwargs)
        self.fake = incomplete
        with self.assertRaisesRegex(SystemExit, "incomplete"):
            self.prepare()
        self.assertFalse(any("create" in call or "record" in call for call in self.calls))

    def test_wrong_observed_repository_refuses(self):
        self.after_create = lambda row: row.update(repoId="foreign")
        with self.assertRaisesRegex(SystemExit, "repository"):
            self.prepare()
        self.assertFalse(any("record" in call for call in self.calls))

    def test_ambiguous_deterministic_worktrees_refuse(self):
        branch = self.prep["branch"]
        self.git(self.repo, "worktree", "add", "-b", branch, str(self.root / "one"), "main")
        self.git(self.repo, "worktree", "add", "--force", str(self.root / "two"), branch)
        with self.assertRaisesRegex(SystemExit, "ambiguous"):
            self.prepare()
        self.assertFalse(any("record" in call or "create" in call for call in self.calls))

    def test_branch_without_checkout_refuses(self):
        self.git(self.repo, "branch", self.prep["branch"])
        with self.assertRaisesRegex(SystemExit, "without a provable checkout"):
            self.prepare()

    def test_dirty_owned_create_refuses_rename_and_record(self):
        self.after_create = lambda row: Path(row["path"], "dirty").write_text("dirty", encoding="utf-8", newline="")
        with self.assertRaisesRegex(SystemExit, "dirty"):
            self.prepare()
        self.assertEqual(self.git(self.root / "anchor", "branch", "--show-current"), "configured/integration")
        self.assertFalse(any("record" in call for call in self.calls))


if __name__ == "__main__":
    unittest.main()
