"""Pure app-signal classification for manifest info dicts.

classify() consumes a manifest info dict (produced
by code_graph_io.packages._read_pyproject / _read_package_json) and returns
the kind, app_kind, and the sorted list of signals that triggered the
classification. The function is pure — no SQLite, no subprocess, no
logging — so it is safe to call from the emit loop without coupling
the schema layer to I/O.

Framework precedence: when multiple framework signals would
match, _FRAMEWORK_PRECEDENCE selects the winner. The order matches the
implementation in code_graph_io.queries._VALID_APP_KINDS — keep both in sync.
"""

from __future__ import annotations

from pathlib import Path

from code_graph_io.queries import _VALID_APP_KINDS

# Priority order — first match wins for app_kind selection.
# electron is placed before spa so Electron+Vite apps resolve to
# 'electron' not 'spa'. Keep in sync with _VALID_APP_KINDS in queries.py.
_FRAMEWORK_PRECEDENCE = ("nextjs", "expo", "electron", "spa")


def classify(
    info: dict,
    pkg_dir: Path,
) -> tuple[str, str | None, list[str]]:
    """Return (kind, app_kind, app_signals) for a manifest info dict.

    Args:
        info: Manifest info dict produced by `_read_pyproject` or
            `_read_package_json`. Must include a `"language"` key; the
            relevant signal keys are `"scripts_present"` (python),
            `"bin_present"` (javascript), and `"dependencies"` (list).
        pkg_dir: Filesystem directory of the manifest. Used only for the
            vite/index.html spa check.

    Returns:
        A tuple `(kind, app_kind, app_signals)` where:
        - `kind` is `"package"` or `"app"`.
        - `app_kind` is one of `_VALID_APP_KINDS` when kind="app", else None.
        - `app_signals` is the sorted list of every matched signal.

    The function is pure: no SQLite, no subprocess, no logging.
    """
    signals: list[str] = []
    lang = info.get("language", "")

    if lang == "python":
        if info.get("scripts_present"):
            signals.append("cli")
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
