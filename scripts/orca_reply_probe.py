"""`just orca-reply-probe` — the standing guard on Orca's ask/reply correlation.

Repo tooling: ruff-excluded, part of no package, matching the rest of `scripts/`. The
recipe sits outside `just check`, which must stay offline and Orca-free.

Filed as a correlation defect
(`work/.../bug-reply-never-reaches-asking-worker`) and refuted at design time: on orca
1.4.191 a blocked `orca orchestration ask` does receive its answer. That refutation is a
point-in-time fact, and Orca updates on its own schedule — so this probe exists to make
the next regression ours to find, instead of discovering it through a lost human answer.

Two assertions, and the distinction between them is deliberate:

- **P1, static and always runnable** — `ask --help` advertises `--resume` and
  `reply --help` advertises `--id`. Offline, cheap, catches a CLI surface change.
- **P2, live** — a real ask -> reply -> answer round trip. It needs an active Dispatch
  *and* a second party willing to answer, so outside one it reports `skipped (no active
  dispatch)` rather than passing vacuously. There is no environment variable carrying a
  dispatch capability: the caller passes `--from` and `--dispatch-capability` from its
  own dispatch preamble, and their absence is what "no active dispatch" means here.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

Runner = Callable[[list[str]], "tuple[int, str]"]

#: Each `(subcommand, flag)` P1 requires. `--resume` is how a timed-out ask is re-entered
#: without filing a duplicate question; `--id` is how the coordinator answers one at all.
#: Losing either one silently is the CLI-surface regression this half exists to catch.
REQUIRED_FLAGS: tuple[tuple[str, str], ...] = (("ask", "--resume"), ("reply", "--id"))

#: Long enough for a human in the loop, short enough that an unattended run is not stuck
#: for the server-side maximum of 1,800,000 ms.
DEFAULT_TIMEOUT_MS = 120_000


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    failed: bool = False

    def note(self, line: str) -> None:
        self.lines.append(line)

    def fail(self, line: str) -> None:
        self.lines.append(line)
        self.failed = True


def _subprocess_runner(argv: list[str]) -> tuple[int, str]:
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    return completed.returncode, completed.stdout + completed.stderr


def probe_surface(runner: Runner, report: Report) -> bool:
    """P1 — the two flags the ask/reply contract rests on. Returns False when `orca` is absent."""
    missing: list[str] = []
    for subcommand, flag in REQUIRED_FLAGS:
        try:
            _, text = runner(["orca", "orchestration", subcommand, "--help"])
        except (FileNotFoundError, OSError):
            report.note("pending (orca not found)")
            return False
        if flag not in text:
            missing.append(f"orca orchestration {subcommand} does not advertise {flag}")
    if missing:
        report.fail("P1 ask/reply CLI surface: FAIL")
        for line in missing:
            report.fail(f"  {line}")
    else:
        report.note("P1 ask/reply CLI surface: ok")
    return True


def probe_round_trip(runner: Runner, sender: str, capability: str, timeout_ms: int, report: Report) -> None:
    """P2 — one real ask, answered by a second party, returning with `timedOut: false`."""
    if not sender or not capability:
        report.note("P2 live ask/reply round trip: skipped (no active dispatch)")
        return
    argv = [
        "orca", "orchestration", "ask",
        "--from", sender,
        "--dispatch-capability", capability,
        "--question", "orca-reply-probe: reply with anything to confirm the ask/reply path is live.",
        "--timeout-ms", str(timeout_ms),
        "--json",
    ]
    try:
        code, text = runner(argv)
    except (FileNotFoundError, OSError):
        report.note("P2 live ask/reply round trip: pending (orca not found)")
        return
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        # Empty stdout is never an answer, and neither is anything that does not parse.
        report.fail(f"P2 live ask/reply round trip: FAIL (unreadable response, exit {code})")
        return
    if payload.get("timedOut"):
        report.fail("P2 live ask/reply round trip: FAIL (timed out — the answer never reached the caller)")
        return
    if payload.get("cancelled"):
        report.fail("P2 live ask/reply round trip: FAIL (cancelled — connection lost before the answer)")
        return
    answer = payload.get("answer")
    if code != 0 or not answer:
        report.fail(f"P2 live ask/reply round trip: FAIL (exit {code}, answer {answer!r})")
        return
    report.note(f"P2 live ask/reply round trip: ok (answer {answer!r})")


def main(argv: list[str] | None = None, *, runner: Runner = _subprocess_runner) -> int:
    parser = argparse.ArgumentParser(description="Probe Orca's coordinator->worker reply path.")
    parser.add_argument("--from", dest="sender", default="")
    parser.add_argument("--dispatch-capability", dest="capability", default="")
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    args = parser.parse_args(argv)

    report = Report()
    if probe_surface(runner, report):
        probe_round_trip(runner, args.sender, args.capability, args.timeout_ms, report)

    for line in report.lines:
        print(line)
    return 1 if report.failed else 0


if __name__ == "__main__":  # pragma: no cover -- exercised through main() in the tests
    raise SystemExit(main())
