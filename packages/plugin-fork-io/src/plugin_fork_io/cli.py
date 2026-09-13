from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

import typer
from typer.core import TyperGroup

from .config import ResolvedSettings
from .inspection import inspect_source
from .machine import Services
from .records import Finding, JsonValue, Result, SourceSpec


class LifecycleGroup(TyperGroup):
    """Keep parser and unexpected command failures in the public JSON envelope."""

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:  # noqa: ANN401
        # Typer versions use different Click Context classes; keep this boundary opaque.
        ctx.meta["json_output"] = "--json" in args
        ctx.meta["operation"] = next(
            (
                arg
                for arg in args
                if arg in {"inspect", "fork", "adopt", "install", "status", "update", "accept", "rollback"}
            ),
            "cli",
        )
        return super().parse_args(ctx, args)

    def invoke(self, ctx: Any) -> object:  # noqa: ANN401 - version-dependent framework Context
        try:
            return super().invoke(ctx)
        except typer.Exit:
            raise
        except Exception as error:
            operation = ctx.invoked_subcommand or ctx.meta.get("operation", "cli")
            usage = getattr(error, "exit_code", None) == 2
            emit(
                Result(
                    operation,
                    None,
                    None,
                    False,
                    False,
                    (Finding("usage.invalid" if usage else "internal.error", "error", None, None, str(error)),),
                    (),
                    {},
                ),
                json_output=bool(ctx.meta.get("json_output")),
            )
            raise typer.Exit(2 if usage else 4) from error


app = typer.Typer(no_args_is_help=True, cls=LifecycleGroup)


@app.callback()
def root() -> None:
    """Manage provenance-preserving forks of agent skills."""


def result_json(result: Result) -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "operation": result.operation,
        "variant_id": result.variant_id,
        "preview_id": result.preview_id,
        "applied": result.applied,
        "allowed": result.allowed,
        "findings": [asdict(finding) for finding in result.findings],
        "affected_paths": list(result.affected_paths),
        "data": dict(result.data),
    }


def emit(result: Result, *, json_output: bool) -> None:
    payload = result_json(result)
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
        return
    typer.echo(f"{result.operation}: {'allowed' if result.allowed else 'refused'}")
    if result.variant_id:
        typer.echo(f"variant: {result.variant_id}")
    if result.preview_id:
        typer.echo(f"preview: {result.preview_id}")
    if result.data:
        typer.echo(json.dumps(dict(result.data), sort_keys=True, indent=2))
    for finding in result.findings:
        location = f"{finding.path}:{finding.line}" if finding.path and finding.line else finding.path
        typer.echo(f"{finding.severity}: {location or '-'}: {finding.message}")


def _internal_error(error: Exception, *, json_output: bool) -> NoReturn:
    result = Result(
        operation="inspect",
        variant_id=None,
        preview_id=None,
        applied=False,
        allowed=False,
        findings=(Finding("internal.error", "error", None, None, str(error)),),
        affected_paths=(),
        data={},
    )
    if json_output:
        emit(result, json_output=True)
    typer.echo(f"internal error: {error}", err=True)
    raise typer.Exit(4)


