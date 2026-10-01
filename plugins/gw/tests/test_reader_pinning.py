#!/usr/bin/env python3
"""Real Git reader isolation; only the external Orca API is simulated."""
from __future__ import annotations

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


class ReaderPinning(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-b", "main")
        self.commit(self.repo, "A")
        self.epic = self.root / "epic"
        self.git(self.repo, "worktree", "add", "-b", "epic/x", str(self.epic), "main")
        self.sha = self.git(self.epic, "rev-parse", "HEAD")
        self.dispatch = {"key": "gw-design-child-1234", "repo": {"path": str(self.repo)},
                         "worktree": {"action": "pin-detached", "branch": None, "path": None,
                                      "start_sha": self.sha, "base_branch": "epic/x",
                                      "parent_path": str(self.epic)}}
        self.rows = []
        self.after_create = None
        self.return_path = None
        self.create_error = False
        self.hide_comments = False
        self.list_override = {}
        self.out = self.root / "placement.json"

    def git(self, path, *args):
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, path, content):
        (Path(path) / "file").write_text(content, encoding="utf-8", newline="")
        self.git(path, "add", "file")
        self.git(path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", content)

    def fake(self, orca, argv, *, label):
        if argv[:2] == ["repo", "list"]:
            return {"repos": [{"id": "repo-id", "path": str(self.repo)}]}
        if argv[:2] == ["worktree", "list"]:
            rows = [{k: v for k, v in row.items() if k != "comment"} for row in self.rows] if self.hide_comments else self.rows
            return {"worktrees": rows, "truncated": False, "totalCount": len(rows),
                    "hostScope": {"omittedHostIds": []}, **self.list_override}
        if argv[:2] == ["worktree", "show"]:
            selector = argv[argv.index("--worktree") + 1]
            return {"worktree": next(row for row in self.rows if selector in ("path:" + row["path"], "id:" + row["id"]))}
        if argv[:2] == ["worktree", "create"]:
            if self.create_error:
                raise SystemExit(label + ": create failed")
            self.assertIn("--no-parent", argv)
            self.assertEqual(argv[argv.index("--setup") + 1], "skip")
            self.assertNotIn("--agent", argv)
            name = argv[argv.index("--name") + 1]
            path = self.return_path or self.root / name
            if self.return_path is None:
                self.git(self.repo, "worktree", "add", "-b", name, str(path), argv[argv.index("--base-branch") + 1])
            row = {"id": "repo-id::" + str(path), "path": str(path), "repoId": "repo-id",
                   "comment": argv[argv.index("--comment") + 1], "isMainWorktree": False,
                   "parentWorktreeId": None, "branch": "refs/heads/" + name}
            self.rows.append(row)
            if self.after_create:
                self.after_create(row)
            return {"worktree": row}
        raise AssertionError(argv)

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8", newline="")
        return str(path)

    def run_helper(self, command, *options):
        args = HELPER["parser"]().parse_args([command, "--dispatch", self.write("dispatch.json", self.dispatch), *options])
        output = io.StringIO()
        with patch.dict(args.func.__globals__, {"orca_top_json": self.fake}), contextlib.redirect_stdout(output):
            args.func(args)
        return json.loads(output.getvalue())

    def prepare(self, attempt="attempt-1"):
        return self.run_helper("prepare-reader", "--attempt-id", attempt, "--out-placement", str(self.out))

    def refused(self, reason):
        with self.assertRaisesRegex(SystemExit, "PREPARATION REFUSED.*" + reason):
            self.prepare()
        self.assertFalse(self.out.exists())

    def detached(self, path, sha):
        self.assertEqual(self.git(path, "rev-parse", "HEAD"), sha)
        result = subprocess.run(["git", "-C", str(path), "symbolic-ref", "-q", "HEAD"], capture_output=True)
        self.assertEqual(result.returncode, 1)

    def test_a_reader_is_created_in_the_clone_that_owns_the_working_checkout(self):
        checkout = self.root / "checkout"
        self.git(self.repo, "worktree", "add", "-b", "track", str(checkout), "main")
        self.dispatch["repo"]["path"] = str(checkout)
        result = self.prepare()
        self.detached(result["path"], self.sha)

    def test_reader_stays_at_its_commit_while_the_epic_advances(self):
        result = self.prepare()
        self.assertEqual(result["attempt_id"], "attempt-1")
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8")), ["--worktree", "path:" + result["path"]])
        self.assertEqual(self.git(self.epic, "rev-parse", "HEAD"), self.sha)
        self.assertEqual(self.git(self.epic, "status", "--porcelain"), "")
        self.commit(self.epic, "B")
        b = self.git(self.epic, "rev-parse", "HEAD")
        feature = self.root / "feature"
        self.git(self.repo, "worktree", "add", "-b", "feature", str(feature), "main")
        (feature / "other").write_text("feature", encoding="utf-8", newline="")
        self.git(feature, "add", "other")
        self.git(feature, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "-m", "feature")
        self.git(self.epic, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "merge", "--no-edit", "feature")
        merged = self.git(self.epic, "rev-parse", "HEAD")
        self.assertNotEqual(merged, b)
        self.detached(result["path"], self.sha)
        self.assertEqual(Path(result["path"], "file").read_bytes(), b"A")
        self.out.unlink()
        self.dispatch["worktree"]["start_sha"] = b
        second = self.prepare("attempt-2")
        self.assertNotEqual(result["path"], second["path"])
        self.detached(second["path"], b)
        self.assertEqual(self.git(self.epic, "rev-parse", "HEAD"), merged)
        self.assertEqual(self.git(self.epic, "status", "--porcelain"), "")

    def test_distinct_attempts_at_same_sha_have_independent_paths(self):
        first = self.prepare()
        self.out.unlink()
        second = self.prepare("attempt-2")
        self.assertNotEqual(first["path"], second["path"])
        self.assertNotEqual(self.rows[0]["comment"], self.rows[1]["comment"])
        self.assertFalse(second["reused"])

    def test_retry_reuses_a_verified_detached_checkout(self):
        first = self.prepare()
        self.out.unlink()
        self.hide_comments = True
        second = self.prepare()
        self.assertEqual(first["path"], second["path"])
        self.assertTrue(second["reused"])
        self.assertEqual(len(self.rows), 1)

    def test_retry_refuses_an_undetached_marked_checkout(self):
        first = self.prepare()
        self.out.unlink()
        self.git(first["path"], "switch", "-c", "still-working")
        self.refused("branch")
        self.assertEqual(self.git(first["path"], "branch", "--show-current"), "still-working")

    def test_retry_refuses_a_dirty_marked_checkout(self):
        first = self.prepare()
        self.out.unlink()
        dirty = Path(first["path"], "untracked")
        dirty.write_text("keep", encoding="utf-8", newline="")
        self.refused("dirty")
        self.assertEqual(dirty.read_bytes(), b"keep")

    def test_missing_commit_refuses(self):
        self.dispatch["worktree"]["start_sha"] = "f" * 40
        self.refused("commit")
        self.assertEqual(self.rows, [])

    def test_wrong_repository_refuses_before_detach(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        self.git(foreign, "init", "-b", "main")
        self.commit(foreign, "foreign")
        head = self.git(foreign, "rev-parse", "HEAD")
        self.return_path = foreign
        self.refused("repository")
        self.assertEqual(self.git(foreign, "branch", "--show-current"), "main")
        self.assertEqual(self.git(foreign, "rev-parse", "HEAD"), head)

    def test_created_path_equal_to_an_integration_worktree_refuses(self):
        self.return_path = self.epic
        self.refused("existing|integration")
        self.assertEqual(self.git(self.epic, "branch", "--show-current"), "epic/x")

    def test_preexisting_detached_checkout_refuses(self):
        path = self.root / "old-reader"
        self.git(self.repo, "worktree", "add", "--detach", str(path), self.sha)
        self.return_path = path
        self.refused("existing")
        self.detached(path, self.sha)

    def test_nonroot_return_refuses_before_detach(self):
        def subdir(row):
            child = Path(row["path"], "subdir")
            child.mkdir()
            row["path"] = str(child)
        self.after_create = subdir
        self.refused("root")
        self.assertNotEqual(self.git(Path(self.rows[0]["path"]).parent, "branch", "--show-current"), "")

    def test_setup_that_moves_head_refuses_before_detach(self):
        self.after_create = lambda row: self.commit(row["path"], "setup")
        self.refused("HEAD|base tip")
        path = self.rows[0]["path"]
        self.assertNotEqual(self.git(path, "branch", "--show-current"), "")
        self.assertEqual(Path(path, "file").read_bytes(), b"setup")

    def test_new_dirty_checkout_refuses_before_detach(self):
        self.after_create = lambda row: Path(row["path"], "dirty").write_text("keep", encoding="utf-8", newline="")
        self.refused("dirty")
        self.assertNotEqual(self.git(self.rows[0]["path"], "branch", "--show-current"), "")

    def test_failed_create_refuses_without_placement(self):
        self.create_error = True
        self.refused("create failed")

    def test_existing_output_refuses_without_overwriting_or_creating(self):
        self.out.write_text('["--worktree","path:/stale"]', encoding="utf-8", newline="")
        with self.assertRaisesRegex(SystemExit, "PREPARATION REFUSED.*output.*exists"):
            self.prepare()
        self.assertEqual(self.out.read_bytes(), b'["--worktree","path:/stale"]')
        self.assertEqual(self.rows, [])

    def test_invalid_attempt_refuses(self):
        for attempt in ("", "a/b", "a b", "a\n", "a" * 129):
            with self.subTest(attempt=attempt), self.assertRaisesRegex(SystemExit, "attempt"):
                self.prepare(attempt)
            self.assertFalse(self.out.exists())
        self.assertEqual(self.rows, [])

    def test_incomplete_inventory_refuses(self):
        for override in ({"truncated": True}, {"totalCount": 1}, {"hostScope": {"omittedHostIds": ["remote"]}}):
            self.list_override = override
            self.refused("incomplete")
        self.assertEqual(self.rows, [])

    def test_non_reader_dispatch_is_rejected_by_prepare_reader(self):
        self.dispatch["worktree"]["action"] = "reuse"
        self.refused("not a pin-detached")

    def test_place_directs_readers_to_preparation(self):
        with self.assertRaisesRegex(SystemExit, "use prepare-reader"):
            self.run_helper("place", "--out-placement", str(self.out))
        self.assertFalse(self.out.exists())

    def settle(self, placed):
        start = {"result": {"effects": [{"kind": "worktree", "id": self.rows[0]["id"]}]}}
        return self.run_helper("settle-placement", "--placement-result", self.write("placed.json", placed),
                               "--start", self.write("start.json", start))

    def test_settle_placement_verifies_detached_sha(self):
        placed = self.prepare()
        result = self.settle(placed)
        self.assertIsNone(result["branch"])
        self.assertEqual(result["start_sha"], self.sha)
        self.commit(placed["path"], "moved")
        with self.assertRaisesRegex(SystemExit, "PLACEMENT MISMATCH.*HEAD"):
            self.settle(placed)

    def test_settle_rejects_branch_and_wrong_path(self):
        placed = self.prepare()
        self.git(placed["path"], "switch", "-c", "unexpected")
        with self.assertRaisesRegex(SystemExit, "PLACEMENT MISMATCH.*branch"):
            self.settle(placed)
        placed["path"] = str(self.epic)
        with self.assertRaisesRegex(SystemExit, "PLACEMENT MISMATCH.*path"):
            self.settle(placed)

    def test_dirty_epic_is_not_touched(self):
        (self.epic / "file").write_text("uncommitted", encoding="utf-8", newline="")
        before = self.git(self.epic, "status", "--porcelain")
        self.detached(self.prepare()["path"], self.sha)
        self.assertEqual((self.epic / "file").read_bytes(), b"uncommitted")
        self.assertEqual(self.git(self.epic, "status", "--porcelain"), before)
        self.assertEqual(self.git(self.epic, "rev-parse", "HEAD"), self.sha)

    def test_ambiguous_markers_refuse(self):
        self.prepare()
        self.out.unlink()
        self.rows.append(dict(self.rows[0]))
        self.refused("ambiguous")

    def test_failed_create_can_leave_branch_but_retry_never_detaches_it(self):
        def crash(row):
            raise SystemExit("PREPARATION REFUSED: connection lost after create")
        self.after_create = crash
        self.refused("connection lost")
        self.after_create = None
        path = self.rows[0]["path"]
        branch = self.git(path, "branch", "--show-current")
        self.refused("branch")
        self.assertEqual(self.git(path, "branch", "--show-current"), branch)
        self.assertEqual(len(self.rows), 1)

    def test_post_checkout_hook_that_moves_head_refuses(self):
        def hook(row):
            hooks = self.repo / ".git" / "hooks"
            script = hooks / "post-checkout"
            script.write_text(
                "#!/bin/sh\ngit -c user.name=Test -c user.email=test@example.com commit --allow-empty -m hook >/dev/null\n",
                encoding="utf-8", newline="")
            script.chmod(0o700)
        self.after_create = hook
        self.refused("HEAD")
        self.assertNotEqual(self.git(self.rows[0]["path"], "rev-parse", "HEAD"), self.sha)

    def test_malformed_reader_action_refuses(self):
        for field, value in (("start_sha", "abc"), ("start_sha", "A" * 40), ("branch", "HEAD"), ("branch", "")):
            with self.subTest(field=field, value=value):
                original = self.dispatch["worktree"][field]
                self.dispatch["worktree"][field] = value
                self.refused("malformed")
                self.dispatch["worktree"][field] = original
        self.assertEqual(self.rows, [])

    def test_dangling_output_symlink_refuses(self):
        self.out.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(SystemExit, "PREPARATION REFUSED.*output.*exists"):
            self.prepare()
        self.assertTrue(self.out.is_symlink())
        self.assertEqual(self.rows, [])

    def test_sha256_repository(self):
        repo = self.root / "sha256"
        result = subprocess.run(["git", "init", "--object-format=sha256", "-b", "main", str(repo)], capture_output=True)
        if result.returncode:
            self.skipTest("Git does not support SHA256 repositories")
        self.repo = repo
        self.commit(repo, "sha256")
        self.sha = self.git(repo, "rev-parse", "HEAD")
        self.assertEqual(len(self.sha), 64)
        self.dispatch["repo"]["path"] = str(repo)
        self.dispatch["worktree"].update(start_sha=self.sha, base_branch="main", parent_path=None)
        self.detached(self.prepare()["path"], self.sha)


if __name__ == "__main__":
    unittest.main()
