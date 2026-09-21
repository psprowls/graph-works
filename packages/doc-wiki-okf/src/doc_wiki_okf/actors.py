"""Actor strings for `generated.by` and `verified[].by` (OKF v0.2 §7).

okf-io recognises three forms -- `human:<id>`, `process:<id>`, and
`<producer>/<version>` -- and reports anything else as `trust.actor-convention`.
The defaults this package and its callers used to stamp (`agent:doc-wiki-okf`,
bare `human`) were the second kind of string, so every page written through
them carried a warning.

`producer_actor` names a piece of software; `human_actor` names the person at
the keyboard, read from git so it needs no configuration of its own.
"""

from __future__ import annotations

import getpass
import re
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_NOREPLY = re.compile(r"^\d+\+(?P<user>[^@]+)@users\.noreply\.github\.com$")
_UNSAFE = re.compile(r"[^a-z0-9._-]+")

#: Timeout for one `git config` read. A hung git must not hang a CLI command.
_GIT_TIMEOUT_SECONDS = 5


def producer_actor(package: str) -> str:
    """`<package>/<version>` from the installed distribution's metadata.

    An uninstalled package (a bare checkout on `PYTHONPATH`) has no metadata; it
    is stamped `<package>/0` rather than failing, so the string still matches the
    convention.
    """
    try:
        return f"{package}/{version(package)}"
    except PackageNotFoundError:
        return f"{package}/0"


def _git_config(key: str, cwd: Path | None) -> str:
    """One `git config --get <key>` value, or `""` when git cannot answer."""
    try:
        completed = subprocess.run(
            ["git", "config", "--get", key],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _slug(text: str) -> str:
    return _UNSAFE.sub("-", text.strip().lower()).strip("-")


def human_handle(cwd: Path | None = None) -> str:
    """The person's handle, read from git in *cwd* (default: the process cwd).

    In order: the local part of `user.email` (a GitHub no-reply address yields
    the GitHub login, and a `+tag` suffix is dropped), then `user.name`
    slugified, then the login name of the operating-system user, then
    `unknown`. It never raises.
    """
    email = _git_config("user.email", cwd)
    if email:
        noreply = _NOREPLY.match(email)
        local = noreply["user"] if noreply else email.split("@", 1)[0]
        handle = _slug(local.split("+", 1)[0])
        if handle:
            return handle
    handle = _slug(_git_config("user.name", cwd))
    if handle:
        return handle
    try:
        handle = _slug(getpass.getuser())
    except (OSError, KeyError, ImportError):
        handle = ""
    return handle or "unknown"


def human_actor(cwd: Path | None = None) -> str:
    """`human:<handle>` for the person running the command."""
    return f"human:{human_handle(cwd)}"


__all__ = ["human_actor", "human_handle", "producer_actor"]
