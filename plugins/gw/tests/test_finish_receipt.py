#!/usr/bin/env python3
"""Exercise the adapter parser and documented receipt-writer runtime closure."""
import contextlib
from dataclasses import dataclass
import io
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch

MAIN = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'skills/finishing-relay/references/finish-receipt.py'))['main']

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
        for path in sorted((plugin / 'skills').rglob('*.md')):
            text = path.read_text(encoding='utf-8').replace('\\\n', ' ')
            for match in re.finditer(
                r'uv run[^\n]*?--package (\S+)[^\n]*?finish-receipt\.py record\b', text
            ):
                examples.append((path.relative_to(plugin), match.group(1)))
        self.assertTrue(examples, 'no documented record-mode runtime examples found')
        imports = {}
        for path, package in examples:
            with self.subTest(document=str(path), package=package):
                if package not in imports:
                    # A fresh declared closure must work without the all-package
                    # development environment or this file's sys.modules doubles.
                    imports[package] = subprocess.run(
                        ['uv', 'run', '--isolated', '--offline', '--no-dev',
                         '--project', str(project), '--package', package,
                         'python', '-I', '-c',
                         'from gw_orchestrate.finish_receipt import run_record_finish; '
                         'assert callable(run_record_finish)'],
                        capture_output=True, text=True, encoding='utf-8', timeout=120,
                    )
                result = imports[package]
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_parser_and_core_outcomes(self):
        for mode, result, code in [('inspect', Result(), 1), ('inspect', Result(complete=True), 0),
                                   ('record', Result(changed=True), 0), ('record', Result(refusal='held'), 1)]:
            discovery = types.ModuleType('gw_kernel.workspace.discovery')
            discovery.resolve = Mock(return_value='layout')
            finish = types.ModuleType('gw_pipeline.finish')
            finish.inspect_finish = Mock(return_value=result)
            record = types.ModuleType('gw_orchestrate.finish_receipt')
            record.run_record_finish = Mock(return_value=result)
            output = io.StringIO()
            with patch.dict(sys.modules, {m.__name__: m for m in (discovery, finish, record)}), \
                 patch('subprocess.run', side_effect=AssertionError('no merge or advance subprocess')), \
                 contextlib.redirect_stdout(output):
                status = MAIN([mode, 'work/epic-a', '--workspace', '/wiki', '--repo', 'ui'])
            self.assertEqual(status, code)
            self.assertEqual(json.loads(output.getvalue())['changed'], result.changed)
            discovery.resolve.assert_called_once_with(workspace=Path('/wiki'))
            if mode == 'inspect':
                finish.inspect_finish.assert_called_once_with('layout', 'work/epic-a')
                record.run_record_finish.assert_not_called()
            else:
                self.assertEqual(record.run_record_finish.call_args.kwargs['repo_name'], 'ui')
                finish.inspect_finish.assert_not_called()

    def test_record_requires_repo(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            MAIN(['record', 'work/epic-a', '--workspace', '/wiki'])
        self.assertEqual(raised.exception.code, 2)

    def test_cleanup_mode(self):
        for refusal, code in [(None, 0), ('not resolved', 1)]:
            @dataclass
            class Plan:
                rows: tuple = ()
                refusal: str | None = None
            discovery = types.ModuleType('gw_kernel.workspace.discovery')
            discovery.resolve = Mock(return_value='layout')
            finish = types.ModuleType('gw_pipeline.finish')
            finish.plan_finish_cleanup = Mock(return_value=Plan(refusal=refusal))
            record = types.ModuleType('gw_orchestrate.finish_receipt')
            output = io.StringIO()
            with patch.dict(sys.modules, {m.__name__: m for m in (discovery, finish, record)}), \
                 patch('subprocess.run', side_effect=AssertionError('cleanup never executes')), \
                 contextlib.redirect_stdout(output):
                status = MAIN(['cleanup', 'work/epic-a', '--workspace', '/wiki', '--runner-cwd', '/elsewhere'])
            self.assertEqual(status, code)
            self.assertEqual(json.loads(output.getvalue())['refusal'], refusal)
            finish.plan_finish_cleanup.assert_called_once_with('layout', 'work/epic-a', runner_cwd=Path('/elsewhere'))

    def test_cleanup_runner_cwd_defaults_to_cwd(self):
        discovery = types.ModuleType('gw_kernel.workspace.discovery')
        discovery.resolve = Mock(return_value='layout')
        finish = types.ModuleType('gw_pipeline.finish')
        finish.plan_finish_cleanup = Mock(return_value=Result())
        record = types.ModuleType('gw_orchestrate.finish_receipt')
        with patch.dict(sys.modules, {m.__name__: m for m in (discovery, finish, record)}), \
             contextlib.redirect_stdout(io.StringIO()):
            MAIN(['cleanup', 'work/epic-a', '--workspace', '/wiki'])
        self.assertEqual(finish.plan_finish_cleanup.call_args.kwargs['runner_cwd'], Path.cwd())

if __name__ == '__main__':
    unittest.main()
