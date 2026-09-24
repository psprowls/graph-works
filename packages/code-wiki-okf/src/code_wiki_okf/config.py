"""Load `workspace.yaml` — the workspace's `repositories` / `ignore` /
`state_gate` blocks.

Config raises; content never does. okf-io's never-raise rule is for
concept content, and the workspace manifest is configuration — following the
`VocabularyError` / `SchemaError` / `SectionError` precedent in okf-ext.

This reads the same file `graph_works_core.workspace.manifest` reads, but
independently: this package cannot depend on `graph_works_core` (ADR 2026-08-02-workspace-layering —
`code-wiki-okf` is tier 3, `graph_works_core` sits above it), so it has its
own permissive top-level parse rather than importing `Manifest`/`CATALOG`.
Every key this module does not name (`version`, `roles`, `layout`, ...) is
ignored rather than rejected -- the manifest catalog owns top-level
validation. The three blocks' *internal* shape is still checked strictly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from code_wiki_okf.placement import filesystem_member_identity

MANIFEST_FILENAME = "workspace.yaml"

_DEFAULT_STATE_GATE_BRANCHES: tuple[str, ...] = ("main",)


class ConfigError(ValueError):
    """A `workspace.yaml` the caller got wrong.

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


def _require_nonempty_kwarg(value: str | Path, *, param: str) -> str:
    """Raise ConfigError if a caller-supplied `graph_dir`/`declarations_dir` is blank.

    Checked on the raw value, before any `str(...)` coercion -- `str(Path(""))`
    is `"."`, which would silently pass a blank-string check downstream. Only a
    blank *string* is a typo; a `Path` is never blank by this test; `Path("")`
    already normalizes to the current directory, a real answer, not one.

    Named after the parameter, not the document: this is a call-site error, and
    the file `load_config` is reading has nothing to do with it.
    """
    if isinstance(value, str) and not value.strip():
        raise ConfigError(f"`{param}` is required and must be a non-empty string")
    return str(value)


def load_config(
    bundle_root: str | Path,
    *,
    config_path: str | Path | None = None,
    graph_dir: str | Path,
    declarations_dir: str | Path | None = None,
) -> Config:
    """Load and validate the workspace manifest's `repositories` / `ignore` /
    `state_gate` blocks, from *config_path* when given, else
    `<bundle_root>/workspace.yaml`.

    *graph_dir* is required: the document does not carry it, so the caller
    supplies it from the resolved workspace layout, e.g.
    `graph_dir=layout.cache_dir`. No default -- a silently-wrong graph
    location is worse than a `TypeError` at the call site that got it wrong.

    *declarations_dir* defaults to *bundle_root* itself when absent.

    Each declared repo's relative `path` resolves against the directory the
    document itself was read from (`config_path`'s parent, or *bundle_root*
    when `config_path` is absent) -- not *bundle_root* directly. The two
    coincide whenever `config_path` is omitted or points at
    `<bundle_root>/workspace.yaml`.

    Every top-level key other than `repositories`, `ignore`, `state_gate` is
    ignored -- the manifest catalog (`graph_works_core.workspace.manifest`)
    owns top-level validation; this reader only owns the three blocks it
    consumes.

    Raises `ConfigError` naming the file and key for any malformed shape
    within those three blocks. Propagates `OSError` unchanged when the file
    is missing.
    """
    bundle_root = Path(bundle_root)
    path = Path(config_path) if config_path is not None else bundle_root / MANIFEST_FILENAME
    raw = _read_yaml(path)
    doc = _require_mapping(raw, name=path.name, where="the document") if raw is not None else {}
    return config_from_mapping(
        doc,
        anchor=path.parent,
        bundle_root=bundle_root,
        graph_dir=graph_dir,
        declarations_dir=declarations_dir,
        source=path.name,
    )


def config_from_mapping(
    content: Mapping[str, Any],
    *,
    anchor: str | Path,
    bundle_root: str | Path,
    graph_dir: str | Path,
    declarations_dir: str | Path | None = None,
    source: str = MANIFEST_FILENAME,
) -> Config:
    """Validate an already-parsed manifest's `repositories` / `ignore` /
    `state_gate` blocks.

    The sibling of `load_config` (D-005), for a caller that has already read
    the document — `graph_works_core.workspace.config`, which reads it through
    `config-io`'s store so the workspace has one YAML parser rather than two. A
    `content=` keyword on `load_config` would have been mutually exclusive
    with `config_path=` and needed a runtime refusal for the both-given case;
    two functions have no illegal argument combination.

    *anchor* is the directory a relative `repositories.*.path` resolves
    against — the directory the document itself was read from (ADR 2026-08-26-a-relative-path). It
    is **not** *bundle_root*, which is what a relative *graph_dir* /
    *declarations_dir* resolves against and what *declarations_dir* defaults
    to. The two coincide only when the document sits at
    `<bundle_root>/workspace.yaml`.

    *source* is the name every `ConfigError` message quotes. It is the
    document's name, not a path, and it defaults to `MANIFEST_FILENAME`.

    Every top-level key other than the three blocks is ignored; see
    `load_config`.
    """
    bundle_root = Path(bundle_root)
    anchor = Path(anchor)
    name = source
    doc = dict(content)

    resolved_graph_dir = _resolve(bundle_root, _require_nonempty_kwarg(graph_dir, param="graph_dir"))

    if declarations_dir is None:
        resolved_declarations_dir = bundle_root
    else:
        resolved_declarations_dir = _resolve(
            bundle_root,
            _require_nonempty_kwarg(declarations_dir, param="declarations_dir"),
        )

    global_ignore = _string_list(doc.get("ignore"), name=name, where="`ignore`")

    repos_raw = doc.get("repositories")
    repos_raw = _require_mapping(repos_raw if repos_raw is not None else {}, name=name, where="`repositories`")

    repos: list[RepoConfig] = []
    for repo_name, entry in repos_raw.items():
        if filesystem_member_identity(str(repo_name)) == "index":
            raise ConfigError(
                f"{name}: `repositories.{repo_name}`: a repository named {repo_name!r} would collide with "
                "code-graph/index.md; rename it"
            )
        entry = _require_mapping(entry, name=name, where=f"`repositories.{repo_name}`")
        allowed_repo_keys = {"path", "ignore"}
        _reject_unknown_keys(entry, allowed=allowed_repo_keys, name=name, where=f"`repositories.{repo_name}`")
        repo_path_raw = _require_nonempty_string(entry.get("path"), name=name, where=f"`repositories.{repo_name}.path`")
        repo_path = _resolve(anchor, repo_path_raw)
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
        graph_dir=resolved_graph_dir,
        declarations_dir=resolved_declarations_dir,
        repos=tuple(repos),
        state_gate=state_gate,
    )
