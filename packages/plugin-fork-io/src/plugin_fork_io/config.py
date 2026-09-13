"""Pure settings resolution; no discovery, reads, or registry writes."""

from __future__ import annotations

import ntpath
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .records import JsonValue, Roots


class ConfigError(ValueError):
    """Settings are malformed or ambiguous."""


@dataclass(frozen=True)
class ResolvedSettings:
    roots: Roots
    project: Path
    config_path: Path
    explicit_content: bool


def portable_content_root(content: Path, state: Path, *, platform: str) -> str | None:
    """A cross-volume binding needs explicit local content resolution."""
    if platform == "win32":
        if ntpath.splitdrive(str(content))[0].casefold() != ntpath.splitdrive(str(state))[0].casefold():
            return None
        return ntpath.relpath(str(content), str(state)).replace("\\", "/")
    return posixpath.relpath(content.as_posix(), state.as_posix())


def resolve_settings(
    options: Mapping[str, Path | str | None],
    config: Mapping[str, JsonValue] | None,
    config_path: Path | None,
    *,
    home: Path,
    platform: str,
    environment: Mapping[str, str],
) -> ResolvedSettings:
    project_value = options.get("project")
    if project_value is None or not Path(project_value).is_absolute():
        raise ConfigError("Pass an absolute project path; cwd belongs to the CLI")
    project = Path(project_value)
    if config_path is None:
        if platform == "win32":
            appdata = environment.get("APPDATA")
            if not appdata:
                raise ConfigError("APPDATA is absent; provide --config explicitly")
            config_path = Path(appdata) / "plugin-fork/config.json"
        else:
            config_path = Path(environment.get("XDG_CONFIG_HOME") or home / ".config") / "plugin-fork/config.json"
    if not config_path.is_absolute():
        raise ConfigError("Configuration path must be absolute")
    parent = config_path.parent

    def path(value: object, relative: Path) -> Path:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ConfigError("Configured paths must be nonempty strings")
        candidate = Path(value)
        # abspath-style normalization without filesystem resolution or cwd reads.
        return Path(posixpath.normpath((candidate if candidate.is_absolute() else relative / candidate).as_posix()))

    selected: dict[str, JsonValue] = {}
    if config is not None:
        if type(config.get("schema_version")) is not int or config.get("schema_version") != 1:
            raise ConfigError("Unsupported configuration schema")
        if set(config) - {"schema_version", "defaults", "projects"}:
            raise ConfigError("Unknown configuration fields")
        defaults = config.get("defaults", {})
        if not isinstance(defaults, dict) or set(defaults) - {"content_dir", "state_dir"}:
            raise ConfigError("Invalid configuration defaults")
        for value in defaults.values():
            path(value, parent)
        selected.update(defaults)
        projects = config.get("projects", [])
        if not isinstance(projects, list):
            raise ConfigError("Projects must be an array")
        matches: list[dict[str, JsonValue]] = []
        variant = options.get("variant")
        for item in projects:
            if not isinstance(item, dict) or set(item) - {"project", "state_dir", "content_dir", "variants"}:
                raise ConfigError("Invalid project record")
            project_path = path(item.get("project"), parent)
            variants = item.get("variants", [])
            if not isinstance(variants, list) or any(not isinstance(v, str) or not v for v in variants):
                raise ConfigError("Variants must be an array of identifiers")
            for key in ("state_dir", "content_dir"):
                if key in item:
                    path(item[key], parent)
            if project_path == project or (variant is not None and variant in variants):
                matches.append(item)
        explicit_state = options.get("state_dir")
        if len(matches) > 1 and explicit_state is not None:
            matches = [
                m
                for m in matches
                if path(m.get("state_dir", defaults.get("state_dir", project / ".plugin-fork")), parent)
                == path(explicit_state, project)
            ]
        if len(matches) > 1:
            raise ConfigError("Ambiguous project or variant stores; specify --state-dir")
        if matches:
            selected.update({k: v for k, v in matches[0].items() if k in {"state_dir", "content_dir"}})
    content = (
        path(options["content_dir"], project)
        if options.get("content_dir") is not None
        else path(selected.get("content_dir", project / "agent-skills"), parent)
    )
    state = (
        path(options["state_dir"], project)
        if options.get("state_dir") is not None
        else path(selected.get("state_dir", project / ".plugin-fork"), parent)
    )
    return ResolvedSettings(
        Roots(content, state), project, config_path, options.get("content_dir") is not None or "content_dir" in selected
    )
