#!/usr/bin/env python3
"""Exercise the adapter's real parser with injected core outcomes, no runtime deps."""
import contextlib
from dataclasses import dataclass
import io
import json
from pathlib import Path
import runpy
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
    def test_parser_and_core_outcomes(self):
        for mode, result, code in [('inspect', Result(), 1), ('inspect', Result(complete=True), 0),
                                   ('record', Result(changed=True), 0), ('record', Result(refusal='held'), 1)]:
            discovery = types.ModuleType('graph_works_core.workspace.discovery')
            discovery.resolve = Mock(return_value='layout')
            finish = types.ModuleType('graph_works_core.workspace.finish')
            finish.inspect_finish = Mock(return_value=result)
            record = types.ModuleType('graph_works_core.orchestrate.finish_receipt')
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

if __name__ == '__main__':
    unittest.main()
