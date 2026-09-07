"""`work-tracker-okf` writes LF-terminated stdout on every platform.

`sys.stdout` is built by the interpreter with `newline=None`, which translates every `"\n"`
written through it to `os.linesep` -- CRLF on Windows. `typer.echo` writes through that stream,
so every `--json` payload the CLI emits was CRLF on Windows even though no `open()` call is
involved anywhere near it. Mirrors `graph-works-cli`'s `test_stdout_newlines.py`.

The process test below has to cross a real process boundary: `typer.testing.CliRunner`
substitutes its own text wrapper for `sys.stdout`, so the whole in-process suite is
structurally blind to this defect.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import helpers
from work_tracker_okf.cli import force_lf_newlines

FIXTURE = Path(__file__).parent / "fixtures" / "path_native"


def test_force_lf_newlines_pins_a_translating_stream_and_ignores_the_rest() -> None:
    """A `TextIOWrapper` is reconfigured to LF; anything else is left alone, not raised on."""
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="utf-8", newline="\r\n")
    stream.write("a\nb\n")
    stream.flush()
    assert buffer.getvalue() == b"a\r\nb\r\n"

    force_lf_newlines(stream, io.StringIO(), None)

    buffer.seek(0)
    buffer.truncate()
    stream.write("a\nb\n")
    stream.flush()
    assert buffer.getvalue() == b"a\nb\n"


def test_status_json_redirected_to_a_file_is_lf(tmp_path: Path) -> None:
    """`status --json` redirected to a file has no CRLF and parses as non-empty JSON."""
    captured = tmp_path / "status.json"
    with captured.open("wb") as handle:
        completed = subprocess.run(
            [str(helpers.console_script("work-tracker-okf")), "status", str(FIXTURE), "--json"],
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")

    # Binary, never `read_text`: universal-newline translation on the way *in* would silently
    # un-break the very thing under test.
    written = captured.read_bytes()
    assert b"\r\n" not in written
    payload = json.loads(written)
    assert payload
