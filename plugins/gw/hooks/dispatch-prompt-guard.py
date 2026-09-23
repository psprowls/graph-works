"""Restore recognizable truncated GW dispatches before Claude processes them.

Standalone Python stdlib only: plugin installations need not have the workspace
packages. Prompt text is data, never a command. Output contains context only.
"""

import json
import os
import re
import subprocess
import sys


HEADER = "You are working inside Orca, a multi-agent IDE. You are a dispatched worker."
TASK_MARKER = re.compile(r"^=== TASK ===(?:\r?\n|$)", re.MULTILINE)
PASTE = re.compile(r'<pasted_content id="([^"\r\n]+)">\r?\n(.*?)\r?\n</pasted_content id="\1">(?:\r?\n)?', re.DOTALL)
TASK_ID = re.compile(r"^Your task ID is: (task_[A-Za-z0-9_-]+)\r?\n", re.MULTILINE)
COMMAND = re.compile(r'^[ \t]*(?:"[^"\r\n]+"|[^ \t\r\n]+)[ \t]+orchestration[ \t]+(?:send|ask)[ \t]+[^\r\n]*', re.MULTILINE)
ENVELOPE_FIELDS = {"version", "dispatch_key", "agent", "model", "reasoning_effort", "placement_argv"}


class Unverified(Exception):
    """A bounded diagnostic, without CLI output or prompt content."""


def require(condition, reason):
    if not condition:
        raise Unverified(reason)


def split_task(prompt):
    marker = TASK_MARKER.search(prompt)
    return (prompt[:marker.start()], prompt[marker.end():]) if marker else (prompt, None)


def without_final_eol(text):
    return re.sub(r"\r?\n\Z", "", text)


def task_identity(before):
    matches = TASK_ID.findall(before)
    # A complete ID line must be in the introductory paragraph, not a quoted
    # example further down the CLI instructions. Duplicates are ambiguous too.
    intro = re.split(r"\r?\n\r?\n", before, maxsplit=1)[0] + "\n"
    require(len(matches) == 1 and TASK_ID.search(intro), "missing or ambiguous task identity")
    require(len(re.findall(r"^Your task ID is:", before, re.MULTILINE)) == 1,
            "ambiguous task identity")
    return matches[0]


def command_ids(before, flag, prefix):
    pattern = re.compile(r"(?:^|[ \t])" + re.escape(flag) + r" (" + prefix + r"[A-Za-z0-9_-]+)(?=[ \t\r\n])")
    # Include a real line terminator, but never manufacture one at a cut EOF.
    values = set()
    for match in COMMAND.finditer(before):
        line = match[0]
        if match.end() < len(before) and before[match.end()] in "\r\n":
            line += "\n"
        values.update(pattern.findall(line))
    return values


def worker_identity(before):
    handles = command_ids(before, "--from", "term_")
    require(len(handles) <= 1, "ambiguous worker identity")
    terminal = os.environ.get("ORCA_TERMINAL_HANDLE")
    if terminal is not None:
        require(re.fullmatch(r"term_[A-Za-z0-9_-]+", terminal), "invalid worker environment identity")
        require(not handles or handles == {terminal}, "conflicting worker identity")
        return terminal
    require(handles, "task ID alone cannot distinguish retries; missing worker identity")
    return next(iter(handles))


def validate_envelope(task):
    # Keep in step with workflow_orca._launch.decode_launch_spec's six-field
    # wire contract, without importing workspace packages into a plugin hook.
    line, separator, body = task.partition("\n")
    require(separator and line.startswith("GW_LAUNCH_V1 "), "missing GW launch envelope")
    try:
        value = json.loads(line[len("GW_LAUNCH_V1 "):])
    except ValueError:
        raise Unverified("invalid GW launch envelope") from None
    require(isinstance(value, dict) and set(value) == ENVELOPE_FIELDS, "invalid GW launch fields")
    require(type(value["version"]) is int and value["version"] == 1, "unsupported GW launch version")
    require(all(isinstance(value[k], str) and value[k].strip() for k in ("dispatch_key", "agent")),
            "invalid GW launch identity")
    require(all(value[k] is None or isinstance(value[k], str) and value[k].strip()
                for k in ("model", "reasoning_effort")), "invalid GW launch preferences")
    require(value["reasoning_effort"] is None or value["model"] is not None, "GW effort requires a model")
    require(isinstance(value["placement_argv"], list) and all(isinstance(v, str) for v in value["placement_argv"]),
            "invalid GW launch placement")
    require(body.strip(), "missing GW task body")