@app.command("inspect")
def inspect_command(
    source: str,
    json_output: bool = typer.Option(False, "--json"),
    revision: str | None = typer.Option(None, "--ref"),
    content_dir: Annotated[Path | None, typer.Option("--content-dir")] = None,
    state_dir: Annotated[Path | None, typer.Option("--state-dir")] = None,
    project: Annotated[Path | None, typer.Option("--project")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    _settings(project, state_dir, content_dir, config_path, None, operation="inspect", json_output=json_output)
    try:
        kind: Literal["local", "git"] = (
            "git" if revision is not None or "://" in source or source.startswith("git@") else "local"
        )
        locator = source if kind == "git" else str(Path(source).resolve())
        result = inspect_source(SourceSpec(locator, kind, revision), services=Services.local())
        emit(result, json_output=json_output)
    except Exception as error:
        _internal_error(error, json_output=json_output)
    failure = 4 if any(f.code in {"git.missing", "git.failed"} for f in result.findings) else 3
    raise typer.Exit(0 if result.allowed else failure)


def main() -> None:  # pragma: no cover - console-script adapter
    app()


@app.command("fork")
def fork_command(
    source: Annotated[str | None, typer.Argument()] = None,
    selection_path: Annotated[Path | None, typer.Option("--selection")] = None,
    apply_id: Annotated[str | None, typer.Option("--apply")] = None,
    intent_path: Annotated[Path | None, typer.Option("--intent")] = None,
    revision: str | None = typer.Option(None, "--ref"),
    content_dir: Annotated[Path | None, typer.Option("--content-dir")] = None,
    state_dir: Annotated[Path | None, typer.Option("--state-dir")] = None,
    project: Annotated[Path | None, typer.Option("--project")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Prepare an inactive selective fork. Application is a subsequent explicit action."""
    from .fork import plan_fork
    from .records import Selection

    settings = _settings(project, state_dir, content_dir, config_path, None, operation="fork", json_output=json_output)

    if apply_id is not None:
        if any(value is not None for value in (source, selection_path, intent_path, revision, content_dir)):
            raise typer.BadParameter("Apply takes only the preview ID and state/output resolution options")

        result = _apply("fork", settings.roots.state, apply_id)
        emit(result, json_output=json_output)
        raise typer.Exit(0 if result.allowed else 4 if any(f.code == "transaction.io" for f in result.findings) else 3)
    if source is None or selection_path is None:
        raise typer.BadParameter("Preparation requires SOURCE and --selection")
    try:
        value = json.loads(selection_path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError("Selection must be an object")
        selection = Selection.from_json(value)
        notes = json.loads(intent_path.read_bytes()) if intent_path else []
        if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
            raise ValueError("Intent must be an array of strings")
    except (ValueError, OSError) as error:
        result = Result(
            "fork",
            None,
            None,
            False,
            False,
            (Finding("config.invalid", "error", str(selection_path), None, str(error)),),
            (),
            {},
        )
        emit(result, json_output=json_output)
        raise typer.Exit(2) from error
    try:
        kind: Literal["local", "git"] = (
            "git" if revision is not None or "://" in source or source.startswith("git@") else "local"
        )
        locator = source if kind == "git" else str(Path(source).resolve())
        project = project or Path.cwd()
        result = plan_fork(
            SourceSpec(locator, kind, revision),
            selection,
            settings.roots,
            intent=tuple(notes),
            services=Services.local(),
        )
        emit(result, json_output=json_output)
    except Exception as error:
        result = Result(
            "fork", None, None, False, False, (Finding("internal.error", "error", None, None, str(error)),), (), {}
        )
        emit(result, json_output=json_output)
        raise typer.Exit(4) from error
    raise typer.Exit(0 if result.allowed else 3)


@app.command("status")
def status_command(
    variant_id: str,
    content_dir: Annotated[Path | None, typer.Option("--content-dir")] = None,
    state_dir: Annotated[Path | None, typer.Option("--state-dir")] = None,
    project: Annotated[Path | None, typer.Option("--project")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    from .records import Roots
    from .status import read_status
    from .store import load_ledger

    settings = _settings(
        project, state_dir, content_dir, config_path, variant_id, operation="status", json_output=json_output
    )
    roots = settings.roots
    if not settings.explicit_content:
        try:
            ledger = load_ledger(roots.state, variant_id)
            from .store import resolve_content_root

            roots = Roots(resolve_content_root(roots.state, ledger), roots.state)
        except (OSError, ValueError) as error:
            emit(
                Result(
                    "status",
                    variant_id,
                    None,
                    False,
                    False,
                    (Finding("tracking.binding", "error", None, None, str(error)),),
                    (),
                    {},
                ),
                json_output=json_output,
            )
            raise typer.Exit(3) from error
    result = read_status(roots, variant_id, services=Services.local())
    emit(result, json_output=json_output)
    raise typer.Exit(0 if result.allowed else 3)


@app.command("rollback")
def rollback_command(
    variant_id: Annotated[str | None, typer.Argument()] = None,
    apply_id: Annotated[str | None, typer.Option("--apply")] = None,
    content_dir: Annotated[Path | None, typer.Option("--content-dir")] = None,
    state_dir: Annotated[Path | None, typer.Option("--state-dir")] = None,
    project: Annotated[Path | None, typer.Option("--project")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    from .recovery import plan_rollback

    settings = _settings(
        project, state_dir, content_dir, config_path, variant_id, operation="rollback", json_output=json_output
    )
    if apply_id is not None:
        if variant_id is not None or content_dir is not None:
            raise typer.BadParameter("Apply takes only the preview ID and state/output resolution options")
        result = _apply("rollback", settings.roots.state, apply_id)
    else:
        if variant_id is None:
            raise typer.BadParameter("Rollback preparation requires VARIANT")
        result = plan_rollback(settings.roots, variant_id, services=Services.local())
    emit(result, json_output=json_output)
    raise typer.Exit(0 if result.allowed else 4 if any(f.code == "transaction.io" for f in result.findings) else 3)


def _settings(
    project: Path | None,
    state: Path | None,
    content: Path | None,
    config_path: Path | None,
    variant: str | None,
    *,
    operation: str,
    json_output: bool,
) -> ResolvedSettings:
    from .config import resolve_settings
    from .machine import settings_environment

    options = {
        "project": (project or Path.cwd()).absolute(),
        "state_dir": state.absolute() if state else None,
        "content_dir": content.absolute() if content else None,
        "variant": variant,
    }
    try:
        environment = settings_environment()
        first = resolve_settings(
            options,
            None,
            config_path.absolute() if config_path else None,
            home=Path.home(),
            platform=Services.local().platform,
            environment=environment,
        )
        config_exists = first.config_path.exists()
        value = json.loads(first.config_path.read_bytes()) if config_exists else None
        if config_exists and not isinstance(value, dict):
            raise ValueError("Configuration must be an object")
        if config_path is not None and not config_exists:
            raise ValueError("Explicit configuration file does not exist")
        resolved = resolve_settings(
            options,
            value,
            first.config_path,
            home=Path.home(),
            platform=Services.local().platform,
            environment=environment,
        )
        if variant and not resolved.explicit_content and operation != "status":
            from dataclasses import replace

            from .records import Roots
            from .store import load_ledger, resolve_content_root

            ledger = load_ledger(resolved.roots.state, variant)
            resolved = replace(
                resolved, roots=Roots(resolve_content_root(resolved.roots.state, ledger), resolved.roots.state)
            )
        return resolved
    except (ValueError, OSError) as error:
        emit(
            Result(
                operation,
                variant,
                None,
                False,
                False,
                (Finding("config.invalid", "error", None, None, str(error)),),
                (),
                {},
            ),
            json_output=json_output,
        )
        raise typer.Exit(2) from error


@app.command("adopt")
def adopt_command(
    selection_path: Annotated[Path | None, typer.Option("--selection")] = None,
    variant_id: Annotated[str | None, typer.Option("--variant")] = None,
    base: Annotated[str | None, typer.Option("--base")] = None,
    revision: Annotated[str | None, typer.Option("--ref")] = None,
    evidence_path: Annotated[Path | None, typer.Option("--evidence")] = None,
    intent_path: Annotated[Path | None, typer.Option("--intent")] = None,
    apply_id: Annotated[str | None, typer.Option("--apply")] = None,
    content_dir: Annotated[Path | None, typer.Option("--content-dir")] = None,
    state_dir: Annotated[Path | None, typer.Option("--state-dir")] = None,
    project: Annotated[Path | None, typer.Option("--project")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Track an existing copy; establish original provenance only on explicit apply."""
    from .adoption import plan_adopt
    from .records import Component, Selection, SourceMapping
    from .store import load_ledger

    if apply_id and any(
        v is not None for v in (selection_path, variant_id, base, revision, evidence_path, intent_path, content_dir)
    ):
        raise typer.BadParameter("Apply takes only the preview ID and state/output resolution options")
    settings = _settings(
        project, state_dir, content_dir, config_path, variant_id, operation="adopt", json_output=json_output
    )
    if apply_id:
        result = _apply("adopt", settings.roots.state, apply_id)
    else:
        try:
            if selection_path:
                selection = Selection.from_json(json.loads(selection_path.read_bytes()))
            elif variant_id:
                ledger = load_ledger(settings.roots.state, variant_id)
                if ledger.content_root is None and not settings.explicit_content:
                    raise ValueError("External variant requires --content-dir or a configured local binding")
                if not settings.explicit_content and ledger.content_root is not None:
                    from dataclasses import replace

                    from .records import Roots

                    settings = replace(
                        settings,
                        roots=Roots((settings.roots.state / ledger.content_root).resolve(), settings.roots.state),
                    )
                skills = tuple(
                    Component(next(m.destination for m in ledger.mappings if m.source == c.source), c.name)
                    for c in ledger.components
                )
                selection = Selection(
                    skills,
                    tuple(
                        SourceMapping(m.destination, m.destination)
                        for m in ledger.mappings
                        if m.destination not in {c.source for c in skills}
                    ),
                    ledger.unresolved,
                    (),
                    (),
                )
            else:
                raise ValueError("Adoption requires --selection or --variant")
            evidence = json.loads(evidence_path.read_bytes()) if evidence_path else {}
            if not isinstance(evidence, dict):
                raise ValueError("Evidence must be an object")
            notes = json.loads(intent_path.read_bytes()) if intent_path else None
            if notes is not None and (not isinstance(notes, list) or any(not isinstance(n, str) for n in notes)):
                raise ValueError("Intent must be an array of strings")
            if base is None and (revision is not None or evidence_path is not None):
                raise ValueError("--ref and --evidence require --base")
            kind: Literal["git", "local"] = (
                "git" if base and (revision is not None or "://" in base or base.startswith("git@")) else "local"
            )
            source = SourceSpec(base if kind == "git" else str(Path(base).resolve()), kind, revision) if base else None
            result = plan_adopt(
                settings.roots,
                selection,
                base=source,
                evidence=evidence,
                variant_id=variant_id,
                intent=tuple(notes) if notes is not None else None,
                services=Services.local(),
            )
        except (ValueError, OSError, TypeError) as error:
            emit(
                Result(
                    "adopt",
                    variant_id,
                    None,
                    False,
                    False,
                    (Finding("config.invalid", "error", None, None, str(error)),),
                    (),
                    {},
                ),
                json_output=json_output,
            )
            raise typer.Exit(2) from error
    emit(result, json_output=json_output)
    raise typer.Exit(
        0
        if result.allowed
        else 4
        if any(f.code in {"adopt.io", "transaction.io", "git.missing", "git.failed"} for f in result.findings)
        else 3
    )


@app.command("update")
def update_command(
    variant_id: str,
    source: Annotated[str, typer.Option("--source")],
    revision: str | None = typer.Option(None, "--ref"),
    selection_path: Annotated[Path | None, typer.Option("--selection")] = None,
    mapping_path: Annotated[Path | None, typer.Option("--mapping")] = None,
    content_dir: Annotated[Path | None, typer.Option("--content-dir")] = None,
    state_dir: Annotated[Path | None, typer.Option("--state-dir")] = None,
    project: Annotated[Path | None, typer.Option("--project")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Prepare an editable upstream candidate; mappings join old to new source paths."""
    from .records import Selection
    from .updates import plan_update

    settings = _settings(
        project, state_dir, content_dir, config_path, variant_id, operation="update", json_output=json_output
    )
    try:
        selection = None
        if selection_path is not None:
            value = json.loads(selection_path.read_bytes())
            if not isinstance(value, dict):
                raise ValueError("Selection must be an object")
            selection = Selection.from_json(value)
        mappings = json.loads(mapping_path.read_bytes()) if mapping_path else {}
        if not isinstance(mappings, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in mappings.items()
        ):
            raise ValueError("Mappings must be an object from old source paths to incoming source paths")
    except (ValueError, OSError) as error:
        emit(
            Result(
                "update",
                variant_id,
                None,
                False,
                False,
                (Finding("config.invalid", "error", None, None, str(error)),),
                (),
                {},
            ),
            json_output=json_output,
        )
        raise typer.Exit(2) from error
    try:
        kind: Literal["local", "git"] = (
            "git" if revision is not None or "://" in source or source.startswith("git@") else "local"
        )
        result = plan_update(
            settings.roots,
            variant_id,
            SourceSpec(source if kind == "git" else str(Path(source).resolve()), kind, revision),
            selection=selection,
            mappings=mappings,
            services=Services.local(),
        )
        emit(result, json_output=json_output)
    except Exception as error:
        emit(
            Result(
                "update",
                variant_id,
                None,
                False,
                False,
                (Finding("internal.error", "error", None, None, str(error)),),
                (),
                {},
            ),
            json_output=json_output,
        )
        raise typer.Exit(4) from error
    failure = any(f.code in {"update.io", "git.failed", "git.missing"} for f in result.findings)
    raise typer.Exit(0 if result.allowed else 4 if failure else 3)


@app.command("accept")
def accept_command(
    variant_id: Annotated[str | None, typer.Argument()] = None,
    candidate_id: Annotated[str | None, typer.Option("--candidate")] = None,
    review_path: Annotated[Path | None, typer.Option("--review")] = None,
    intent_path: Annotated[Path | None, typer.Option("--intent")] = None,
    resolutions_path: Annotated[Path | None, typer.Option("--resolutions")] = None,
    apply_id: Annotated[str | None, typer.Option("--apply")] = None,
    content_dir: Annotated[Path | None, typer.Option("--content-dir")] = None,
    state_dir: Annotated[Path | None, typer.Option("--state-dir")] = None,
    project: Annotated[Path | None, typer.Option("--project")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Authorize a complete final candidate, or apply its exact fresh preview."""
    from dataclasses import replace
    from typing import cast

    from .acceptance import plan_accept
    from .previews import _decode
    from .records import Resolution, Review

    settings = _settings(
        project, state_dir, content_dir, config_path, variant_id, operation="accept", json_output=json_output
    )
    if apply_id is not None:
        if any(
            value is not None
            for value in (variant_id, candidate_id, review_path, intent_path, resolutions_path, content_dir)
        ):
            raise typer.BadParameter("Apply takes only the preview ID and state/output resolution options")
        result = _apply("accept", settings.roots.state, apply_id)
    else:
        if variant_id is None or candidate_id is None:
            raise typer.BadParameter("Acceptance requires VARIANT and --candidate")
        try:
            review = None
            if review_path is not None:
                review = cast(Review, _decode(json.loads(review_path.read_bytes()), Review))
                review = replace(review, evidence_path=str(review_path.resolve()))
            resolutions = (
                cast(tuple[Resolution, ...], _decode(json.loads(resolutions_path.read_bytes()), tuple[Resolution, ...]))
                if resolutions_path
                else ()
            )
            intent = (
                cast(tuple[str, ...], _decode(json.loads(intent_path.read_bytes()), tuple[str, ...]))
                if intent_path
                else ()
            )
            result = plan_accept(
                settings.roots,
                variant_id,
                candidate_id,
                review=review,
                intent=intent,
                resolutions=resolutions,
                services=Services.local(),
            )
        except (ValueError, OSError) as error:
            emit(
                Result(
                    "accept",
                    variant_id,
                    None,
                    False,
                    False,
                    (Finding("config.invalid", "error", None, None, str(error)),),
                    (),
                    {},
                ),
                json_output=json_output,
            )
            raise typer.Exit(2) from error
    emit(result, json_output=json_output)
    raise typer.Exit(
        0 if result.allowed else 4 if any(f.code in {"accept.io", "transaction.io"} for f in result.findings) else 3
    )


@app.command("install")
def install_command(
    variant_id: Annotated[str | None, typer.Argument()] = None,
    agents: Annotated[list[str] | None, typer.Option("--agent")] = None,
    scope: Annotated[str | None, typer.Option("--scope")] = None,
    mode: Annotated[str | None, typer.Option("--mode")] = None,
    destination: Annotated[Path | None, typer.Option("--destination")] = None,
    apply_id: Annotated[str | None, typer.Option("--apply")] = None,
    content_dir: Annotated[Path | None, typer.Option("--content-dir")] = None,
    state_dir: Annotated[Path | None, typer.Option("--state-dir")] = None,
    project: Annotated[Path | None, typer.Option("--project")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Preview shared links or independent copies; apply only an exact preview."""
    from typing import cast

    from .installation import plan_install

    settings = _settings(
        project, state_dir, content_dir, config_path, variant_id, operation="install", json_output=json_output
    )
    if apply_id is not None:
        if any(value is not None for value in (variant_id, agents, scope, mode, destination, content_dir)):
            raise typer.BadParameter("Apply takes only the preview ID and state/output resolution options")
        result = _apply("install", settings.roots.state, apply_id)
    else:
        if variant_id is None or not agents:
            raise typer.BadParameter("Installation requires VARIANT and at least one --agent")
        if scope not in {None, "project", "personal"} or mode not in {None, "shared", "copy"}:
            raise typer.BadParameter("Use project|personal scope and shared|copy mode")
        result = plan_install(
            settings.roots,
            variant_id,
            agents=tuple(agents),
            scope=cast(Literal["project", "personal"], scope or "project"),
            mode=cast(Literal["shared", "copy"], mode or "shared"),
            project=settings.project,
            home=Path.home(),
            destination=destination.absolute() if destination else None,
            services=Services.local(),
        )
    emit(result, json_output=json_output)
    raise typer.Exit(0 if result.allowed else 4 if any(f.code == "transaction.io" for f in result.findings) else 3)


def _apply(operation: str, state: Path, preview_id: str) -> Result:
    from dataclasses import replace

    from .previews import load_preview
    from .store import identifier
    from .transactions import apply_preview, failure

    try:
        identifier(preview_id)
        recovery = operation == "rollback" and (state / "previews" / preview_id / "recovery.json").is_file()
        if not recovery and load_preview(state, preview_id).operation != operation:
            return failure(operation, preview_id, "preview.operation", "Preview belongs to a different command")
    except (ValueError, OSError) as error:
        return failure(operation, preview_id, "preview.invalid", str(error))
    return replace(apply_preview(state, preview_id, services=Services.local()), operation=operation)
