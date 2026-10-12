"""The canonical fail-open shim reaches an installed, functioning pipeline hook."""

from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from gw_kernel.workspace.init import apply_init, plan_init
from okf_io import load

SHIM = Path(__file__).resolve().parents[3] / "plugins/gw/hooks/examples/session-end-transcript-capture.py"


def test_hook_shim_import_target_is_live() -> None:
    tree = ast.parse(SHIM.read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert [(node.module, [name.name for name in node.names]) for node in imports] == [
        ("gw_pipeline.transcript_capture", ["main"])
    ]
    assert callable(importlib.import_module(imports[0].module).main)


def test_installed_hook_copies_bytes_and_replaces_one_source_on_rerun(tmp_path: Path) -> None:
    layout = apply_init(plan_init(tmp_path / "scratch-workspace", today=date(2026, 9, 2), topic="Hook scratch")).layout
    work_path = "work/feature-scratch"
    page = layout.bundle_dir / f"{work_path}.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("---\ntype: Feature\n---\n", encoding="utf-8", newline="\n")
    (layout.cache_dir / "active-work.json").write_text(
        json.dumps({"path": work_path, "phase": "execute"}) + "\n", encoding="utf-8", newline="\n"
    )
    transcript = tmp_path / "session.jsonl"
    trace = tmp_path / "trace.log"
    payload = json.dumps({"session_id": "pipeline-scratch", "transcript_path": str(transcript)})
    env = os.environ | {
        "GRAPH_WORKS_DIR": str(layout.root),
        "GRAPH_WORKS_TRANSCRIPT_CAPTURE_GUARD": "1",
        "GRAPH_WORKS_TRANSCRIPT_CAPTURE_TRACE_LOG": str(trace),
    }
    destination = layout.bundle_dir / work_path / "references/03-execute-transcript.jsonl"
    for content in [b'{"revision":1}\n', b'{"revision":2}\n']:
        transcript.write_bytes(content)
        completed = subprocess.run(
            [sys.executable, str(SHIM)], input=payload, text=True, capture_output=True, env=env, check=True
        )
        assert completed.returncode == 0
        assert destination.read_bytes() == content
        assert load(page).fm_data()["sources"] == [
            {
                "id": "execute-transcript",
                "resource": f"/{work_path}/references/03-execute-transcript.jsonl",
                "title": "Execute session transcript",
            }
        ]
    assert trace.read_text(encoding="utf-8").count(" | copied | ") == 2
