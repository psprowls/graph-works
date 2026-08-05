"""agent_plugin detector: .claude-plugin/plugin.json -> kind:agent_plugin nodes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from code_graph_io import agent_plugins, store
from code_graph_io.uri import RepoContext

_CTX = RepoContext(org="test", repo="repo")


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "code.db"
    c = store.connect(db, create=True)
    yield c
    c.close()


def _make_plugin(root: Path) -> Path:
    """Lay down a minimal but complete plugin tree under root/plugins/demo."""
    pdir = root / "plugins" / "demo"
    (pdir / ".claude-plugin").mkdir(parents=True)
    (pdir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "1.2.3",
                "description": "A demo plugin.",
            }
        )
    )
    (pdir / "commands").mkdir()
    (pdir / "commands" / "scan.md").write_text("---\nname: scan\ndescription: Walk the monorepo.\n---\n# /demo:scan\n")
    (pdir / "agents").mkdir()
    (pdir / "agents" / "scanner.md").write_text(
        "---\nname: scanner\ndescription: Sub-agent.\nmodel: sonnet\ntools: [Read, Write]\n---\nbody\n"
    )
    (pdir / "skills" / "demo-skill").mkdir(parents=True)
    (pdir / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: A skill.\n---\nbody\n"
    )
    (pdir / "scripts").mkdir()
    (pdir / "scripts" / "lint.py").write_text("print('hi')\n")
    (pdir / "hooks").mkdir()
    (pdir / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command"}]}]}})
    )
    (pdir / ".mcp.json").write_text(json.dumps({"mcpServers": {"demo-server": {"command": "uv run demo-mcp"}}}))
    return pdir


def test_emit_no_plugins_is_silent(tmp_path: Path, conn: sqlite3.Connection) -> None:
    agent_plugins.emit(conn, repo_root=tmp_path, ctx=_CTX)
    n = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='agent_plugin'").fetchone()[0]
    assert n == 0


def test_emit_creates_one_node_with_manifest_fields(tmp_path: Path, conn: sqlite3.Connection) -> None:
    _make_plugin(tmp_path)
    agent_plugins.emit(conn, repo_root=tmp_path, ctx=_CTX)
    rows = conn.execute("SELECT name, attrs_json, uri, path FROM nodes WHERE kind='agent_plugin'").fetchall()
    assert len(rows) == 1
    name, attrs_json, uri, path = rows[0]
    attrs = json.loads(attrs_json)
    assert name == "demo"
    assert uri == "agent_plugin:test/repo/demo"
    assert attrs["ecosystem"] == "claude-code"
    assert attrs["version"] == "1.2.3"
    assert attrs["description"] == "A demo plugin."
    assert path == "plugins/demo"


def test_emit_parses_all_component_types(tmp_path: Path, conn: sqlite3.Connection) -> None:
    _make_plugin(tmp_path)
    agent_plugins.emit(conn, repo_root=tmp_path, ctx=_CTX)
    attrs = json.loads(conn.execute("SELECT attrs_json FROM nodes WHERE kind='agent_plugin'").fetchone()[0])
    c = attrs["components"]

    assert c["commands"] == [{"id": "command:test/repo/demo/scan", "name": "scan", "description": "Walk the monorepo."}]
    assert c["agents"] == [
        {
            "id": "agent:test/repo/demo/scanner",
            "name": "scanner",
            "description": "Sub-agent.",
            "model": "sonnet",
            "tools": ["Read", "Write"],
        }
    ]
    assert c["skills"] == [{"id": "skill:test/repo/demo/demo-skill", "name": "demo-skill", "description": "A skill."}]
    assert c["scripts"] == [
        {"id": "script:test/repo/demo/scripts/lint.py", "path": "scripts/lint.py", "lang": "python"}
    ]
    assert c["hooks"] == [{"id": "hook:test/repo/demo/PreToolUse", "event": "PreToolUse", "matchers": ["Bash"]}]
    assert c["mcp_servers"] == [
        {"id": "mcp_server:test/repo/demo/demo-server", "name": "demo-server", "command": "uv run demo-mcp"}
    ]


def test_emit_tolerates_missing_components(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A bare plugin (manifest only) emits a node with all-empty component lists."""
    pdir = tmp_path / "plugins" / "bare" / ".claude-plugin"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps({"name": "bare"}))
    agent_plugins.emit(conn, repo_root=tmp_path, ctx=_CTX)
    attrs = json.loads(conn.execute("SELECT attrs_json FROM nodes WHERE kind='agent_plugin'").fetchone()[0])
    assert attrs["version"] == ""
    assert attrs["components"] == {
        "commands": [],
        "agents": [],
        "skills": [],
        "scripts": [],
        "hooks": [],
        "mcp_servers": [],
    }


def test_emit_skips_vendored_plugins(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A plugin under a skip-dir (e.g. node_modules) is not emitted."""
    vend = tmp_path / "node_modules" / "vendored" / ".claude-plugin"
    vend.mkdir(parents=True)
    (vend / "plugin.json").write_text(json.dumps({"name": "vendored"}))
    agent_plugins.emit(conn, repo_root=tmp_path, ctx=_CTX)
    n = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='agent_plugin'").fetchone()[0]
    assert n == 0


def test_emit_skips_entries_without_name(tmp_path: Path, conn: sqlite3.Connection) -> None:
    pdir = tmp_path / "plugins" / "noname" / ".claude-plugin"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps({"version": "1.0.0"}))
    agent_plugins.emit(conn, repo_root=tmp_path, ctx=_CTX)
    n = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='agent_plugin'").fetchone()[0]
    assert n == 0
