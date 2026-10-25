#!/usr/bin/env python3
"""Exercise the adapter parser and documented receipt-writer runtime closure."""

import contextlib
import io
import json
import re
import runpy
import subprocess
import sys
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch

MAIN = runpy.run_path(str(Path(__file__).resolve().parents[1] / "skills/finishing-relay/references/finish-receipt.py"))[
    "main"
]


@dataclass
class Result:
    complete: bool = False
    refusal: str | None = None
    changed: bool = False
    receipt_path: str | None = None


class Adapter(unittest.TestCase):
    def test_documented_record_runtimes_import_receipt_writer(self):
        plugin = Path(__file__).resolve().parents[1]
        project = plugin.parents[1]
        examples = []
        for path in sorted((plugin / "skills").rglob("*.md")):
            text = path.read_text(encoding="utf-8").replace("\\\n", " ")
            for match in re.finditer(r"uv run[^\n]*?--package (\S+)[^\n]*?finish-receipt\.py record\b", text):
                examples.append((path.relative_to(plugin), match.group(1)))
        self.assertTrue(examples, "no documented record-mode runtime examples found")
        imports = {}
        for path, package in examples:
            with self.subTest(document=str(path), package=package):
                if package not in imports:
                    # A fresh declared closure must work without the all-package
                    # development environment or this file's sys.modules doubles.
                    imports[package] = subprocess.run(
                        [
                            "uv",
                            "run",
                            "--isolated",
                            "--offline",
                            "--no-dev",
                            "--project",
                            str(project),
                            "--package",
                            package,
                            "python",
                            "-I",
                            "-c",
                            "from gw_orchestrate.finish_receipt import run_record_finish; "
                            "assert callable(run_record_finish)",
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=120,
                    )
                result = imports[package]
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_parser_and_core_outcomes(self):
        for mode, result, code in [
            ("inspect", Result(), 1),
            ("inspect", Result(complete=True), 0),
            ("record", Result(changed=True), 0),
            ("record", Result(refusal="held"), 1),
        ]:
            discovery = types.ModuleType("gw_kernel.workspace.discovery")
            discovery.resolve = Mock(return_value="layout")
            finish = types.ModuleType("gw_pipeline.finish")
            finish.inspect_finish = Mock(return_value=result)
            record = types.ModuleType("gw_orchestrate.finish_receipt")
            record.run_record_finish = Mock(return_value=result)
            output = io.StringIO()
            with (
                patch.dict(sys.modules, {m.__name__: m for m in (discovery, finish, record)}),
                patch("subprocess.run", side_effect=AssertionError("no merge or advance subprocess")),
                contextlib.redirect_stdout(output),
            ):
                status = MAIN([mode, "work/epic-a", "--workspace", "/wiki", "--repo", "ui"])
            self.assertEqual(status, code)
            self.assertEqual(json.loads(output.getvalue())["changed"], result.changed)
            discovery.resolve.assert_called_once_with(workspace=Path("/wiki"))
            if mode == "inspect":
                finish.inspect_finish.assert_called_once_with("layout", "work/epic-a")
                record.run_record_finish.assert_not_called()
            else:
                self.assertEqual(record.run_record_finish.call_args.kwargs["repo_name"], "ui")
                finish.inspect_finish.assert_not_called()

    def test_record_requires_repo(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            MAIN(["record", "work/epic-a", "--workspace", "/wiki"])
        self.assertEqual(raised.exception.code, 2)

    def _cleanup(self, *, status="resolved", entries=("code",), removed=True, kept="dirty", runner="/elsewhere", refusal=None):
        """Run `cleanup` against doubled engine modules; returns (exit status, parsed JSON, remove mock)."""
        modules = {}

        def module(name, **attrs):
            mod = types.ModuleType(name)
            for key, value in attrs.items():
                setattr(mod, key, value)
            modules[name] = mod
            return mod

        item = types.SimpleNamespace(work_status=status)
        placement = types.SimpleNamespace(worktree=Path("/ws/tree"), branch="feat")
        step = types.SimpleNamespace(action="removed" if removed else "kept", detail=None if removed else kept)
        outcome = types.SimpleNamespace(refusal=refusal, chain=(step,))
        remove = Mock(return_value=outcome)
        items = types.SimpleNamespace(by_path={"work/epic-a": item})
        module("gw_kernel.workspace.discovery", resolve=Mock(return_value="layout"))
        module("gw_kernel.workspace.bundle", load_work_bundle=Mock(return_value="bundle"))
        module("gw_kernel.workspace.errors", WorkspaceError=type("WorkspaceError", (Exception,), {}))
        module("gw_kernel.workspace.provenance", GitFailure=type("GitFailure", (), {}), gate_git=Mock(return_value="git"))
        module(
            "gw_pipeline.finish",
            read_finish_receipt=Mock(
                return_value=(object(), tuple(types.SimpleNamespace(repo=r) for r in entries), None)
            ),
        )
        module("gw_pipeline.place", PlaceContext=Mock(), remove=remove)
        module("gw_pipeline.placement", NullNotifier=Mock())
        module("gw_pipeline.placement_resolve", item_placement=Mock(return_value=placement))
        module("work_tracker_okf.items", load_items=Mock(return_value=items))
        output = io.StringIO()
        with (
            patch.dict(sys.modules, modules),
            patch("subprocess.run", side_effect=AssertionError("cleanup never executes")),
            contextlib.redirect_stdout(output),
        ):
            flags = ["--runner-cwd", runner] if runner is not None else []
            status = MAIN(["cleanup", "work/epic-a", "--workspace", "/wiki", *flags])
        return status, json.loads(output.getvalue()), remove

    def test_cleanup_maps_removed_to_remove(self):
        status, out, remove = self._cleanup()
        self.assertEqual(status, 0)
        self.assertIsNone(out["refusal"])
        self.assertEqual(
            out["rows"],
            [
                {
                    "repo": "code",
                    "worktree": "/ws/tree",
                    "branch": "feat",
                    "action": "remove",
                    "reason": "",
                    "command": "gw work place work/epic-a --remove --repo-name code",
                }
            ],
        )
        self.assertTrue(remove.called)

    def test_cleanup_maps_kept_to_skip(self):
        status, out, _ = self._cleanup(removed=False, kept="dirty")
        self.assertEqual(status, 0)
        self.assertEqual((out["rows"][0]["action"], out["rows"][0]["reason"]), ("skip", "dirty"))

    def test_cleanup_skips_a_refused_removal_with_its_kind(self):
        status, out, _ = self._cleanup(refusal=types.SimpleNamespace(kind="not-finished"))
        self.assertEqual((out["rows"][0]["action"], out["rows"][0]["reason"]), ("skip", "not-finished"))

    def test_cleanup_defers_runner_cwd(self):
        status, out, remove = self._cleanup(runner="/ws/tree/sub")
        self.assertEqual(status, 0)
        self.assertEqual((out["rows"][0]["action"], out["rows"][0]["reason"]), ("deferred", "runner"))
        remove.assert_not_called()

    def test_cleanup_refuses_unresolved(self):
        status, out, _ = self._cleanup(status="in-progress")
        self.assertEqual(status, 1)
        self.assertEqual(out["rows"], [])
        self.assertIn("not resolved", out["refusal"])

    def test_cleanup_never_deletes_branches(self):
        _, out, _ = self._cleanup(entries=("code", "docs"))
        self.assertEqual([row["repo"] for row in out["rows"]], ["code", "docs"])
        for row in out["rows"]:
            for forbidden in ("branch -d", "branch -D", "update-ref"):
                self.assertNotIn(forbidden, row["command"])

    def test_cleanup_runner_cwd_defaults_to_cwd(self):
        status, out, _ = self._cleanup(runner=None)
        self.assertEqual(out["rows"][0]["action"], "remove")


if __name__ == "__main__":
    unittest.main()
