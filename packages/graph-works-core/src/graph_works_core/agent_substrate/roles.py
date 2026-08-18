"""Role to model — the one module in this package that reads anything.

Three layers, in increasing precedence:

1. **Packaged** — `[roles.<role>]` from `graph_works_core/models.toml`, read
   through `importlib.resources` so it resolves under editable, wheel and zip
   installs alike.
2. **Workspace** — `roles.<role>.<field>` from `workspace.yaml`, when a layout
   is supplied.
3. **Explicit** — the `model_override=` / `backend_override=` arguments.

The merge itself is `subagents_io.resolve_role_spec`'s. This module supplies
both mappings and never layers field by field on its own.

`layout` is an argument, never a discovery call. `None` means packaged-only;
passing a layout is how a caller opts into workspace overrides. Injecting it is
what keeps this module testable without an `except ImportError` /
`except RuntimeError` pair around a resolution call it would otherwise have to
make itself.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache, partial
from importlib import resources
from typing import Any

from config_io import PlainYamlStore, StoreValidationError, dotted, expand_wildcards
from langchain_core.language_models import BaseChatModel
from models_io import make_bedrock_llm, make_gateway_llm
from subagents_io.roles import RoleBinding, RoleSpec, resolve_role_spec

from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.manifest import BACKENDS, check_version
from graph_works_core.workspace.manifest import CATALOG as MANIFEST_CATALOG

#: The packaged catalog, as package data.
CATALOG_FILENAME = "models.toml"

#: The gateway credential. models-io never reads the environment, so reading
#: this is C2's job — and the variable's own name is handed back to the
#: constructor as `credential_hint` so its refusal names what to set.
GATEWAY_API_KEY_ENV = "AI_GATEWAY_API_KEY"


@lru_cache(maxsize=1)
def _packaged_roles() -> dict[str, dict[str, Any]]:
    with resources.files("graph_works_core").joinpath(CATALOG_FILENAME).open("rb") as handle:
        data: dict[str, Any] = tomllib.load(handle)
    roles: dict[str, dict[str, Any]] = data["roles"]
    return roles


def packaged_roles() -> dict[str, dict[str, Any]]:
    """Every `[roles.<name>]` block in the packaged catalog.

    Parsed once per process -- it is package data and cannot change at runtime
    -- and copied per call, so the caller owns what it gets. One level of copy
    is enough: `resolve_role_spec` reads scalars out of a role entry and never
    descends further.
    """
    return {role: dict(fields) for role, fields in _packaged_roles().items()}


def workspace_roles(layout: WorkspaceLayout) -> dict[str, dict[str, Any]]:
    """The `roles.<name>.<field>` overrides in this workspace's manifest.

    The manifest catalog now carries two wildcard families -- `roles.*` and
    `workflow.pipeline.*` -- so every expansion is filtered by prefix here
    rather than regrouped wholesale. `test_manifest.py`'s
    `test_every_wildcard_catalog_entry_is_a_role_or_pipeline_entry` is what
    keeps the pair exhaustive; a third family would be the point to hoist a
    shared helper, and two is not.

    `expand_wildcards` yields only keys actually present in the file, so an
    absent field is absent. An explicit `null` is dropped for the same reason:
    a field the workspace did not set must not shadow the packaged one.

    Two parses, constant in the number of roles: one here and one inside
    `expand_wildcards`. `dotted.get` replaces the per-key `resolve_key` --
    equivalent for these entries, which declare no `env_var` and default to
    `None`, and whose keys all came out of the same catalog.
    """
    store = PlainYamlStore(layout.manifest_path)
    try:
        raw = store.read_explicit()
    except StoreValidationError as exc:
        raise WorkspaceError(f"{layout.manifest_path}: {exc}") from exc
    check_version(raw, layout.manifest_path)
    overrides: dict[str, dict[str, Any]] = {}
    for key in expand_wildcards(MANIFEST_CATALOG, store=store):
        if not key.startswith("roles."):
            continue
        _, role, field = key.split(".", 2)
        value = dotted.get(raw, key)
        if value is None:
            continue
        overrides.setdefault(role, {})[field] = value
    return overrides


def role_spec(
    role: str,
    *,
    layout: WorkspaceLayout | None = None,
    model_override: str | None = None,
    backend_override: str | None = None,
) -> RoleSpec:
    """Resolve *role* to a frozen, provider-free spec.

    The backend is validated after the merge rather than at the argument, so
    one gate covers all three doors: the explicit override, a hand-edited
    manifest (`config_io` applies `allowed=` on write, not on read), and a typo
    in the packaged catalog.

    Raises:
        KeyError: when *role* is absent from both sources — with a message
            naming the `workspace.yaml` key that would define it, since the
            bare `KeyError(role)` `resolve_role_spec` raises reaches a user as
            `drift_propagator: 'drift_propagator'` and explains nothing — and
            separately when the merge yields no `model_id`.
        WorkspaceError: when the resolved backend is not one of `BACKENDS`.
    """
    override = None if layout is None else workspace_roles(layout).get(role)
    if override is None and role not in packaged_roles():
        raise KeyError(
            f"no role {role!r}: it is not in the packaged catalog, and this "
            f"workspace does not define it. Set `roles.{role}.model_id` in "
            f"workspace.yaml, or run `gw config set roles.{role}.model_id <model>`."
        )
    spec = resolve_role_spec(
        role,
        packaged_roles().get(role),
        override,
        model_override=model_override,
        backend_override=backend_override,
    )
    if spec.backend not in BACKENDS:
        raise WorkspaceError(f"role {role!r}: unknown backend {spec.backend!r} — expected one of {sorted(BACKENDS)}")
    return spec


def role_binding(
    role: str,
    *,
    layout: WorkspaceLayout | None = None,
    model_override: str | None = None,
    backend_override: str | None = None,
) -> RoleBinding:
    """The spec plus a factory for the model it names.

    What `SubagentPool` wants: it calls the factory once per item, so a
    constructed model would force one client on the whole fan-out.
    """
    spec = role_spec(role, layout=layout, model_override=model_override, backend_override=backend_override)
    return RoleBinding(spec=spec, make_llm=partial(_construct, spec))


def make_llm(
    role: str,
    *,
    layout: WorkspaceLayout | None = None,
    model_override: str | None = None,
    backend_override: str | None = None,
) -> BaseChatModel:
    """One constructed chat model for *role*.

    What the `agent_loop` call sites want. `role_binding` and this share one
    resolution path: shipping only the binding would suffix every non-pool
    call site with `.make_llm()`, and shipping only this would push binding
    assembly into each vertical.
    """
    return _construct(role_spec(role, layout=layout, model_override=model_override, backend_override=backend_override))


def _construct(spec: RoleSpec) -> BaseChatModel:
    """Dispatch a resolved spec to its provider constructor.

    `region` is a conditional keyword rather than `spec.region or "us-east-1"`:
    that literal is `make_bedrock_llm`'s own default, and a second copy of it
    here is exactly what band 1 declined to write. Any backend other than
    `"vercel"` is Bedrock — by elimination, not by assumption: `role_spec` has
    already refused anything outside `BACKENDS`.
    """
    if spec.backend == "vercel":
        return make_gateway_llm(
            spec.model_id,
            api_key=os.environ.get(GATEWAY_API_KEY_ENV, ""),
            credential_hint=GATEWAY_API_KEY_ENV,
            max_tokens=spec.max_tokens,
        )
    if spec.region is None:
        return make_bedrock_llm(spec.model_id, max_tokens=spec.max_tokens)
    return make_bedrock_llm(spec.model_id, region=spec.region, max_tokens=spec.max_tokens)


__all__ = [
    "CATALOG_FILENAME",
    "GATEWAY_API_KEY_ENV",
    "make_llm",
    "packaged_roles",
    "role_binding",
    "role_spec",
    "workspace_roles",
]
