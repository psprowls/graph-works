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


if __name__ == "__main__":
    unittest.main()
