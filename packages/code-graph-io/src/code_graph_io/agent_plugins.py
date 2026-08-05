"""agent_plugin detector: walks `.claude-plugin/plugin.json` directories and
emits one `kind:agent_plugin` node per plugin, with its component inventory
(commands/agents/skills/scripts/hooks/mcp_servers) carried in attrs_json.

agent_plugin nodes have NO edges — components are inventory, not
graph nodes (see the design spec, "Why components are prose/attrs, not edges").
Each component carries a stable id so a future promotion to real graph nodes
(Option C) is a non-breaking extension.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml
from code_parser.projections.graph import GraphNode

from code_graph_io import _ignore, upsert
from code_graph_io.records import as_graph_records
from code_graph_io.uri import RepoContext, agent_plugin_uri

# Map file extension -> a coarse language label for the Scripts inventory.
_LANG_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".sh": "shell",
    ".bash": "shell",
    ".rb": "ruby",
    ".go": "go",
}


def _read_frontmatter(path: Path) -> dict:
    """Parse the leading `---\\n...\\n---` YAML frontmatter block of a markdown
    file. Returns {} when the file has no frontmatter, is unreadable, or the
    block is not a mapping. Avoids a python-frontmatter dependency — code-graph-io
    already ships pyyaml."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_json(path: Path) -> dict:
    """Read a JSON file into a dict; {} on any error or non-dict top level."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_str_list(value: Any) -> list[str]:
    """Coerce a frontmatter value to a list of strings (tools/skills may be a
    YAML list or a single string)."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if value is None or value == "":
        return []
    return [str(value)]


def _parse_commands(plugin_dir: Path, id_for) -> list[dict]:
    out: list[dict] = []
    for p in sorted(plugin_dir.glob("commands/*.md")):
        fm = _read_frontmatter(p)
        name = str(fm.get("name") or p.stem)
        out.append(
            {
                "id": id_for("command", name),
                "name": name,
                "description": str(fm.get("description") or ""),
            }
        )
    return out


def _parse_agents(plugin_dir: Path, id_for) -> list[dict]:
    out: list[dict] = []
    for p in sorted(plugin_dir.glob("agents/*.md")):
        fm = _read_frontmatter(p)
        name = str(fm.get("name") or p.stem)
        out.append(
            {
                "id": id_for("agent", name),
                "name": name,
                "description": str(fm.get("description") or ""),
                "model": str(fm.get("model") or ""),
                "tools": _as_str_list(fm.get("tools")),
            }
        )
    return out


def _parse_skills(plugin_dir: Path, id_for) -> list[dict]:
    out: list[dict] = []
    for p in sorted(plugin_dir.glob("skills/*/SKILL.md")):
        fm = _read_frontmatter(p)
        name = str(fm.get("name") or p.parent.name)
        out.append(
            {
                "id": id_for("skill", name),
                "name": name,
                "description": str(fm.get("description") or ""),
            }
        )
    return out


def _parse_scripts(plugin_dir: Path, id_for) -> list[dict]:
    scripts_dir = plugin_dir / "scripts"
    if not scripts_dir.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(scripts_dir.rglob("*")):
        if not p.is_file():
            continue
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(plugin_dir).as_posix()
        out.append(
            {
                "id": id_for("script", rel),
                "path": rel,
                "lang": _LANG_BY_SUFFIX.get(p.suffix, ""),
            }
        )
    return out


def _parse_hooks(plugin_dir: Path, id_for) -> list[dict]:
    data = _read_json(plugin_dir / "hooks" / "hooks.json")
    # claude-code hooks.json nests events under a top-level "hooks" map; tolerate
    # either {"hooks": {Event: [...]}} or a bare {Event: [...]} top level.
    events = data.get("hooks") if isinstance(data.get("hooks"), dict) else data
    out: list[dict] = []
    if not isinstance(events, dict):
        return out
    for event, entries in sorted(events.items()):
        matchers: list[str] = []
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("matcher") is not None:
                    matchers.append(str(entry["matcher"]))
        out.append(
            {
                "id": id_for("hook", str(event)),
                "event": str(event),
                "matchers": matchers,
            }
        )
    return out


def _parse_mcp_servers(plugin_dir: Path, id_for) -> list[dict]:
    data = _read_json(plugin_dir / ".mcp.json")
    servers = data.get("mcpServers")
    out: list[dict] = []
    if not isinstance(servers, dict):
        return out
    for srv_name, cfg in sorted(servers.items()):
        command = ""
        if isinstance(cfg, dict):
            command = str(cfg.get("command") or "")
        out.append(
            {
                "id": id_for("mcp_server", str(srv_name)),
                "name": str(srv_name),
                "command": command,
            }
        )
    return out


def emit(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    ctx: RepoContext,
    skip_dirs: frozenset[str] | None = None,
) -> None:
    """Walk `repo_root` for `.claude-plugin/plugin.json`, emit one
    `kind:agent_plugin` node per plugin with its component inventory in attrs.

    Honors the same vendored/fixture skip-dir filtering as the package walker
    (`code_graph_io._ignore`). Silently tolerates plugins with no components and
    manifests missing a `name`. Each node's `path` is the plugin directory
    relative to `repo_root` (e.g. `plugins/demo`); whole-plugin removal is
    covered by delete-and-rebuild per the project backward-compatibility rule.
    """
    repo_root = Path(repo_root).resolve()
    if skip_dirs is None:
        skip_dirs = _ignore.load_skip_dirs(repo_root)

    nodes: list[GraphNode] = []
    for manifest_path in sorted(repo_root.rglob(".claude-plugin/plugin.json")):
        rel = manifest_path.relative_to(repo_root).as_posix()
        if _ignore.should_skip(rel, skip_dirs):
            continue
        manifest = _read_json(manifest_path)
        name = manifest.get("name")
        if not name:
            continue
        name = str(name)
        plugin_dir = manifest_path.parent.parent  # .claude-plugin/ sits inside it

        def id_for(kind: str, leaf: str, _name: str = name) -> str:
            return f"{kind}:{ctx.org}/{ctx.repo}/{_name}/{leaf}"

        components = {
            "commands": _parse_commands(plugin_dir, id_for),
            "agents": _parse_agents(plugin_dir, id_for),
            "skills": _parse_skills(plugin_dir, id_for),
            "scripts": _parse_scripts(plugin_dir, id_for),
            "hooks": _parse_hooks(plugin_dir, id_for),
            "mcp_servers": _parse_mcp_servers(plugin_dir, id_for),
        }
        attrs: dict[str, Any] = {
            "uri": agent_plugin_uri(ctx, name),
            "ecosystem": "claude-code",
            "name": name,
            "version": str(manifest.get("version") or ""),
            "description": str(manifest.get("description") or ""),
            "components": components,
        }
        plugin_rel = plugin_dir.resolve().relative_to(repo_root).as_posix()
        nodes.append(GraphNode(kind="agent_plugin", name=name, path=plugin_rel, line=None, attrs=attrs))

    if nodes:
        upsert.upsert_records(conn, as_graph_records(nodes=nodes))
