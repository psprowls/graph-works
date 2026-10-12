"""Claude local-file placement and permission gating, with injected user identity.

This models recorded trust in an interactive file-only read. Session acceptance,
headless/SDK state and per-session changes are deliberately not inferred.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .conventions import ResolvedConventions
from .git_state import RepositoryContext
from .records import Finding, Layer, TrustStatus


def resolve_local(
    conventions: ResolvedConventions,
    project: Path,
    home: Path,
    context: RepositoryContext,
    *,
    platform: str,
    user_id: int | None,
) -> ResolvedConventions:
    """Keep legacy starting-directory data below the canonical root local file."""
    local = next(layer for layer in conventions.layers if layer.scope == "local")
    root = context.main
    error = context.error
    if platform == "win32" or project == home or context.root == home:
        return conventions
    if error is None and (root is None or root == project):
        return conventions
    if error is None and root is not None:
        if root == home:
            return conventions
        if user_id is None:
            error = "current user ID was not supplied; repository-root local-file ownership cannot be established"
        else:
            try:
                for path in (root, root / ".git", root / ".claude"):
                    try:
                        owner = path.lstat().st_uid
                    except FileNotFoundError:
                        continue
                    if owner != user_id:
                        return conventions
            except OSError as exc:
                error = f"repository-root local-file ownership could not be read: {exc}"
    if error is not None:
        replacement = (replace(local, location_error=error),)
    else:
        assert root is not None
        # Preserve a legacy file only when present (or its existence is unreadable).
        try:
            legacy = local.path.exists()
        except OSError:
            legacy = True
        replacement = (*((local,) if legacy else ()), replace(local, path=root / ".claude/settings.local.json"))
    return replace(
        conventions,
        layers=tuple(
            item for layer in conventions.layers for item in (replacement if layer.scope == "local" else (layer,))
        ),
    )


def permission_gate(
    layer: Layer,
    project: Path,
    home: Path,
    agent_home: Path,
    context: RepositoryContext,
    trust: TrustStatus,
    findings: list[Finding],
) -> bool:
    """Whether to withhold capability-granting keys from this particular file."""
    if trust.state == "unknown":
        return False  # read.py exposes the existing, explicit assumed-trust policy.
    if layer.scope == "project":
        # Outside git, parent acceptance does not approve this folder's allow rules.
        return trust.state != "trusted" or (context.root is None and trust.match == "ancestor")
    if layer.scope != "local":
        return False
    exact_trust = trust.state == "trusted" and (context.root is not None or trust.match == "exact")
    if exact_trust:
        return False
    try:
        if layer.path.parent.is_symlink():
            return True
    except OSError as exc:
        findings.append(
            Finding("agent-config.local-trust", f"Local symlink status is uncertain: {exc}", str(layer.path))
        )
        return True
    config_home = project == home or (agent_home.name == ".claude" and project == agent_home.parent)
    if config_home and layer.path == project / ".claude/settings.local.json":
        return False
    # Before any recorded acceptance, do not equate "untracked" with approved.
    if trust.state != "trusted":
        return True
    # Only outside-repository parent acceptance reaches here: repository trust
    # uses the main-root key and was handled above. Preserve a tracked result
    # supplied by a custom probe conservatively.
    return layer.git == "committed"
