"""Pure launch argv, durable spec envelope, and receipt verification.

The versioned first line is shared with auto-drive. The remaining text is the
unaltered human prompt. Orca receipt fields use `effort`, not our dispatch's
`reasoning_effort`; provider strings remain opaque throughout.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from subagents_io.backend import BackendError
from subagents_io.dispatch import PlannedDispatch

LAUNCH_SPEC_PREFIX = "GW_LAUNCH_V1 "


def launch_preferences(dispatch: PlannedDispatch) -> list[str]:
    """Explicit per-dispatch selections, with no silent effort fallback."""
    if dispatch.reasoning_effort is not None and dispatch.model is None:
        raise BackendError("Set a model or clear reasoning_effort before launching with Orca.")
    argv = ["--agent", dispatch.agent]
    if dispatch.model is not None:
        argv += ["--model", dispatch.model]
    if dispatch.reasoning_effort is not None:
        argv += ["--effort", dispatch.reasoning_effort]
    return argv


def encode_launch_spec(dispatch: PlannedDispatch, *, placement_argv: list[str]) -> str:
    """Freeze launch choices in the task before starting any worker."""
    launch_preferences(dispatch)
    envelope = {
        "version": 1,
        "dispatch_key": dispatch.key,
        "agent": dispatch.agent,
        "model": dispatch.model,
        "reasoning_effort": dispatch.reasoning_effort,
        "placement_argv": placement_argv,
    }
    return LAUNCH_SPEC_PREFIX + json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n" + dispatch.prompt


def decode_launch_spec(spec: str) -> dict[str, object]:
    """Validate the durable envelope; never infer missing launch choices."""
    message = "Invalid or missing launch envelope; inspect the full task spec before recovery."
    line, separator, _ = spec.partition("\n")
    if not separator or not line.startswith(LAUNCH_SPEC_PREFIX):
        raise BackendError(message)
    try:
        value = json.loads(line[len(LAUNCH_SPEC_PREFIX) :])
    except json.JSONDecodeError as exc:
        raise BackendError(message) from exc
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise BackendError(message)
    if set(value) != {"version", "dispatch_key", "agent", "model", "reasoning_effort", "placement_argv"}:
        raise BackendError(message)
    if any(not isinstance(value[k], str) or not value[k].strip() for k in ("dispatch_key", "agent")):
        raise BackendError(message)
    if any(
        value[k] is not None and (not isinstance(value[k], str) or not value[k].strip())
        for k in ("model", "reasoning_effort")
    ):
        raise BackendError(message)
    if value["reasoning_effort"] is not None and value["model"] is None:
        raise BackendError(message)
    if not isinstance(value["placement_argv"], list) or not all(isinstance(v, str) for v in value["placement_argv"]):
        raise BackendError(message)
    return cast(dict[str, object], value)


def check_launch_receipt(request: Mapping[str, object], receipt: Mapping[str, object]) -> str | None:
    """Return an actionable reason when explicit choices lack matching proof.

    `receipt` is the launch block, not the enclosing worker-start response.
    Null model/effort ask for agent defaults and make no claims about them.
    """
    for part in ("requested", "effective"):
        proof = receipt.get(part)
        if not isinstance(proof, Mapping):
            return f"Launch configuration unverified: missing launch.{part}; inspect worker-show before recovery."
        for field, receipt_field in (("agent", "agent"), ("model", "model"), ("reasoning_effort", "effort")):
            selected = request.get(field)
            if selected is not None and proof.get(receipt_field) != selected:
                return (
                    f"Launch configuration unverified: launch.{part}.{receipt_field} "
                    f"was {proof.get(receipt_field)!r}, expected {selected!r}; inspect worker-show before recovery."
                )
    return None
