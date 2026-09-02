"""`doc-wiki-okf` writes LF-terminated stdout on every platform.

`sys.stdout` is built by the interpreter with `newline=None`, which translates every `"\n"`
written through it to `os.linesep` -- CRLF on Windows. `typer.echo` writes through that stream,
so every `--json` payload the CLI emits was CRLF on Windows even though no `open()` call is
involved anywhere near it. Mirrors `graph-works-cli`'s `test_stdout_newlines.py`.

The process test below has to cross a real process boundary: `typer.testing.CliRunner`
substitutes its own text wrapper for `sys.stdout`, so the whole in-process suite is
structurally blind to this defect. The bundle it reads is seeded in-process through
`CliRunner` -- only the measured `proposals --json` command crosses the process boundary.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import helpers
from doc_wiki_okf.cli import app, force_lf_newlines
from proposal_helpers import TODAY
from typer.testing import CliRunner

runner = CliRunner()
DAY = TODAY.isoformat()


def _seed_bundle(root: Path) -> None:
    assert runner.invoke(app, ["init", str(root), "--today", DAY]).exit_code == 0
    result = runner.invoke(
        app,
        [
            "proposal",
            "file",
            str(root),
            "--lane",
            "adr",
            "--title",
            "Bulk Write Staging Protocol",
            "--description",
            "Why this page.",
            "--id",
            "src-a",
            "--resource",
            "sources/a.md",
            "--rationale",
            "It settles it.",
            "--evidence",
            "Staging precedes any live write.",
            "--by",
            "agent:test",
            "--today",
            DAY,
        ],
    )
    assert result.exit_code == 0, result.output


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


def test_proposals_json_redirected_to_a_file_is_lf(tmp_path: Path) -> None:
    """`proposals --json` redirected to a file has no CRLF and parses as non-empty JSON."""
    root = tmp_path / "bundle"
    _seed_bundle(root)

    captured = tmp_path / "proposals.json"
    with captured.open("wb") as handle:
        completed = subprocess.run(
            [str(helpers.console_script("doc-wiki-okf")), "proposals", str(root), "--json"],
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
