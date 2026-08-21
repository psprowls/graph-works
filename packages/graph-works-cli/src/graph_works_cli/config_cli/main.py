"""`gw config` — workspace-manifest configuration and host hook wiring."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Never

import typer
from config_io import (
    PlainYamlStore,
    RegistryError,
    StoreValidationError,
    resolve_key,
    set_key,
    unset_key,
    write_projection,
)
from graph_works_core.hooks import Action, HooksSettingsError
from graph_works_core.hooks import apply as apply_hooks
from graph_works_core.workspace import manifest
from graph_works_core.workspace.discovery import find_repo_root
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout

from graph_works_cli import exit_codes
from graph_works_cli.config_cli.rendering import render_hooks, render_projection, render_resolved, render_resolved_list
from graph_works_cli.workspace_resolution import resolve_workspace

config_app = typer.Typer(
    name="config",
    help="Read and write graph-works workspace configuration.",
    no_args_is_help=True,
)

_WORKSPACE_OPTION = typer.Option(
    "",
    "--workspace",
    help="Workspace path (default: GRAPH_WORKS_DIR, then cwd discovery).",
)
_JSON_OPTION = typer.Option(False, "--json", help="Emit machine-readable JSON.")
_REPO_OPTION = typer.Option(
    "",
    "--repo",
    help="Repository whose .claude/settings.local.json is written (default: cwd Git discovery).",
)
_HOOK_FEATURE_ARGUMENT = typer.Argument(..., help="transcript")


class HookFeature(StrEnum):
    """The closed hook-feature vocabulary exposed by core."""

    transcript = "transcript"


hooks_app = typer.Typer(
    name="hooks",
    help="Register or unregister opt-in hooks in .claude/settings.local.json.",
    no_args_is_help=True,
)
config_app.add_typer(hooks_app, name="hooks")


def _layout(workspace: str) -> WorkspaceLayout:
    return resolve_workspace(workspace)


def _store(workspace: str) -> tuple[WorkspaceLayout, PlainYamlStore]:
    """Resolve a workspace and bind its manifest to the config-io store seam."""
    layout = _layout(workspace)
    return layout, PlainYamlStore(layout.manifest_path)


def _exit_config_error(exc: Exception, *, code: int) -> Never:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=code) from exc


def _repo_root(repo: str) -> Path:
    if repo:
        return Path(repo).expanduser().resolve()
    cwd = Path.cwd().resolve()
    return find_repo_root(cwd) or cwd


def _run_hooks(
    action: Action,
    feature: HookFeature,
    repo: str,
    *,
    json_output: bool,
) -> None:
    try:
        result = apply_hooks(action, feature.value, _repo_root(repo))
    except HooksSettingsError as exc:
        _exit_config_error(exc, code=exit_codes.SCHEMA_MISMATCH)
    except WorkspaceError as exc:
        _exit_config_error(exc, code=exit_codes.GENERIC)
    typer.echo(render_hooks(result, json_output=json_output))


@hooks_app.command()
def enable(
    feature: HookFeature = _HOOK_FEATURE_ARGUMENT,
    repo: str = _REPO_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Merge a feature's hook registrations into .claude/settings.local.json."""
    _run_hooks("enable", feature, repo, json_output=json_output)


@hooks_app.command()
def disable(
    feature: HookFeature = _HOOK_FEATURE_ARGUMENT,
    repo: str = _REPO_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Remove a feature's hook registrations from .claude/settings.local.json."""
    _run_hooks("disable", feature, repo, json_output=json_output)


@config_app.command(name="get")
def get_cmd(
    key: str = typer.Argument(..., help="Config key (see `gw config list`)."),
    workspace: str = _WORKSPACE_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Show a key's effective value and origin (env / manifest / default)."""
    try:
        result = manifest.resolve_checked_key(_layout(workspace), key, environ=os.environ)
    except RegistryError as exc:
        _exit_config_error(exc, code=exit_codes.GENERIC)
    except (StoreValidationError, WorkspaceError) as exc:
        _exit_config_error(exc, code=exit_codes.SCHEMA_MISMATCH)
    typer.echo(render_resolved(result, json_output=json_output))


@config_app.command(name="list")
def list_cmd(
    workspace: str = _WORKSPACE_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """List every resolved catalog key, including concrete wildcard keys."""
    try:
        results = manifest.resolve_checked_all(_layout(workspace), environ=os.environ)
    except RegistryError as exc:
        _exit_config_error(exc, code=exit_codes.GENERIC)
    except (StoreValidationError, WorkspaceError) as exc:
        _exit_config_error(exc, code=exit_codes.SCHEMA_MISMATCH)
    typer.echo(render_resolved_list(results, json_output=json_output))


@config_app.command(name="set")
def set_cmd(
    key: str = typer.Argument(...),
    value: str = typer.Argument(...),
    workspace: str = _WORKSPACE_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Set a catalog key and refresh `_gw/_config/config.json`."""
    try:
        layout, store = _store(workspace)
        result = set_key(
            manifest.CATALOG,
            key,
            value,
            store=store,
            projection=layout.config_dir / "config.json",
        )
    except RegistryError as exc:
        _exit_config_error(exc, code=exit_codes.GENERIC)
    except (StoreValidationError, WorkspaceError) as exc:
        _exit_config_error(exc, code=exit_codes.SCHEMA_MISMATCH)
    typer.echo(render_resolved(result, json_output=json_output))


@config_app.command(name="unset")
def unset_cmd(
    key: str = typer.Argument(...),
    workspace: str = _WORKSPACE_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Remove an explicit key, refresh the projection, and show its fallback."""
    try:
        layout, store = _store(workspace)
        unset_key(
            manifest.CATALOG,
            key,
            store=store,
            projection=layout.config_dir / "config.json",
        )
        result = resolve_key(manifest.CATALOG, key, store=store, environ=os.environ)
    except RegistryError as exc:
        _exit_config_error(exc, code=exit_codes.GENERIC)
    except (StoreValidationError, WorkspaceError) as exc:
        _exit_config_error(exc, code=exit_codes.SCHEMA_MISMATCH)
    typer.echo(render_resolved(result, json_output=json_output))


@config_app.command()
def sync(
    workspace: str = _WORKSPACE_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Regenerate `_gw/_config/config.json` after an out-of-band manifest edit."""
    try:
        layout, store = _store(workspace)
        target = write_projection(store, layout.config_dir / "config.json")
    except RegistryError as exc:
        _exit_config_error(exc, code=exit_codes.GENERIC)
    except (StoreValidationError, WorkspaceError) as exc:
        _exit_config_error(exc, code=exit_codes.SCHEMA_MISMATCH)
    typer.echo(render_projection(target, json_output=json_output))
