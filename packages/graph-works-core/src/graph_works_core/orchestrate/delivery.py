"""Delivery receipts: did the worker receive the whole frozen brief?

Submission (a heartbeat or any transcript message) says the prompt was sent;
delivery says its tail arrived. The token sits only at the end of the frozen
brief, so a worker that lost a prefix-cut prompt cannot echo it. A positive,
exact, attempt-bound line verifies; silence in a clipped window proves nothing.
"""

from __future__ import annotations

import hashlib

from graph_works_core.orchestrate.orca_port import OrcaRead

RECEIPT_VERSION: int = 1
DELIVERY_OUTCOMES: tuple[str, ...] = ("verified", "unverified", "inconclusive", "skipped")
_PREFIX = f"GW-RECEIPT v{RECEIPT_VERSION}"


def receipt_token(key: str, prompt: str) -> str:
    return hashlib.sha256(f"{key}\n{prompt}".encode()).hexdigest()[:16]


def receipt_instruction(key: str, token: str) -> str:
    return (
        "Delivery receipt: before any other action, your first reply must contain this line on its own, "
        "with the task ID and dispatch ID from your Orca instructions above filled in:\n"
        f"{_PREFIX} task=<task ID> dispatch=<dispatch ID> key={key} token={token}"
    )


def expected_line(*, task_id: str, dispatch_id: str, key: str, token: str) -> str:
    return f"{_PREFIX} task={task_id} dispatch={dispatch_id} key={key} token={token}"


def assess(read: OrcaRead, expected: str) -> str:
    source = read.get("source")
    source_exact = read.get("source_exact")
    window_complete = read.get("window_complete")
    messages = read.get("messages")
    message_count = read.get("message_count")
    if (
        source != "transcript"
        or source_exact is not True
        or type(window_complete) is not bool
        or not isinstance(messages, list)
        or type(message_count) is not int
        or message_count != len(messages)
    ):
        return "inconclusive"

    for message in messages:
        if not isinstance(message, dict):
            return "inconclusive"
        role = message.get("role")
        text = message.get("text")
        if not isinstance(role, str) or not role.strip() or not isinstance(text, str):
            return "inconclusive"

    for message in messages:
        if message["role"] != "assistant":
            continue
        if any(line.strip().strip("`").strip() == expected for line in message["text"].splitlines()):
            return "verified"
    return "unverified" if window_complete else "inconclusive"
