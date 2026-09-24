"""Pure app-signal classification for manifest info dicts.

classify() consumes a manifest info dict (produced
by code_graph_io.packages._read_pyproject / _read_package_json) and returns
the kind, app_kind, and the sorted list of signals that triggered the
classification. classify() never touches SQLite, spawns a subprocess or logs. Its only I/O is
bounded reads under pkg_dir.

Framework precedence: when multiple framework signals would
match, _FRAMEWORK_PRECEDENCE selects the winner. The order matches the
implementation in code_graph_io.queries._VALID_APP_KINDS — keep both in sync.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from code_graph_io.queries import _VALID_APP_KINDS

# Priority order — first match wins for app_kind selection.
# electron is placed before spa so Electron+Vite apps resolve to
# 'electron' not 'spa'. A Python server beats the 'cli' default.
# Keep in sync with _VALID_APP_KINDS in queries.py.
_FRAMEWORK_PRECEDENCE = ("nextjs", "expo", "electron", "server", "spa")

# A Python server needs BOTH a server dependency AND entry-point evidence:
# a dependency alone would turn every library that depends on Flask into an App.
_PY_SERVER_DEPS = frozenset(
    {"fastapi", "flask", "starlette", "django", "sanic", "uvicorn", "gunicorn", "hypercorn", "daphne"}
)
_PY_SERVER_ENTRY_FILES = ("app.py", "main.py", "server.py", "wsgi.py", "asgi.py", "manage.py", "__main__.py")
_PY_SERVER_EVIDENCE_RE = re.compile(
    rb"\b(?:FastAPI|Flask|Starlette|Sanic)\s*\("
    rb"|\buvicorn\.run\s*\("
    rb"|\bget_(?:asgi|wsgi)_application\b"
    rb"|\bexecute_from_command_line\b"
)
_EVIDENCE_READ_LIMIT = 64 * 1024
_PEP508_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)")


def _pep503_name(raw: str) -> str | None:
    match = _PEP508_NAME_RE.match(raw)
    return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else None


def _python_server_evidence(info: dict[str, Any], pkg_dir: Path) -> bool:
    """A runtime server dependency plus a top-level entry file that starts a server.

    Reads at most the first 64 KiB of each candidate entry file, as bytes.
    An unreadable file (missing, a directory, a permission error) is simply
    no evidence. This never raises.
    """
    names = {
        name
        for raw in info.get("dependencies") or []
        if isinstance(raw, str) and (name := _pep503_name(raw)) is not None
    }
    if not names & _PY_SERVER_DEPS:
        return False
    for filename in _PY_SERVER_ENTRY_FILES:
        try:
            with (pkg_dir / filename).open("rb") as handle:
                head = handle.read(_EVIDENCE_READ_LIMIT)
        except OSError:
            continue
        if _PY_SERVER_EVIDENCE_RE.search(head):
            return True
    return False


def classify(
    info: dict[str, Any],
    pkg_dir: Path,
) -> tuple[str, str | None, list[str]]:
    """Return (kind, app_kind, app_signals) for a manifest info dict.

    Args:
        info: Manifest info dict produced by `_read_pyproject` or
            `_read_package_json`. Must include a `"language"` key; the
            relevant signal keys are `"scripts_present"` (python),
            `"bin_present"` (javascript), and `"dependencies"` (list).
        pkg_dir: Filesystem directory of the manifest. Used for the
            vite/index.html spa check and the bounded Python server entry-file read.

    Returns:
        A tuple `(kind, app_kind, app_signals)` where:
        - `kind` is `"package"` or `"app"`.
        - `app_kind` is one of `_VALID_APP_KINDS` when kind="app", else None.
        - `app_signals` is the sorted list of every matched signal.

    Never touches SQLite, spawns a subprocess or logs; its only I/O is bounded
    reads under pkg_dir.
    """
    signals: list[str] = []
    lang = info.get("language", "")

    if lang == "python":
        if info.get("scripts_present"):
            signals.append("cli")
        if _python_server_evidence(info, pkg_dir):
            signals.append("server")
    elif lang == "javascript":
        if info.get("bin_present"):
            signals.append("cli")
        deps = info.get("dependencies") or []
        if "next" in deps:
            signals.append("nextjs")
        if "expo" in deps:
            signals.append("expo")
        if "electron" in deps:
            signals.append("electron")
        if "vite" in deps and (pkg_dir / "index.html").exists():
            signals.append("spa")

    if not signals:
        return "package", None, []

    signals.sort()

    # Default to "cli"; framework signals override per _FRAMEWORK_PRECEDENCE.
    app_kind: str = "cli"
    for framework in _FRAMEWORK_PRECEDENCE:
        if framework in signals:
            app_kind = framework
            break

    # write-time gate: catch typos before the value reaches the DB.
    if app_kind not in _VALID_APP_KINDS:
        raise ValueError(
            f"classify produced app_kind={app_kind!r} which is not in _VALID_APP_KINDS={sorted(_VALID_APP_KINDS)}"
        )

    return "app", app_kind, signals
