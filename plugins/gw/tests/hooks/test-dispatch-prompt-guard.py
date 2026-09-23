"""Black-box recovery contract; fake only the external Orca process."""

import json
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import importlib.util
from unittest.mock import patch


PLUGIN = Path(__file__).resolve().parents[2]
HEADER = "You are working inside Orca, a multi-agent IDE. You are a dispatched worker."
TASK = "task_example"
WORKER = "term_worker-123"
DISPATCH = "ctx_example"
ENVELOPE = 'GW_LAUNCH_V1 {"version":1,"dispatch_key":"example","agent":"claude","model":null,"reasoning_effort":null,"placement_argv":[]}'
# Sanitized shape of Orca 1.4.209's composed preamble. The first worker command
# follows >1016 bytes of explanation, just as in the historical startup loss.
PREAMBLE = (
    HEADER + "\nYour coordinator's terminal handle is: term_coordinator\n"
    "Your task ID is: task_example\n\n=== CLI COMMANDS ===\n```sh\n"
    + "  # Report the task outcome to the coordinator after completing the work.\n" * 20
    + "  orca orchestration send --from term_worker-123 --type worker_done "
    "--task-id task_example --dispatch-id ctx_example --outcome succeeded\n"
    "  orca orchestration ask --from term_worker-123 --question \"<question>\"\n"
    "```\n\n=== SUB-DISPATCH ===\nWorker guidance.\n\n=== TASK ===\n"
)
FULL = PREAMBLE + ENVELOPE + "\nReturn the exact result: café \\ path \"quoted\".\n=== TASK ===\nEnd of body.\n"


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = self.root / "calls.jsonl"
        self.response = self.root / "response.json"
        self.cli = self.root / "fake orca"
        self.cli.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys, time\n"
            "with open(os.environ['GUARD_CALLS'], 'a', encoding='utf-8', newline='') as f:\n"
            " f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "time.sleep(float(os.environ.get('GUARD_DELAY', '0')))\n"
            "sys.stdout.buffer.write(b'invalid json' if os.environ.get('GUARD_BAD_JSON') else pathlib.Path(os.environ['GUARD_RESPONSE']).read_bytes())\n"
            "sys.stderr.write('PRIVATE STDERR MUST NOT BE INJECTED')\n"
            "sys.exit(int(os.environ.get('GUARD_EXIT', '0')))\n",
            encoding="utf-8", newline="",
        )
        self.cli.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('ORCA_')}
        self.env.update(ORCA_CLI_COMMAND=str(self.cli), ORCA_TERMINAL_HANDLE=WORKER,
                        GUARD_CALLS=str(self.calls), GUARD_RESPONSE=str(self.response))
        self.data = {
            "ok": True,
            "result": {
                "dispatch": {"id": DISPATCH, "task_id": TASK, "assignee_handle": WORKER,
                             "status": "dispatched", "completed_at": None},
                "preamble": FULL,
                "capability": "PRIVATE METADATA MUST NOT BE INJECTED",
            },
        }

    def run_guard(self, prompt=None, *, raw=None, event="UserPromptSubmit"):
        self.response.write_text(json.dumps(self.data), encoding="utf-8", newline="")
        if raw is None:
            raw = json.dumps({"hook_event_name": event, "prompt": prompt})
        result = subprocess.run(
            ["bash", str(PLUGIN / "hooks/run-hook.cmd"), "dispatch-prompt-guard"],
            input=raw.encode(), capture_output=True, env=self.env, cwd=self.root, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        output = json.loads(result.stdout) if result.stdout else None
        self.assertNotIn("PRIVATE", result.stdout.decode())
        return output

    def assert_calls(self, count):
        calls = self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []
        self.assertEqual([json.loads(line) for line in calls],
                         [["orchestration", "dispatch-show", "--task", TASK, "--preamble", "--json"]] * count)
        self.calls.unlink(missing_ok=True)

    def assert_recovery(self, output, full=FULL):
        self.assertEqual(set(output), {"hookSpecificOutput"})
        context = output["hookSpecificOutput"]
        self.assertEqual(context["hookEventName"], "UserPromptSubmit")
        note, recovered = context["additionalContext"].split("\n\n", 1)
        self.assertIn("truncated", note)
        self.assertIn(TASK, note)
        self.assertIn(DISPATCH, note)
        self.assertEqual(recovered, full)

    def assert_declined(self, output):
        self.assertIsInstance(output, dict)
        self.assertIn("not verified", output["systemMessage"])
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Do not guess", context)
        self.assertNotIn(HEADER, context)
        self.assertNotIn("GW_LAUNCH_V1", context)

    def test_early_and_mid_preamble_cuts_restore_once(self):
        for fragment in (FULL[:1016], FULL[:len(PREAMBLE)-15], PREAMBLE + "GW_L"):
            with self.subTest(fragment_length=len(fragment)):
                self.assert_recovery(self.run_guard(fragment))
                self.assert_calls(1)

    def test_complete_envelope_does_not_hide_a_cut_body(self):
        self.assert_recovery(self.run_guard(PREAMBLE + ENVELOPE + "\nReturn the exact"))
        self.assert_calls(1)

    def test_transport_newline_on_a_cut_body_still_recovers(self):
        for ending in ("\n", "\r\n"):
            self.assert_recovery(self.run_guard(PREAMBLE + ENVELOPE + "\nReturn the exact" + ending))
            self.assert_calls(1)

    def test_full_prompt_is_quiet_even_if_coordinator_context_changed(self):
        delivered = FULL.replace("term_coordinator", "term_previous_coordinator")
        self.assertIsNone(self.run_guard(delivered))
        self.assert_calls(1)

    def test_unrelated_input_never_looks_up(self):
        for prompt in ("hello", "Please review:\n" + FULL, "> " + FULL, FULL[200:], None, {}, 12):
            with self.subTest(prompt=str(prompt)[:40]):
                self.assertIsNone(self.run_guard(prompt))
                self.assert_calls(0)
        for raw in ("not json", "[]", "null"):
            self.assertIsNone(self.run_guard(raw=raw))
            self.assert_calls(0)
        self.assertIsNone(self.run_guard(FULL[:1016], event="PreToolUse"))
        self.assert_calls(0)

    def test_wrapped_paste_and_crlf_preserve_text(self):
        full = FULL.replace("\n", "\r\n")
        self.data["result"]["preamble"] = full
        fragment = full[:1016]
        self.assert_recovery(self.run_guard('<pasted_content id="1">\r\n' + fragment + '\r\n</pasted_content id="1">'), full)
        self.assert_calls(1)
        self.assertIsNone(self.run_guard('<pasted_content id="1">\n' + fragment + '\n</pasted_content id="2">'))
        self.assert_calls(0)

    def test_transport_final_line_ending_is_ignored(self):
        for delivered in (FULL[:-1], FULL[:-1] + "\r\n"):
            self.assertIsNone(self.run_guard(delivered))
            self.assert_calls(1)

    def test_crlf_wrappers_do_not_add_carriage_returns_to_task(self):
        full = FULL.replace("\n", "\r\n")
        self.data["result"]["preamble"] = full
        for delivered, intact in ((full, True), (full[:full.index("exact")], False)):
            with self.subTest(intact=intact):
                wrapped = '<pasted_content id="7">\r\n' + delivered + '\r\n</pasted_content id="7">\r\n'
                result = self.run_guard(wrapped)
                if intact:
                    self.assertIsNone(result)
                else:
                    self.assert_recovery(result, full)
                self.assert_calls(1)

    def test_registration_is_synchronous_and_bounded(self):
        config = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))
        entries = config["hooks"].get("UserPromptSubmit", [])
        self.assertEqual(len(entries), 1)
        self.assertNotIn("matcher", entries[0])
        hook, = entries[0]["hooks"]
        self.assertEqual(hook["type"], "command")
        self.assertFalse(hook["async"])
        self.assertEqual(hook["timeout"], 15)
        self.assertEqual(hook["command"], '"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd" dispatch-prompt-guard')

    def test_missing_ambiguous_or_incomplete_task_id_never_looks_up(self):
        for before in (HEADER, HEADER + "\nYour task ID is: task_example",
                       HEADER + "\nYour task ID is: task_\n",
                       PREAMBLE.replace("task_example\n", "task_example; touch BAD\n"),
                       PREAMBLE.replace("\n=== CLI", "\nYour task ID is: task_other\n=== CLI"),
                       PREAMBLE.replace("\n=== CLI", "\nYour task ID is: task_example\n=== CLI")):
            with self.subTest(before=before[:200]):
                self.assert_declined(self.run_guard(before))
                self.assert_calls(0)

    def test_worker_identity_must_distinguish_attempts(self):
        self.env.pop("ORCA_TERMINAL_HANDLE")
        self.assert_declined(self.run_guard(FULL[:1016]))
        self.assert_calls(0)
        self.assert_recovery(self.run_guard(PREAMBLE + "GW_L"))
        self.assert_calls(1)
        # A --from fragment at EOF is not a complete token.
        cut = PREAMBLE[:PREAMBLE.index(WORKER) + len(WORKER)]
        self.assert_declined(self.run_guard(cut))
        self.assert_calls(0)
        self.assert_declined(self.run_guard(PREAMBLE.replace(WORKER, "term_coordinator") + "GW_L"))
        self.assert_calls(1)

    def test_surviving_worker_and_dispatch_ids_must_agree(self):
        for prompt in (PREAMBLE.replace(DISPATCH, "ctx_other") + "GW_L",
                       PREAMBLE.replace(WORKER, "term_other", 1) + "GW_L"):
            with self.subTest(prompt=prompt):
                self.assert_declined(self.run_guard(prompt))
                # It is valid to reject contradictory local identity before lookup.
                if self.calls.exists():
                    self.assert_calls(1)

    def test_invalid_or_settled_response_cannot_inject_task(self):
        original = copy.deepcopy(self.data)
        cases = [("task_id", "task_other"), ("assignee_handle", "term_other"),
                 ("id", ""), ("id", 1), ("status", "failed"), ("status", "completed"),
                 ("status", "revoked"), ("status", "settled"), ("status", None),
                 ("completed_at", "2026-09-23"), ("revoked_at", "2026-09-23")]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                self.data = copy.deepcopy(original)
                self.data["result"]["dispatch"][key] = value
                self.assert_declined(self.run_guard(FULL[:1016]))
                self.assert_calls(1)
        for response in ({}, [], None, {"ok": 1, "result": original["result"]},
                         {"ok": False, "result": original["result"]},
                         {"ok": True, "result": []}, {"ok": True, "result": {"dispatch": []}}):
            with self.subTest(response=response):
                self.data = response
                self.assert_declined(self.run_guard(FULL[:1016]))
                self.assert_calls(1)

    def test_recovered_preamble_must_have_same_identity_and_valid_task(self):
        for full in (None, 1, FULL.replace(HEADER, "not a worker"),
                     FULL.replace("Your task ID is: task_example", "Your task ID is: task_other"),
                     FULL.replace(WORKER, "term_other"), FULL.replace(DISPATCH, "ctx_other"),
                     FULL.replace("\n=== CLI", "\nYour task ID is: task_other\n=== CLI"),
                     FULL.replace("=== TASK ===", "TASK"), PREAMBLE + "plain task"):
            with self.subTest(full=str(full)[:100]):
                self.data["result"]["preamble"] = full
                self.assert_declined(self.run_guard(FULL[:1016]))
                self.assert_calls(1)

    def test_six_field_launch_contract_and_nonblank_body(self):
        envelope = json.loads(ENVELOPE.removeprefix("GW_LAUNCH_V1 "))
        invalid = [[], {}, {**envelope, "extra": 1}]
        for key, value in (("version", True), ("version", 2), ("agent", ""),
                           ("dispatch_key", " "), ("model", 1), ("model", " "),
                           ("reasoning_effort", []), ("reasoning_effort", "high"),
                           ("placement_argv", "--cwd"), ("placement_argv", [1])):
            invalid.append({**envelope, key: value})
        for value in invalid:
            with self.subTest(envelope=value):
                self.data["result"]["preamble"] = PREAMBLE + "GW_LAUNCH_V1 " + json.dumps(value) + "\nBody"
                self.assert_declined(self.run_guard(FULL[:1016]))
                self.assert_calls(1)
        for suffix in (ENVELOPE, ENVELOPE + "\n \n", "GW_LAUNCH_V1 {bad}\nBody"):
            self.data["result"]["preamble"] = PREAMBLE + suffix
            self.assert_declined(self.run_guard(FULL[:1016]))
            self.assert_calls(1)

    def test_conflicting_task_or_internal_whitespace_is_not_overwritten(self):
        for delivered in (FULL.replace("exact result", "different result"),
                          FULL.replace("Return the", "Return  the"),
                          FULL.replace("\n", "\r\n"), FULL + "Extra instruction"):
            self.assert_declined(self.run_guard(delivered))
            self.assert_calls(1)

    def test_shell_text_is_never_executed(self):
        full = FULL + "\n$(touch BAD); `touch BAD`; ' \" \\"
        self.data["result"]["preamble"] = full
        self.assert_recovery(self.run_guard(full[:-3]), full)
        self.assertFalse((self.root / "BAD").exists())
        self.assert_calls(1)

    def test_large_context_has_leading_instruction_to_read_full_output(self):
        full = FULL + "x" * 11000 + " END_SENTINEL"
        self.data["result"]["preamble"] = full
        output = self.run_guard(FULL[:1016])
        self.assert_recovery(output, full)
        lead = output["hookSpecificOutput"]["additionalContext"][:2000]
        self.assertIn("read the full hook-output file", lead)
        self.assertIn("before acting", lead)
        self.assert_calls(1)

    def test_lookup_failure_is_truthful_and_does_not_leak_output(self):
        for key, value in (("GUARD_EXIT", "1"), ("GUARD_BAD_JSON", "1")):
            with self.subTest(key=key):
                self.env[key] = value
                self.assert_declined(self.run_guard(FULL[:1016]))
                self.assert_calls(1)
                self.env.pop(key)

    def test_missing_executable_does_not_fall_back(self):
        self.env["ORCA_CLI_COMMAND"] = str(self.root / "missing")
        self.assert_declined(self.run_guard(FULL[:1016]))
        self.assert_calls(0)

    def test_timeout_finishes_before_hook_deadline(self):
        self.env["GUARD_DELAY"] = "30"
        start = time.monotonic()
        output = self.run_guard(FULL[:1016])
        self.assert_declined(output)
        self.assertIn("timed out", output["systemMessage"])
        self.assertLess(time.monotonic() - start, 8)
        self.assert_calls(1)

    def test_missing_python_is_nonblocking_without_installation(self):
        # Run the extensionless entrypoint with a PATH containing no Python.
        result = subprocess.run(["/bin/bash", str(PLUGIN / "hooks/dispatch-prompt-guard")],
                                input=b"{}", capture_output=True, env={"PATH": str(self.root)}, timeout=2)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assert_declined(json.loads(result.stdout))
        self.assertIn("Python 3", result.stdout.decode())
        self.assert_calls(0)

    def test_platform_executable_selection_avoids_linux_screen_reader(self):
        spec = importlib.util.spec_from_file_location("guard", PLUGIN / "hooks/dispatch-prompt-guard.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        cases = [
            ("linux", {}, "orca-ide"),
            ("linux", {"ORCA_TERMINAL_HANDLE": WORKER}, "orca"),
            ("darwin", {}, "orca"), ("win32", {}, "orca"),
            ("linux", {"ORCA_DEV_REPO_ROOT": "/dev/repo"}, "orca-dev"),
            ("darwin", {"ORCA_CLI_COMMAND": "/custom cli", "ORCA_DEV_REPO_ROOT": "/dev/repo"}, "/custom cli"),
        ]
        for platform, env, expected in cases:
            with self.subTest(platform=platform, env=env), patch.dict(os.environ, env, clear=True), patch.object(sys, "platform", platform):
                self.assertEqual(guard.orca_executable(), expected)


if __name__ == "__main__":
    unittest.main()
