"""Load `_repositories.yaml` — the bundle's only configuration surface.

Config raises; content never does. okf-io's never-raise rule is for
concept content, and `_repositories.yaml` is configuration — following the
`VocabularyError` / `SchemaError` / `SectionError` precedent in okf-ext.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

CONFIG_FILENAME = "_repositories.yaml"

_DEFAULT_STATE_GATE_BRANCHES: tuple[str, ...] = ("main",)


class ConfigError(ValueError):
    """A `_repositories.yaml` the caller got wrong.

    Subclasses `ValueError` so a caller catching either works, mirroring
    `okf_ext.tags.VocabularyError` / `okf_ext.schemas.SchemaError`.
    """


@dataclass(frozen=True, slots=True)
class RepoConfig:
    name: str
    path: Path
    ignore: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StateGateConfig:
    enabled: bool
    branches: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Config:
    graph_dir: Path
    declarations_dir: Path
    repos: tuple[RepoConfig, ...]
    state_gate: StateGateConfig


def _read_yaml(path: Path) -> Any:  # noqa: ANN401 -- arbitrary parsed YAML document
    text = path.read_bytes().decode("utf-8")
    try:
        return YAML(typ="safe").load(text)
    except YAMLError as exc:
        raise ConfigError(f"{path.name}: not valid YAML: {exc}") from exc


def _resolve(bundle_root: Path, raw: str) -> Path:
    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        return expanded
    return (bundle_root / expanded).resolve()


def _require_mapping(value: Any, *, name: str, where: str) -> dict[str, Any]:  # noqa: ANN401
    if not isinstance(value, dict):
        raise ConfigError(f"{name}: {where} must be a mapping")
    return value


def _string_list(value: Any, *, name: str, where: str) -> tuple[str, ...]:  # noqa: ANN401
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name}: {where} must be a list of strings")
    return tuple(value)


def _reject_unknown_keys(
    mapping: dict[str, Any],
    *,
    allowed: set[str],
    name: str,
    where: str,
) -> None:
    """Raise ConfigError if mapping contains keys not in allowed set."""
    unknown = set(mapping) - allowed
    if unknown:
        raise ConfigError(
            f"{name}: unknown key(s) {sorted(unknown)!r} under {where}; only {sorted(allowed)!r} are recognized"
        )


def _require_nonempty_string(value: Any, *, name: str, where: str) -> str:  # noqa: ANN401
    """Raise ConfigError if value is not a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name}: {where} is required and must be a non-empty string")
    return value


def load_config(bundle_root: str | Path) -> Config:
    """Load and validate `<bundle_root>/_repositories.yaml`.

    Raises `ConfigError` naming the file and key for any malformed shape.
    Propagates `OSError` unchanged when the file is missing.
    """
    bundle_root = Path(bundle_root)
    path = bundle_root / CONFIG_FILENAME
    raw = _read_yaml(path)
    name = CONFIG_FILENAME

    doc = _require_mapping(raw, name=name, where="the document") if raw is not None else {}
    allowed_top = {"graph_dir", "declarations_dir", "repositories", "ignore", "state_gate"}
    _reject_unknown_keys(doc, allowed=allowed_top, name=name, where="at top level")

    graph_dir_raw = _require_nonempty_string(doc.get("graph_dir"), name=name, where="`graph_dir`")
    graph_dir = _resolve(bundle_root, graph_dir_raw)

    # Absent means "in the bundle", which is both the default layout and the
    # only one that needs no coordination between the packages sharing a
    # bundle. Present-but-blank is a typo, so it goes through the same
    # non-empty check every other path key uses rather than falling back.
    declarations_raw = doc.get("declarations_dir")
    if declarations_raw is None:
        declarations_dir = bundle_root
    else:
        declarations_dir = _resolve(
            bundle_root,
            _require_nonempty_string(declarations_raw, name=name, where="`declarations_dir`"),
        )

    global_ignore = _string_list(doc.get("ignore"), name=name, where="`ignore`")

    repos_raw = doc.get("repositories")
    repos_raw = _require_mapping(repos_raw if repos_raw is not None else {}, name=name, where="`repositories`")

    repos: list[RepoConfig] = []
    for repo_name, entry in repos_raw.items():
        entry = _require_mapping(entry, name=name, where=f"`repositories.{repo_name}`")
        allowed_repo_keys = {"path", "ignore"}
        _reject_unknown_keys(entry, allowed=allowed_repo_keys, name=name, where=f"`repositories.{repo_name}`")
        repo_path_raw = _require_nonempty_string(entry.get("path"), name=name, where=f"`repositories.{repo_name}.path`")
        repo_path = _resolve(bundle_root, repo_path_raw)
        per_repo_ignore = _string_list(entry.get("ignore"), name=name, where=f"`repositories.{repo_name}.ignore`")
        repos.append(RepoConfig(name=repo_name, path=repo_path, ignore=global_ignore + per_repo_ignore))

    gate_raw = doc.get("state_gate")
    if gate_raw is None:
        state_gate = StateGateConfig(enabled=True, branches=_DEFAULT_STATE_GATE_BRANCHES)
    else:
        gate_raw = _require_mapping(gate_raw, name=name, where="`state_gate`")
        allowed_gate_keys = {"enabled", "branches"}
        _reject_unknown_keys(gate_raw, allowed=allowed_gate_keys, name=name, where="`state_gate`")
        enabled = gate_raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"{name}: `state_gate.enabled` must be a boolean")
        # Only apply default if key is absent; empty list is intentional and valid
        if "branches" in gate_raw:
            branches = _string_list(gate_raw["branches"], name=name, where="`state_gate.branches`")
        else:
            branches = _DEFAULT_STATE_GATE_BRANCHES
        state_gate = StateGateConfig(enabled=enabled, branches=branches)

    return Config(
        graph_dir=graph_dir,
        declarations_dir=declarations_dir,
        repos=tuple(repos),
        state_gate=state_gate,
    )
