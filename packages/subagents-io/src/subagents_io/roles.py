"""Role resolution: a role's config, merged, becomes a `RoleSpec`.

The split this module exists to hold: band 1 *resolves* a role, and the caller
*constructs* the model. `resolve_role_spec` takes the packaged entry and the
workspace override as arguments and returns a frozen spec; `RoleBinding` pairs
that spec with a caller-supplied factory. Nothing here opens a packaged data
file, reads a workspace manifest, or names a provider.

This is the package's third instance of one idiom, not a new one: `routing.py`
resolves over caller-supplied vocabularies, and `SubagentPool` takes a
`price_lookup` rather than importing a price table.

    spec = resolve_role_spec("librarian", packaged_entry, workspace_override)
    binding = RoleBinding(spec, make_llm=lambda: make_bedrock_llm(spec.model_id))
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


@dataclass(frozen=True)
class RoleSpec:
    """What a role resolves to. Provider-free.

    `region` defaults to None, not "us-east-1": that literal is a Bedrock
    fact, `models_io.make_bedrock_llm` already defaults it, and a second copy
    here would be foundation code knowing a provider's geography.

    `max_concurrency` is a field because the runner sizes the pool's semaphore
    from it — concurrency is a property of the pool, and the pool is band 1.
    """

    model_id: str
    backend: str = "bedrock"
    region: str | None = None
    max_tokens: int | None = None
    max_concurrency: int = 3


@dataclass(frozen=True)
class RoleBinding:
    """A resolved spec plus the caller's factory for the model it names.

    A factory rather than a constructed model: `run_all` calls it once per
    item, and a shared client is the caller's decision to make by closing over
    one, not this package's to force.
    """

    spec: RoleSpec
    make_llm: Callable[[], BaseChatModel]


def resolve_role_spec(
    role: str,
    packaged: Mapping[str, Any] | None,
    override: Mapping[str, Any] | None = None,
    *,
    model_override: str | None = None,
    backend_override: str | None = None,
) -> RoleSpec:
    """Merge `override` onto `packaged`, field by field, into a `RoleSpec`.

    `packaged` is the role's own entry, not a catalog — reading a catalog is
    the caller's. `role` is carried only for the error messages.

    Args:
        role: The logical role name, used in errors.
        packaged: The role's packaged entry, or None when it has none.
        override: The workspace-defined entry, or None. Any subset of
            {model_id, backend, region, max_tokens, max_concurrency}; the
            fields it omits keep their packaged values.
        model_override: Wins over the merged `model_id` when given.
        backend_override: Wins over the merged `backend` when given.

    Raises:
        KeyError: when `role` is absent from both sources, and separately when
            the merge yields no `model_id` — reachable only for an
            override-only definition that omits it.
    """
    if packaged is None and override is None:
        raise KeyError(role)
    merged: dict[str, Any] = {**(packaged or {}), **(override or {})}

    if model_override is not None:
        model_id = model_override
    elif "model_id" in merged:
        model_id = merged["model_id"]
    else:
        raise KeyError(
            f"role {role!r} has no model_id: no packaged entry supplied one, and "
            "the override does not set model_id either"
        )

    backend = backend_override if backend_override is not None else merged.get("backend", "bedrock")
    return RoleSpec(
        model_id=model_id,
        backend=backend,
        region=merged.get("region"),
        max_tokens=merged.get("max_tokens"),
        max_concurrency=int(merged.get("max_concurrency", 3)),
    )
