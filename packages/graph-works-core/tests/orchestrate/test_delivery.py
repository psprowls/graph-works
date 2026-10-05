from __future__ import annotations

import pytest
from graph_works_core.orchestrate import delivery as dv

LINE = dv.expected_line(task_id="task_1", dispatch_id="ctx_1", key="k", token="0123456789abcdef")


def read(messages, *, source="transcript", exact=True, window=True):
    return {
        "source": source,
        "message_count": len(messages),
        "source_exact": exact,
        "window_complete": window,
        "messages": messages,
    }


def test_token_is_deterministic_hex_and_prompt_bound():
    token = dv.receipt_token("k", "Run x.")
    assert token == dv.receipt_token("k", "Run x.") and len(token) == 16 and int(token, 16) >= 0
    assert token != dv.receipt_token("k", "Run y.") and token != dv.receipt_token("k2", "Run x.")


def test_instruction_names_key_and_token_but_not_ids():
    text = dv.receipt_instruction("k", "0123456789abcdef")
    assert "key=k token=0123456789abcdef" in text and "task=<" in text and "dispatch=<" in text
    assert LINE == "GW-RECEIPT v1 task=task_1 dispatch=ctx_1 key=k token=0123456789abcdef"
    assert LINE not in text


@pytest.mark.parametrize("line", [LINE, f"  {LINE}  ", f"`{LINE}`"])
def test_assistant_text_line_verifies(line):
    messages = [{"role": "user", "text": "p"}, {"role": "assistant", "text": f"{line}\nok"}]
    assert dv.assess(read(messages), LINE) == "verified"


def test_assess_ignores_user_tool_and_placeholder_lines():
    instruction = dv.receipt_instruction("k", "0123456789abcdef")
    messages = [
        {"role": "user", "text": LINE},
        {"role": "tool", "text": LINE},
        {"role": "assistant", "text": instruction},
        {"role": "assistant", "text": f"I will print {LINE} later"},
    ]
    assert dv.assess(read(messages), LINE) == "unverified"


@pytest.mark.parametrize("wrong", ["token=ffffffffffffffff", "task=task_2", "dispatch=ctx_0", "key=other", "v2"])
def test_wrong_fields_do_not_verify(wrong):
    field = wrong.split("=")[0]
    if "=" in wrong:
        bad = " ".join(wrong if part.startswith(field) else part for part in LINE.split(" "))
    else:
        bad = LINE.replace("v1", "v2")
    assert dv.assess(read([{"role": "assistant", "text": bad}]), LINE) == "unverified"


@pytest.mark.parametrize("kwargs", [{"source": "terminal"}, {"source": None}, {"exact": False}])
def test_untrusted_source_is_inconclusive_even_with_a_receipt(kwargs):
    assert dv.assess(read([{"role": "assistant", "text": LINE}], **kwargs), LINE) == "inconclusive"


def test_absent_receipt_in_a_clipped_window_is_inconclusive():
    assert dv.assess(read([{"role": "assistant", "text": "where is my brief?"}], window=False), LINE) == "inconclusive"
    assert dv.assess(read([{"role": "assistant", "text": LINE}], window=False), LINE) == "verified"


def test_worker_asking_for_its_brief_is_unverified():
    messages = [
        {"role": "user", "text": "You are working inside Orca…"},
        {"role": "assistant", "text": "The task block is missing; asking."},
    ]
    assert dv.assess(read(messages), LINE) == "unverified"


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "transcript"},
        {"source_exact": True},
        {"window_complete": True},
        {"messages": []},
    ],
)
def test_missing_read_metadata_is_inconclusive(changes):
    data = read([])
    data.update(changes)
    for key in set(data) - set(changes):
        data.pop(key)
    assert dv.assess(data, LINE) == "inconclusive"


@pytest.mark.parametrize(
    "messages",
    [
        [None],
        ["assistant message"],
        [{"role": "assistant", "text": None}],
        [{"role": "assistant", "text": 123}],
    ],
)
def test_malformed_message_entries_are_inconclusive(messages):
    assert dv.assess(read(messages), LINE) == "inconclusive"


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        {"text": "missing role"},
        {"role": None, "text": "missing role"},
        {"role": "assistant", "text": None},
    ],
)
@pytest.mark.parametrize("position", ["before", "after"])
def test_malformed_entry_anywhere_makes_receipt_inconclusive(malformed, position):
    valid = {"role": "assistant", "text": LINE}
    messages = [malformed, valid] if position == "before" else [valid, malformed]
    assert dv.assess(read(messages), LINE) == "inconclusive"


def test_wrong_metadata_types_are_inconclusive():
    data = read([])
    data["source_exact"] = "true"
    assert dv.assess(data, LINE) == "inconclusive"
