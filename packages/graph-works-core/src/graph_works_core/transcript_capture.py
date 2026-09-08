"""The `SessionEnd` transcript-capture hook body.

Ported from the bash script this replaced
(`git show 6c1c5763:plugins/graph-works/hooks/examples/session-end-transcript-capture.sh`,
the vendored subtree's own copy, at the last commit before it was rewritten as
a Python shim) (bug-windows-hook-command-unexecutable / D-066, D-068): that
bash script, invoked via a `VAR=value bash <path>` command, could not run under
a native Windows platform shell. As a typed module it is unit-testable in process
and sits inside this package's branch-coverage gate, which the bash script
never did -- its only exercise was two slow wheel/sdist subprocess smoke
tests.

`main` is the test seam: dependency-injected on `stdin`/`env`, never reading
`sys.stdin`/`os.environ` itself. It never raises -- fail-open is the
contract this hook has always had (the bash script's `trap ... ERR; exit 0`),
now made explicit: every exception is caught, traced with its message, and
`main` returns 0.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import traceback
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

GUARD_ENV = "GRAPH_WORKS_TRANSCRIPT_CAPTURE_GUARD"
TRACE_LOG_ENV = "GRAPH_WORKS_TRANSCRIPT_CAPTURE_TRACE_LOG"

#: `pointer["phase"]` -> the reference filename's ordinal prefix. Matches the
#: `.sh`'s embedded mapping exactly.
_ORDINALS = {"design": "01", "plan": "02", "execute": "03", "finish": "04"}


def _default_trace_log() -> Path:
    """`/tmp/...` on POSIX (a no-op change there), `%TEMP%\\...` on Windows --
    where the bash script's hardcoded `/tmp/claude-hooks/...` silently wrote
    to an unreachable `C:\\tmp\\...` (design M6)."""
    return Path(tempfile.gettempdir()) / "claude-hooks" / "transcript-capture-trace.log"


def _trace(env: Mapping[str, str], session: str, event: str, reason: str = "") -> None:
    log_path = Path(env.get(TRACE_LOG_ENV) or _default_trace_log())
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        suffix = f" | {reason}" if reason else ""
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{stamp} | session-end-transcript-capture | session={session} | {event}{suffix}\n")
    except OSError:
        pass


def _copy_transcript(env: Mapping[str, str], session: str, transcript_path: Path) -> None:
    from work_tracker_okf.paths import parse_item_path

    from graph_works_core.workspace.discovery import resolve
    from graph_works_core.workspace.provenance import ACTIVE_WORK_FILENAME

    layout = resolve(cwd=Path.cwd(), environ=env)
    pointer_path = layout.cache_dir / ACTIVE_WORK_FILENAME
    if not pointer_path.exists():
        _trace(env, session, "skip", "no-pointer")
        return

    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    work_path = pointer["path"]
    phase = pointer["phase"]
    location = parse_item_path(work_path)
    if location is None or not (layout.bundle_dir / location.page).is_file():
        _trace(env, session, "skip", "invalid-path")
        return

    ordinal = _ORDINALS[phase]
    references = layout.bundle_dir / location.path / "references"
    references.mkdir(parents=True, exist_ok=True)

    main_dest = references / f"{ordinal}-{phase}-transcript.jsonl"
    shutil.copy2(transcript_path, main_dest)
    copied = [str(main_dest)]

    sidechain_dir = transcript_path.with_suffix("") / "subagents"
    if sidechain_dir.is_dir():
        for agent_file in sorted(sidechain_dir.glob("agent-*.jsonl")):
            agent_id = agent_file.stem.removeprefix("agent-")
            dest = references / f"{ordinal}-{phase}-transcript-subagent-{agent_id}.jsonl"
            shutil.copy2(agent_file, dest)
            copied.append(str(dest))

    _trace(env, session, "copied", ",".join(copied))


def main(stdin: TextIO, env: Mapping[str, str]) -> int:
    """Read the `SessionEnd` payload from *stdin*, copy the transcript (and
    any subagent sidechains) into the active work item's `references/`.

    Never raises. Every failure -- malformed input, no active work, an
    exception mid-copy -- is traced and answered with `0`: this hook must
    never block session end.
    """
    session = "?"
    try:
        if env.get(GUARD_ENV, "1") == "0":
            _trace(env, session, "skip", "guard=0")
            return 0

        try:
            payload = json.load(stdin)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _trace(env, session, "error", f"malformed-json:{exc}")
            return 0
        if not isinstance(payload, dict):
            _trace(env, session, "error", "malformed-json:not-an-object")
            return 0

        session = str(payload.get("session_id") or "")[:8] or "?"

        transcript_value = payload.get("transcript_path")
        transcript_path = Path(transcript_value) if transcript_value else None
        if transcript_path is None or not transcript_path.is_file():
            _trace(env, session, "skip", "no-transcript")
            return 0

        _trace(env, session, "enter")
        _copy_transcript(env, session, transcript_path)
        return 0
    except BaseException as exc:  # fail-open: never block session end
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        _trace(env, session, "error", detail)
        return 0
