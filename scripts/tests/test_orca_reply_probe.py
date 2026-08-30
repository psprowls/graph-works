"""Acceptance tests for `scripts/orca_reply_probe.py`.

Outside the repo's coverage `source` list for the same reason as its siblings:
`scripts/` is repo tooling, not a package, so these do not move the 95% gate.

`main()` carries an injected `runner` seam for exactly this — every case drives
the real `main()` with a fake `orca` and asserts on the printed report plus the
exit code. Each case is a property from
`work/epic-auto-drive-dispatch-correctness/children/bug-reply-never-reaches-asking-worker`'s
design stage: the probe exists so a regression in Orca's ask/reply correlation
is found by us rather than by a lost human answer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import orca_reply_probe  # noqa: E402

ASK_HELP = "Usage: orca orchestration ask (--question <text> | --resume <message_id>)\n  --resume\n"
REPLY_HELP = "Usage: orca orchestration reply --id <msg_id> --body <text>\n  --id <id>\n"
ANSWERED = {"answer": "merge", "timedOut": False, "cancelled": False}


def fake_runner(
    *,
    ask_help: str = ASK_HELP,
    reply_help: str = REPLY_HELP,
    ask_result: tuple[int, dict | str] = (0, ANSWERED),
    missing: bool = False,
):
    """An `orca` that answers the two `--help` probes and one live ask."""
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        if missing:
            raise FileNotFoundError(argv[0])
        if argv[1:4] == ["orchestration", "ask", "--help"]:
            return 0, ask_help
        if argv[1:4] == ["orchestration", "reply", "--help"]:
            return 0, reply_help
        if argv[1:3] == ["orchestration", "ask"]:
            code, payload = ask_result
            return code, payload if isinstance(payload, str) else json.dumps(payload)
        return 0, ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def report(capsys) -> str:
    return capsys.readouterr().out


def test_static_half_passes_offline(capsys):
    """P1: `ask --help` advertises `--resume` and `reply --help` advertises `--id`."""
    code = main_with([], fake_runner())
    out = report(capsys)
    assert code == 0
    assert "P1 ask/reply CLI surface: ok" in out


def test_live_half_skips_without_a_dispatch(capsys):
    """P2 reports `skipped`, never a vacuous pass, outside an active Dispatch."""
    code = main_with([], fake_runner())
    out = report(capsys)
    assert code == 0
    assert "P2 live ask/reply round trip: skipped (no active dispatch)" in out


def test_missing_resume_flag_fails(capsys):
    """A CLI surface change that drops `--resume` is what P1 exists to catch."""
    code = main_with([], fake_runner(ask_help="Usage: orca orchestration ask --question\n"))
    out = report(capsys)
    assert code == 1
    assert "--resume" in out


def test_missing_id_flag_fails(capsys):
    """Same for `reply --id`: without it the coordinator cannot answer at all."""
    code = main_with([], fake_runner(reply_help="Usage: orca orchestration reply --body\n"))
    out = report(capsys)
    assert code == 1
    assert "--id" in out


def test_orca_absent_is_pending_not_failure(capsys):
    """No `orca` on PATH is `pending`, not a red gate — the tool is not a dependency."""
    code = main_with([], fake_runner(missing=True))
    out = report(capsys)
    assert code == 0
    assert "pending (orca not found)" in out


def test_live_round_trip_passes(capsys):
    """P2: a real answer with `timedOut: false` is the only passing shape."""
    runner = fake_runner()
    code = main_with(["--from", "term_x", "--dispatch-capability", "dcap_y"], runner)
    out = report(capsys)
    assert code == 0
    assert "P2 live ask/reply round trip: ok" in out
    assert any("--json" in argv for argv in runner.calls)


def test_live_round_trip_timeout_fails(capsys):
    """The exact regression: the ask returns unanswered."""
    runner = fake_runner(ask_result=(1, {"answer": None, "timedOut": True, "cancelled": False}))
    code = main_with(["--from", "term_x", "--dispatch-capability", "dcap_y"], runner)
    out = report(capsys)
    assert code == 1
    assert "timed out" in out


def test_live_round_trip_unparseable_output_fails(capsys):
    """Empty or non-JSON stdout is never an answer."""
    runner = fake_runner(ask_result=(0, "not json"))
    code = main_with(["--from", "term_x", "--dispatch-capability", "dcap_y"], runner)
    out = report(capsys)
    assert code == 1
    assert "unreadable" in out


def main_with(argv: list[str], runner) -> int:
    return orca_reply_probe.main(argv, runner=runner)
