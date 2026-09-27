#!/usr/bin/env python3
"""attend-cards derives a worktree's in-review card from live attend dispatches (auto-drive §2.5.3).

Task rows are shaped like `orca orchestration task-list --json` on 1.4.211;
classification rows like `launch-worker.py classify-restart` output.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

HELPER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "skills/auto-drive/references/launch-worker.py"))
WT_A, WT_B = "/wt/main", "/wt/other"


def task(task_id, *, mode="attend", worktree=WT_A, spec=None, truncated=False):
    if spec is None:
        envelope = {
            "version": 2 if mode is not None else 1,
            "dispatch_key": f"gw-design-{task_id}",
            "agent": "claude",
            "model": None,
            "reasoning_effort": None,
            "placement_argv": ["--worktree", f"path:{worktree}"] if worktree else [],
        }
        if mode is not None:
            envelope["mode"] = mode
            envelope["worktree_path"] = worktree
        spec = "GW_LAUNCH_V1 " + json.dumps(envelope, separators=(",", ":")) + "\nRun it.\n"
    return {"id": task_id, "task_title": f"gw-design-{task_id}", "spec": spec, "spec_truncated": truncated}


def row(task_id, action):
    return {"task_id": task_id, "dispatch_id": f"ctx_{task_id}", "action": action}


def cards(tasks, rows):
    return HELPER["attend_cards_result"](tasks, rows)


class AttendCardsTests(unittest.TestCase):
    def test_two_live_attends_on_one_worktree_hold_it_until_both_are_gone(self):
        tasks = [task("t1"), task("t2")]
        self.assertEqual(cards(tasks, [row("t1", "live"), row("t2", "live")])["set_in_review"], [WT_A])
        one_done = cards(tasks, [row("t1", "settled"), row("t2", "live")])
        self.assertEqual((one_done["set_in_review"], one_done["release_candidates"]), ([WT_A], []))
        both_done = cards(tasks, [row("t1", "settled"), row("t2", "recovered-settled")])
        self.assertEqual((both_done["set_in_review"], both_done["release_candidates"]), ([], [WT_A]))

    def test_a_parked_attend_frees_the_card(self):
        result = cards([task("t1")], [row("t1", "parked")])
        self.assertEqual((result["set_in_review"], result["release_candidates"]), ([], [WT_A]))

    def test_every_non_live_class_frees_the_card(self):
        for action in ("settled", "recovered-settled", "parked", "deliberate-skip", "recovery-inspection"):
            with self.subTest(action=action):
                self.assertEqual(cards([task("t1")], [row("t1", action)])["release_candidates"], [WT_A])

    def test_a_task_with_no_classification_row_is_not_live(self):
        self.assertEqual(cards([task("t1")], [])["release_candidates"], [WT_A])

    def test_replaying_the_same_rows_is_idempotent(self):
        tasks, rows = [task("t1"), task("t2")], [row("t1", "settled"), row("t2", "live")]
        self.assertEqual(cards(tasks, rows), cards(tasks, rows + rows))

    def test_worktrees_are_independent(self):
        result = cards([task("t1"), task("t2", worktree=WT_B)], [row("t1", "live"), row("t2", "settled")])
        self.assertEqual((result["set_in_review"], result["release_candidates"]), ([WT_A], [WT_B]))

    def test_non_attend_dispatches_never_appear(self):
        tasks = [task("t1", mode="autonomous"), task("t2", mode="relay", worktree=WT_B)]
        result = cards(tasks, [row("t1", "live"), row("t2", "live")])
        self.assertEqual(result, {"set_in_review": [], "release_candidates": [], "skipped": []})

    def test_undecodable_rows_are_skipped_with_a_reason(self):
        tasks = [
            task("bad", spec="not an envelope\n"),
            task("trunc", truncated=True),
            task("legacy", mode=None),
            task("nopath", worktree=None),
            task("json", spec="GW_LAUNCH_V1 {not json\nRun it.\n"),
            "not-a-dict",
        ]
        rows = [row(t["id"], "live") for t in tasks if isinstance(t, dict)]
        result = cards(tasks, rows)
        self.assertEqual((result["set_in_review"], result["release_candidates"]), ([], []))
        self.assertEqual(
            {(entry["task_id"], entry["reason"]) for entry in result["skipped"]},
            {
                ("bad", "envelope-undecodable"),
                ("trunc", "spec-unreadable"),
                ("legacy", "mode-unknown"),
                ("nopath", "worktree-unknown"),
                ("json", "envelope-undecodable"),
                (None, "task-unreadable"),
            },
        )

    def test_a_non_attend_task_with_no_worktree_is_not_skipped(self):
        self.assertEqual(cards([task("t1", mode="autonomous", worktree=None)], [row("t1", "live")])["skipped"], [])

    def test_paths_are_sorted_deduplicated_and_inputs_are_unchanged(self):
        tasks = [task("b", worktree=WT_B), task("a"), task("a2"), task("b2", worktree=WT_B)]
        rows = [row("b", "live"), row("a", "live"), row("a", "live"), None, {}, {"task_id": [], "action": "live"}]
        before = copy.deepcopy((tasks, rows))
        self.assertEqual(cards(tasks, rows), {"set_in_review": [WT_A, WT_B], "release_candidates": [], "skipped": []})
        self.assertEqual(cards(tasks, []), {"set_in_review": [], "release_candidates": [WT_A, WT_B], "skipped": []})
        self.assertEqual((tasks, rows), before)

    def test_frozen_worktree_path_wins_over_placement_arguments(self):
        item = task("t1")
        envelope = json.loads(item["spec"].split("\n", 1)[0].removeprefix("GW_LAUNCH_V1 "))
        envelope["placement_argv"] = ["--worktree", "path:/wrong/path"]
        item["spec"] = "GW_LAUNCH_V1 " + json.dumps(envelope) + "\nRun it.\n"
        self.assertEqual(HELPER["attend_target"](item), ("attend", WT_A, None))
        envelope["worktree_path"] = None
        item["spec"] = "GW_LAUNCH_V1 " + json.dumps(envelope) + "\nRun it.\n"
        self.assertEqual(HELPER["attend_target"](item), ("attend", None, "worktree-unknown"))

    def test_malformed_rows_are_silent_and_do_not_hide_valid_tasks(self):
        tasks = [{}, {"id": 12}, {"id": "missing"}, task("bad", spec="GW_LAUNCH_V1 {}\n"), task("ok")]
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = cards(tasks, [row("ok", "live")])
        self.assertEqual((output.getvalue(), errors.getvalue()), ("", ""))
        self.assertEqual(result, {"set_in_review": [WT_A], "release_candidates": [], "skipped": [
            {"task_id": None, "reason": "task-unreadable"},
            {"task_id": None, "reason": "task-unreadable"},
            {"task_id": "bad", "reason": "envelope-undecodable"},
            {"task_id": "missing", "reason": "spec-unreadable"},
        ]})

    def test_cli_registration_and_invalid_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks_path, rows_path = Path(directory) / "tasks.json", Path(directory) / "rows.json"
            tasks_path.write_text(json.dumps({"ok": True, "result": {"tasks": [task("t1")]}}), encoding="utf-8", newline="\n")
            rows_path.write_text(json.dumps([row("t1", "live")]), encoding="utf-8", newline="\n")
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / "skills/auto-drive/references/launch-worker.py"), "attend-cards", "--tasks", str(tasks_path), "--classification", str(rows_path)]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"set_in_review": [WT_A], "release_candidates": [], "skipped": []})
            self.assertEqual(len(result.stdout.splitlines()), 1)
            rows_path.write_text("{}", encoding="utf-8", newline="\n")
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("must be classify-restart's JSON list", result.stderr)

    def test_cli_reads_task_list_and_classification_files(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks_path, rows_path = Path(directory) / "tasks.json", Path(directory) / "rows.json"
            tasks_path.write_text(json.dumps({"ok": True, "result": {"tasks": [task("t1")]}}), encoding="utf-8", newline="\n")
            rows_path.write_text(json.dumps([row("t1", "live")]), encoding="utf-8", newline="\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                HELPER["attend_cards"](argparse.Namespace(tasks=str(tasks_path), classification=str(rows_path)))
        self.assertEqual(json.loads(output.getvalue())["set_in_review"], [WT_A])


if __name__ == "__main__":
    unittest.main()
