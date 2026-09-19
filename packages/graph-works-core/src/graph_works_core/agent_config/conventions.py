"""`plugin_fork_io.adapters` conventions resolved to absolute paths.

Everything machine-specific is an argument: `home`, `env` and `platform` are injected by the
caller, so nothing here reads `Path.home()`, `os.environ` or `sys.platform`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal

from plugin_fork_io.adapters import TrustRecordSpec, adapter

from graph_works_core.agent_config.records import AgentName, Scope


@dataclass(frozen=True, slots=True)
class ResolvedLayer:
    scope: Scope
    path: Path
    format: Literal["json", "toml"]
    location_error: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedConventions:
    agent: AgentName
    home: Path
    layers: tuple[ResolvedLayer, ...]
    trust: TrustRecordSpec | None
    trust_path: Path | None


def _absolute(path: Path, *, home: Path | None = None) -> Path:
    """Return a native absolute path, expanding only a leading tilde we were given."""
    raw = str(path)
    if home is not None and (raw == "~" or raw.startswith("~/")):
        path = home.joinpath(*raw.removeprefix("~").lstrip("/").split("/"))
    return path if path.is_absolute() else Path.cwd() / path


def _relative(base: Path, raw: str) -> Path:
    return _absolute(base / raw)


def _system_path(raw: str, platform: str) -> Path:
    """Keep Windows map entries intact when win32 is simulated on another host."""
    if platform == "win32" and PureWindowsPath(raw).is_absolute():
        return Path(raw)
    return _absolute(Path(raw))


def resolve_conventions(
    agent: AgentName, *, project: Path, home: Path, env: Mapping[str, str], platform: str
) -> ResolvedConventions:
    """Resolve one adapter's static conventions without consulting process-global state."""
    project = _absolute(project)
    home = _absolute(home)
    config = adapter(agent).config
    override = env.get(config.home_env, "") if config.home_env else ""
    agent_home = _absolute(Path(override), home=home) if override else _relative(home, config.home)
    layers: list[ResolvedLayer] = []
    for spec in config.layers:
        if isinstance(spec.path, tuple):
            system = dict(spec.path).get(platform)
            if system is None:
                continue
            path = _system_path(system, platform)
        elif spec.base == "project":
            path = _relative(project, spec.path)
        else:
            path = _relative(agent_home, spec.path)
        layers.append(ResolvedLayer(spec.scope, path, spec.format))
    trust_path = None
    if config.trust is not None:
        trust_path = _relative(home if config.trust.base == "home" else agent_home, config.trust.path)
    return ResolvedConventions(agent, agent_home, tuple(layers), config.trust, trust_path)