def orca_executable():
    if "ORCA_CLI_COMMAND" in os.environ:
        return os.environ["ORCA_CLI_COMMAND"]
    if os.environ.get("ORCA_DEV_REPO_ROOT"):
        return "orca-dev"
    if sys.platform.startswith("linux") and not os.environ.get("ORCA_TERMINAL_HANDLE"):
        return "orca-ide"
    return "orca"


def recover(prompt):
    before, delivered = split_task(prompt)
    task_id = task_identity(before)
    worker = worker_identity(before)
    try:
        result = subprocess.run(
            [orca_executable(), "orchestration", "dispatch-show",
             "--task", task_id, "--preamble", "--json"],
            capture_output=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        raise Unverified("Orca lookup timed out") from None
    except OSError:
        raise Unverified("selected Orca executable is unavailable") from None
    require(result.returncode == 0, "Orca lookup failed")
    try:
        response = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise Unverified("Orca returned invalid JSON") from None
    require(isinstance(response, dict) and response.get("ok") is True, "Orca did not confirm the lookup")
    data = response.get("result")
    require(isinstance(data, dict), "invalid Orca result")
    dispatch = data.get("dispatch")
    require(isinstance(dispatch, dict), "missing dispatch record")
    require(dispatch.get("task_id") == task_id, "wrong task in dispatch record")
    dispatch_id = dispatch.get("id")
    require(isinstance(dispatch_id, str) and re.fullmatch(r"ctx_[A-Za-z0-9_-]+", dispatch_id),
            "invalid dispatch identity")
    require(dispatch.get("status") == "dispatched" and not dispatch.get("completed_at")
            and not dispatch.get("revoked_at"), "dispatch is not live")
    require(dispatch.get("assignee_handle") == worker, "dispatch belongs to a different worker attempt")
    ids = command_ids(before, "--dispatch-id", "ctx_")
    require(not ids or ids == {dispatch_id}, "dispatch attempt changed")
    full = data.get("preamble")
    require(isinstance(full, str) and full.split("\n", 1)[0].removesuffix("\r") == HEADER,
            "invalid recovered preamble")
    recovered_before, expected = split_task(full)
    require(task_identity(recovered_before) == task_id, "wrong task in recovered preamble")
    recovered_workers = command_ids(recovered_before, "--from", "term_")
    recovered_dispatches = command_ids(recovered_before, "--dispatch-id", "ctx_")
    require(not recovered_workers or recovered_workers == {worker}, "wrong worker in recovered preamble")
    require(not recovered_dispatches or recovered_dispatches == {dispatch_id}, "wrong attempt in recovered preamble")
    require(expected is not None, "missing recovered TASK block")
    validate_envelope(expected)
    if delivered is not None and without_final_eol(delivered) == without_final_eol(expected):
        return
    require(delivered is None or expected.startswith(without_final_eol(delivered)),
            "task text conflicts with the stored dispatch")
    note = (f"The submitted Orca dispatch was truncated (task {task_id}, dispatch {dispatch_id}). "
            "The complete intended task follows.")
    if len(note) + 2 + len(full) > 10000:
        note += " Please read the full hook-output file supplied by Claude before acting; the preview is incomplete."
    emit(note + "\n\n" + full)


def emit(context, diagnostic=False):
    result = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}
    if diagnostic:
        result["systemMessage"] = context
    print(json.dumps(result, ensure_ascii=True))


def main():
    try:
        data = json.loads(sys.stdin.buffer.read())
    except (ValueError, UnicodeError):
        return
    if not isinstance(data, dict) or data.get("hook_event_name") != "UserPromptSubmit":
        return
    prompt = data.get("prompt")
    if not isinstance(prompt, str):
        return
    wrapper = PASTE.fullmatch(prompt)
    if wrapper:
        prompt = wrapper[2]
    if prompt.split("\n", 1)[0].removesuffix("\r") != HEADER:
        return
    try:
        recover(prompt)
    except Unverified as error:
        emit(f"Dispatch recovery was not verified: {error}. Do not guess incomplete work; request the full task.", True)


if __name__ == "__main__":
    main()
