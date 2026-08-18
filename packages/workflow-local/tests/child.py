"""A scriptable test worker that speaks the child-side contract.

Each argv item after the script path is one instruction:

    heartbeat:<phase>   append a heartbeat
    question:<text>     append a question, then block until the next unread reply
    escalate:<subject>  append an escalation
    done:<outcome>      append a worker_done with that outcome, then exit 0
    say:<text>          print to stdout (which the backend captures)
    garbage             append a line that is not JSON
    blank               append an empty line
    array               append a JSON value that is not a JSON object
    unknown             append a well-formed line with an unregistered kind
    sleep:<seconds>     sleep, so the parent can kill or stop this process
    exit:<code>         exit immediately with that code, emitting nothing

`question` correlates by ORDER, not by token: the child never learns its own
handle, so it takes the next reply line it has not already consumed. That is
the contract the README documents.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

EVENTS = Path(os.environ["SUBAGENT_EVENT_LOG"])
REPLIES = Path(os.environ["SUBAGENT_REPLY_LOG"])
KEY = os.environ["SUBAGENT_DISPATCH_KEY"]

_replies_consumed = 0


def append(line: str) -> None:
    with EVENTS.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


def emit(payload: dict) -> None:
    append(json.dumps(payload))


def next_reply(timeout_s: float = 15.0) -> str | None:
    """Block for the next reply line this child has not already consumed."""
    global _replies_consumed
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        lines = [ln for ln in REPLIES.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) > _replies_consumed:
            payload = json.loads(lines[_replies_consumed])
            _replies_consumed += 1
            return str(payload["answer"])
        time.sleep(0.02)
    return None


def main(program: list[str]) -> int:
    for step in program:
        verb, _, arg = step.partition(":")
        if verb == "heartbeat":
            emit({"kind": "heartbeat", "phase": arg or None})
        elif verb == "question":
            emit({"kind": "question", "question": arg, "options": ["a", "b"]})
            answer = next_reply()
            print(f"answered: {answer}", flush=True)
        elif verb == "escalate":
            emit({"kind": "escalation", "subject": arg, "body": f"{KEY} is blocked"})
        elif verb == "done":
            emit(
                {
                    "kind": "worker_done",
                    "outcome": arg,
                    "summary": f"{KEY} finished",
                    "files_modified": ["a.py"],
                    "report_path": None,
                }
            )
            return 0
        elif verb == "say":
            print(arg, flush=True)
        elif verb == "garbage":
            append("{this is not json")
        elif verb == "blank":
            append("")
        elif verb == "array":
            append(json.dumps([1, 2, 3]))
        elif verb == "unknown":
            emit({"kind": "gossip", "text": "not in EVENT_KINDS"})
        elif verb == "sleep":
            time.sleep(float(arg))
        elif verb == "exit":
            return int(arg)
        else:
            raise SystemExit(f"unknown instruction: {step}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
