#!/usr/bin/env python3
"""classify-lifecycle maps one Orca worker-release/worker-stop receipt to an action (auto-drive §4.2/§4.3).

Fixtures under fixtures/orca-lifecycle/ are real coordinator receipts; their
README records provenance and live probe limits. All payloads built inline
(including ok()) are SYNTHETIC contract cases, not recorded Orca evidence.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import runpy
import unittest
import subprocess
import sys
import tempfile

HELPER = runpy.run_path(
    str(
        Path(__file__).resolve().parents[1]
        / "skills/auto-drive/references/launch-worker.py"
    )
)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "orca-lifecycle"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def ok(**result):
    return {"ok": True, "result": {"dispatchId": "ctx_x", **result}}


def action(op, payload, mode="attend", authority=None):
    return HELPER["lifecycle_action"](op, payload, mode=mode, authority=authority)[
        "action"
    ]


class ReleaseTests(unittest.TestCase):
    def test_recorded_release_receipts(self):
        cases = {
            "release-released.json": "done",
            "release-already-released.json": "done",
            "release-no-interaction.json": "done",
            "release-already-released-no-interaction.json": "done",
            "release-released-after-restart.json": "done",
            "release-retained-focus-sequence.json": "close-terminal",
            "release-retained-user-takeover.json": "close-terminal",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(action("release", fixture(name)), expected)

    def test_user_takeover_closes_only_attend_terminals(self):
        payload = ok(state="retained", reason="user_takeover", processAction="none")
        self.assertEqual(action("release", payload, mode="attend"), "close-terminal")
        for mode in ("autonomous", "relay", None):
            with self.subTest(mode=mode):
                self.assertEqual(
                    action("release", payload, mode=mode), "report-retained"
                )

    def test_other_retained_reasons_are_reported(self):
        for reason in (
            "identity_unproven",
            "external_terminal",
            "ownership_transferred",
            None,
        ):
            with self.subTest(reason=reason):
                payload = ok(
                    state="retained",
                    processAction="none",
                    **({"reason": reason} if reason else {}),
                )
                self.assertEqual(action("release", payload), "report-retained")

    def test_uncertain_releases_follow_the_receipt(self):
        for state in ("release_pending", "release_unknown"):
            with self.subTest(state=state):
                self.assertEqual(
                    action("release", ok(state=state, processAction="none")),
                    "follow-receipt",
                )

    def test_release_unknown_as_an_error_envelope_still_follows_the_receipt(self):
        payload = {
            "ok": False,
            "error": {"code": "release_unknown", "message": "Retry worker-release…"},
        }
        self.assertEqual(action("release", payload), "follow-receipt")

    def test_unrecognised_release_is_inspected(self):
        for payload in (
            ok(state="evaporated"),
            ok(),
            {"ok": False, "error": {"code": "boom"}},
            [],
            None,
        ):
            with self.subTest(payload=payload):
                self.assertEqual(action("release", payload), "inspect")


class StopTests(unittest.TestCase):
    def test_recorded_stop_receipts(self):
        self.assertEqual(
            action("stop", fixture("stop-already-settled.json"), authority="park"),
            "done",
        )
        self.assertEqual(action("stop", fixture("stop-stopped.json")), "done")
        self.assertEqual(
            action("stop", fixture("stop-already-settled-after-stop.json")), "done"
        )
        self.assertEqual(
            action(
                "stop",
                fixture("stop-unknown-user-owned.json"),
                authority="user-authorized",
            ),
            "abandon-then-close",
        )

    def test_synthetic_clean_stop(self):
        self.assertEqual(
            action("stop", ok(state="stopped", alreadySettled=False), authority=None),
            "done",
        )

    def test_stop_unknown_closes_only_under_authority(self):
        payload = ok(state="stop_unknown", alreadySettled=False, processAction="none")
        for authority in ("park", "user-authorized", "exit-evidence"):
            with self.subTest(authority=authority):
                self.assertEqual(
                    action("stop", payload, authority=authority), "abandon-then-close"
                )
        for authority in ("none", "already-settled", None):
            with self.subTest(authority=authority):
                self.assertEqual(
                    action("stop", payload, authority=authority), "abandon-report"
                )

    def test_already_settled_terminal_states_are_done(self):
        for state in ("succeeded", "failed", "stopped", "abandoned"):
            with self.subTest(state=state):
                self.assertEqual(
                    action(
                        "stop",
                        ok(state=state, alreadySettled=True, processAction="none"),
                    ),
                    "done",
                )

    def test_pending_or_unknown_stops_are_inspected(self):
        for payload in (
            ok(state="stopping", alreadySettled=False),
            ok(state="running", alreadySettled=False),
            ok(state="succeeded", alreadySettled=False),
            {"ok": False, "error": {"code": "x"}},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(action("stop", payload, authority="park"), "inspect")


class CliTests(unittest.TestCase):
    def test_cli_prints_the_action_row(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "synthetic.json"
            receipt.write_text(
                json.dumps(ok(state="retained", reason="user_takeover")),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(output):
                HELPER["classify_lifecycle"](
                    argparse.Namespace(
                        op="release",
                        result=str(receipt),
                        mode="attend",
                        authority=None,
                    )
                )
        row = json.loads(output.getvalue())
        self.assertEqual(
            (row["op"], row["state"], row["reason"], row["action"]),
            ("release", "retained", "user_takeover", "close-terminal"),
        )


class DefensiveTests(unittest.TestCase):
    """SYNTHETIC malformed and ambiguous receipts must never authorize cleanup."""

    def test_unknown_operation_is_inspected(self):
        self.assertEqual(action("other", ok(state="stopped")), "inspect")

    def test_malformed_shapes_are_inspected(self):
        for value in ([], {}, 1, True):
            for op in ("release", "stop"):
                for payload in (
                    ok(state=value),
                    ok(state="stopped" if op == "stop" else "released", reason=value),
                    {"error": {"code": value}},
                    {"result": value},
                ):
                    with self.subTest(op=op, payload=payload):
                        self.assertEqual(action(op, payload), "inspect")
            self.assertEqual(
                action("stop", ok(state="stop_unknown"), authority=value), "inspect"
            )

    def test_result_state_wins_over_error_code(self):
        for state, expected in (("released", "done"), ("evaporated", "inspect")):
            payload = ok(state=state)
            payload["error"] = {"code": "release_unknown"}
            self.assertEqual(action("release", payload), expected)
        # Present malformed states are inspected, not replaced with error codes.
        self.assertEqual(
            action(
                "release",
                {"result": {"state": []}, "error": {"code": "release_unknown"}},
            ),
            "inspect",
        )

    def test_only_named_release_errors_follow(self):
        for state in ("release_pending", "release_unknown"):
            payload = {"ok": False, "error": {"code": state}}
            self.assertEqual(action("release", payload), "follow-receipt")
            self.assertEqual(action("stop", payload), "inspect")

    def test_settled_requires_literal_true(self):
        for settled in (False, 1, "true", [], None):
            self.assertEqual(
                action("stop", ok(state="succeeded", alreadySettled=settled)), "inspect"
            )

    def test_cli_registration_and_recorded_receipt(self):
        result = subprocess.run(
            [
                sys.executable,
                str(
                    Path(__file__).resolve().parents[1]
                    / "skills/auto-drive/references/launch-worker.py"
                ),
                "classify-lifecycle",
                "--op",
                "stop",
                "--result",
                str(FIXTURES / "stop-already-settled.json"),
                "--authority",
                "park",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"op": "stop", "state": "succeeded", "reason": None, "action": "done"},
        )


if __name__ == "__main__":
    unittest.main()
