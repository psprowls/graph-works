#!/usr/bin/env python3
"""`launch-worker.py` keeps no placement or launch commands, and skill text names only what it has.

`gw work dispatch` places, encodes, creates and launches every worker, retry and
park-resume included (`--retry-of`), so the plugin's helper carries only the
decode, resume-text, classification, record and hash commands. A skill that
names a command the parser lacks, or a deleted one, is a recipe nothing runs.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
from pathlib import Path
import re
import unittest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "skills/auto-drive/references/launch-worker.py"

EXPECTED = {
    "decode",
    "resume-spec",
    "classify-restart",
    "classify-report",
    "attend-cards",
    "classify-lifecycle",
    "record-write",
    "spec-hash",
}
DELETED = {"place", "prepare", "prepare-reader", "settle-placement", "encode", "create", "launch"}
NAMED = re.compile(r"launch-worker\.py\s+([a-z][a-z-]*)")


def load_module():
    spec = importlib.util.spec_from_file_location("launch_worker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def subcommands(root: argparse.ArgumentParser) -> set[str]:
    actions = [action for action in root._actions if isinstance(action, argparse._SubParsersAction)]
    assert len(actions) == 1
    return set(actions[0].choices)


def named_commands() -> dict[str, set[str]]:
    """Every `launch-worker.py <command>` a skill or reference page names, by page."""
    named: dict[str, set[str]] = {}
    for page in sorted((PLUGIN / "skills").rglob("*.md")):
        found = set(NAMED.findall(page.read_text(encoding="utf-8")))
        if found:
            named[str(page.relative_to(PLUGIN))] = found
    return named


class LaunchWorkerCommandTests(unittest.TestCase):
    def test_the_parser_has_exactly_the_surviving_commands(self):
        self.assertEqual(subcommands(load_module().parser()), EXPECTED)

    def test_no_deleted_function_survives(self):
        module = load_module()
        for name in (
            "place",
            "prepare",
            "prepare_reader",
            "settle_placement",
            "encode",
            "create",
            "launch",
            "owning_checkout",
            "registered_repo",
            "placement_dispatch",
            "selected_preparation",
            "marked_rows",
            "started_worktree_id",
            "show_worktree",
            "preparation_runtime",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(module, name))
        for name in ("EXISTING_ACTIONS", "CREATION_ACTIONS", "READER_ACTIONS"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(module, name))

    def test_resume_spec_takes_no_placement(self):
        root = load_module().parser()
        args = root.parse_args(["resume-spec", "--spec", "s", "--checkpoint", "c", "--answer", "a"])
        self.assertFalse(hasattr(args, "placement"))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            root.parse_args(["resume-spec", "--spec", "s", "--checkpoint", "c", "--answer", "a", "--placement", "[]"])

    def test_every_command_skill_text_names_is_in_the_parser(self):
        commands = subcommands(load_module().parser())
        named = named_commands()
        self.assertTrue(named, "no skill names a launch-worker.py command; the scan is broken")
        for page, found in named.items():
            with self.subTest(page=page):
                self.assertEqual(found - commands, set())

    def test_no_skill_text_names_a_deleted_command(self):
        for page, found in named_commands().items():
            with self.subTest(page=page):
                self.assertEqual(found & DELETED, set())

    def test_deleted_helpers_are_gone_from_the_tree(self):
        references = PLUGIN / "skills/auto-drive/references"
        self.assertFalse((references / "record-preparation.py").exists())
        self.assertFalse((references / "dispatch-checks.md").exists())


class EnvelopeVersionTests(unittest.TestCase):
    V1 = {
        "version": 1,
        "dispatch_key": "k",
        "agent": "claude",
        "model": None,
        "reasoning_effort": None,
        "placement_argv": [],
    }
    V2 = {**V1, "version": 2, "mode": "attend", "worktree_path": "/tmp/w"}

    def test_both_versions_validate(self):
        validate = load_module().validate_envelope
        for envelope in (self.V1, self.V2):
            with self.subTest(version=envelope["version"]):
                self.assertEqual(validate(dict(envelope)), envelope)

    def test_each_version_keeps_its_exact_key_set(self):
        validate = load_module().validate_envelope
        for bad in (
            {**self.V1, "mode": "attend"},
            {**self.V2, "extra": 1},
            {k: v for k, v in self.V2.items() if k != "worktree_path"},
            {**self.V2, "mode": ""},
        ):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                validate(bad)


if __name__ == "__main__":
    unittest.main()
