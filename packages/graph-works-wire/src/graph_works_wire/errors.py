"""The refusal envelope every interface emits: `gw work --json` and graph-works-serve.

Moved verbatim from `graph_works_cli.work_cli.rendering` so the CLI and the
sidecar cannot disagree about its shape. No success projection carries a
top-level `error` key, so `"error" in doc` is a collision-free discriminator.
"""

from __future__ import annotations

#: The closed `reason` vocabulary (D-004 §3.2). A new exit site picks one
#: deliberately -- there is no catch-all.
REASONS: frozenset[str] = frozenset(
    {
        "refused",
        "incomplete-apply",
        "conflict",
        "incomplete",
        "usage",
        "workspace",
        "unresolved",
        "not-a-repo",
        "io",
        # serve's apply: the re-planned digest differs from the one the
        # client saw, or the plan's `as_of` is outside the window.
        "stale-plan",
    }
)


def error_envelope(*, command: str, reason: str, message: str, exit_code: int, payload: object) -> dict[str, object]:
    """The single-key, structurally unmistakable refusal document."""
    if reason not in REASONS:
        raise ValueError(f"{reason!r} is not in the closed reason vocabulary")
    return {
        "error": {
            "command": command,
            "reason": reason,
            "message": message,
            "exit_code": exit_code,
            "payload": payload,
        }
    }
