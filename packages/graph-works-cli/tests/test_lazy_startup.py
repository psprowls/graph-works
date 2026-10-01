"""The no-AI guard (D-004): basic gw paths must not load the agent-loop stack.

Wall-clock thresholds flake on a contended host; module presence does not. Each case runs a
fresh interpreter, because this process has already imported everything.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

FORBIDDEN = ("langchain_core", "langsmith", "langchain_protocol")

_DRIVER = """
import json, sys
sys.argv = ["gw", *json.loads(sys.argv[1])]
code = 0
try:
    from graph_works_cli.cli import app
    app()
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
loaded = sorted(m for m in sys.modules if m.split(".")[0] in {forbidden})
sys.stderr.write("\\nEXIT_CODE=" + str(code) + "\\n")
sys.stderr.write("FORBIDDEN_LOADED=" + json.dumps(loaded) + "\\n")
""".replace("{forbidden}", repr(set(FORBIDDEN)))


def _loaded_after_import(statement: str) -> list[str]:
    code = (
        f"{statement}\nimport json, sys\n"
        f"print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in {set(FORBIDDEN)!r})))"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", check=True)
    return list(json.loads(done.stdout.strip().splitlines()[-1]))


def _loaded_after_gw(argv: list[str]) -> list[str]:
    done = subprocess.run(
        [sys.executable, "-c", _DRIVER, json.dumps(argv)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    marker = done.stderr.rsplit("FORBIDDEN_LOADED=", 1)
    assert len(marker) == 2, f"driver did not finish: {done.stderr}"
    exit_marker = marker[0].rsplit("EXIT_CODE=", 1)
    assert len(exit_marker) == 2, f"driver did not report an exit code: {done.stderr}"
    exit_code = int(exit_marker[1].strip())
    assert exit_code == 0, f"gw {' '.join(argv)} exited {exit_code}: {done.stderr}"
    return list(json.loads(marker[1]))


IMPORT_PROBES = {
    "subagents_io": "import subagents_io",
    "graph_works_core": "import graph_works_core",
    "cli": "import graph_works_cli.cli",
}

GW_PROBES = {
    "version": ["version"],
    "root-help": ["--help"],
    "work-status-help": ["work", "status", "--help"],
    "next-help": ["next", "--help"],
    "config-list-help": ["config", "list", "--help"],
    "graph-help": ["graph", "--help"],
    "agent-config-help": ["agent-config", "--help"],
    "util-help": ["util", "--help"],
    "archive-help": ["archive", "--help"],
}

# Flipped on, task by task, as each hub goes lazy.
READY_IMPORTS: set[str] = {"subagents_io", "graph_works_core", "cli"}
READY_GW: set[str] = set(GW_PROBES)


@pytest.mark.parametrize("probe", sorted(IMPORT_PROBES))
def test_importing_leaves_the_ai_stack_out(probe: str) -> None:
    if probe not in READY_IMPORTS:
        pytest.skip("pending its task")
    assert _loaded_after_import(IMPORT_PROBES[probe]) == []


@pytest.mark.parametrize("probe", sorted(GW_PROBES))
def test_basic_gw_commands_leave_the_ai_stack_out(probe: str) -> None:
    if probe not in READY_GW:
        pytest.skip("pending its task")
    assert _loaded_after_gw(GW_PROBES[probe]) == []


def test_the_guard_can_see_a_violation() -> None:
    """Proves the harness detects loads at all, so a green guard means something."""
    assert "langchain_core" in _loaded_after_import("import langchain_core")
