"""Refusals: wire error envelopes and HTTP statuses mapped from reasons."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from graph_works_wire.errors import error_envelope

from graph_works_serve.context import Reply

EXIT: Mapping[str, int] = MappingProxyType({"usage": 1, "unresolved": 7, "workspace": 4, "io": 1, "refused": 1})
STATUS: Mapping[str, int] = MappingProxyType(
    {
        "usage": 400,
        "unresolved": 404,
        "stale-plan": 409,
        # A handler's refusal: a mutation whose plan core refused, blocked, or
        # found in conflict. The guard's `refused` is 401/403, built by the guard.
        "refused": 422,
        "conflict": 422,
        "incomplete": 422,
        "incomplete-apply": 500,
    }
)


@dataclass(frozen=True, slots=True)
class Catch:
    """One `except` clause of a route's CLI twin, in the twin's order."""

    error: type[BaseException]
    reason: str
    exit_code: int


def refusal(
    command: str,
    reason: str,
    message: str,
    *,
    exit_code: int | None = None,
    payload: object = None,
    status: int | None = None,
) -> Reply:
    """Build a refusal reply whose exit code and status preserve interface policy."""
    code = EXIT.get(reason, 1) if exit_code is None else exit_code
    body = error_envelope(command=command, reason=reason, message=message, exit_code=code, payload=payload)
    return Reply(status if status is not None else STATUS.get(reason, 500), body)


def call(command: str, thunk: Callable[[], Reply], catches: Sequence[Catch]) -> Reply:
    """Run *thunk*, mapping the first matching catch and re-raising anything else."""
    try:
        return thunk()
    except BaseException as exc:
        for catch in catches:
            if isinstance(exc, catch.error):
                return refusal(command, catch.reason, str(exc), exit_code=catch.exit_code)
        raise
