"""`gw` writes LF-terminated stdout on every platform.

`sys.stdout` is built by the interpreter with `newline=None`, which translates every `"\n"`
written through it to `os.linesep` -- CRLF on Windows. `typer.echo` writes through that stream,
so every `--json` payload the CLI emits was CRLF on Windows even though no `open()` call is
involved anywhere near it.

The process test below is the automated form of checkpoint F6 of the manual Windows
verification run. It has to cross a real process boundary: `typer.testing.CliRunner`
substitutes its own text wrapper for `sys.stdout`, so the whole in-process suite is
structurally blind to this defect.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import helpers
from graph_works_cli.cli import force_lf_newlines

GOLDEN = Path(__file__).parent / "fixtures" / "surface.golden.json"


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


def test_describe_surface_json_redirected_to_a_file_is_lf_and_matches_the_golden(tmp_path: Path) -> None:
    """Checkpoint F6, automated: redirected `--json` stdout is LF and byte-identical to the golden."""
    captured = tmp_path / "surface.json"
    with captured.open("wb") as handle:
        completed = subprocess.run(
            [str(helpers.console_script("gw")), "util", "describe-surface", "--json"],
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")

    # Binary, never `read_text`: universal-newline translation on the way *in* would silently
    # un-break the very thing under test.
    written = captured.read_bytes()
    assert b"\r\n" not in written
    assert written == GOLDEN.read_bytes()
