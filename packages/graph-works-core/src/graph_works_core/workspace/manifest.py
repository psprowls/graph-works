"""`<root>/workspace.yaml` — the workspace's one configuration surface.

Four layout overrides, five role-override wildcards, the bundle declarations
(`repositories`, `ignore`, `state_gate`), a version, a provenance stamp, a
topic, and one `env-only` entry documenting the discovery override.
The plugin backends belong to `models-io`; the guidance and workflow blocks
are deferred until their consumers exist.

`roles` is the one line C2 amends. The role *concept* does belong to
`subagents-io` and `models-io` — `resolve_role_spec` takes both mappings as
arguments by design — but the *key declaration* belongs to whoever owns the
file it lives in, and that is this package. Storing it here is what makes
`gw config set roles.librarian.model_id …` work through config-io's validated,
rollback-safe path instead of inventing a second config surface. Regrouping
the resolved keys into per-role dicts is `roles.py`'s; `Manifest` itself is
untouched, because roles are not layout.

`version: 1` is a **fresh format**, not v3 of `.graph-wiki.yaml`. A manifest at
any other version raises: there is no migration path, and converting the live
workspace belongs to a later work item.

The catalog is the whole reason this is `config-io` rather than hand-rolled
validators: `gw config get/set`, the env mapping and the JSON projection all
come for free, and roughly 250 lines of bespoke checking does not get written.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from config_io import (
    ConfigEntry,
    PlainYamlStore,
    Resolved,
    StoreValidationError,
    dotted,
    resolve_all,
    resolve_key,
    set_key,
)
from subagents_io.dispatch import DISPATCH_MODES

from graph_works_core.workspace.errors import WorkspaceError, WorkspaceNotFound
from graph_works_core.workspace.layout import (
    DEFAULT_BUNDLE_DIR,
    DEFAULT_CONFIG_DIR,
    MANIFEST_FILENAME,
    WorkspaceLayout,
)

#: The one format this package reads.
MANIFEST_VERSION = 1

#: The provider backends a role may name. Declared here because both the
#: catalog entry below and `roles.role_spec`'s refusal read it, and a second
#: literal is how the two would drift apart.
BACKENDS: tuple[str, ...] = ("bedrock", "vercel", "claude_code")

#: The discovery override. `discovery.resolve` reads the variable directly; the
#: catalog carries it as an `env-only` entry anyway, because the catalog is
#: where a key gets documented and a key the CLI cannot name is one users have
#: to be told about in prose.
WORKSPACE_DIR_ENV = "GRAPH_WORKS_DIR"

CATALOG: tuple[ConfigEntry, ...] = (
    ConfigEntry(
        key="version",
        type="int",
        default=MANIFEST_VERSION,
        description="Manifest format version. There is no migration path; a foreign version is refused.",
        write_policy="read-only",
    ),
    ConfigEntry(
        key="initialized_at",
        type="str",
        default="",
        description="ISO date this workspace was initialized.",
        write_policy="provenance",
    ),
    ConfigEntry(
        key="topic",
        type="str",
        default=None,
        description="Display name for this workspace.",
    ),
    ConfigEntry(
        key="layout.bundle_dir",
        type="str",
        default=DEFAULT_BUNDLE_DIR,
        description="The OKF bundle directory, relative to the workspace root.",
    ),
    ConfigEntry(
        key="layout.config_dir",
        type="str",
        default=DEFAULT_CONFIG_DIR,
        description=(
            "The control plane — declarations (`schema/`, `sections/`, `tags.yaml`) "
            "and the `.gitignore` anchor — relative to the root."
        ),
    ),
    ConfigEntry(
        key="layout.cache_dir",
        type="str",
        default=None,
        description=(
            "Gitignored machine state — the graph database — relative to the root. "
            "Absent derives from `layout.config_dir`."
        ),
    ),
    ConfigEntry(
        key="layout.worktrees_dir",
        type="str",
        default=None,
        description=("Gitignored feature worktrees, relative to the root. Absent derives from `layout.config_dir`."),
    ),
    # The bundle declarations. `repositories.*.path` mirrors the
    # `roles.*.<field>` wildcard shape: one entry per repo, keyed on name.
    ConfigEntry(
        key="repositories.*.path",
        type="str",
        default=None,
        description="Checkout path for one repository this workspace scans, relative to the workspace root.",
    ),
    ConfigEntry(
        key="repositories.*.ignore",
        type="list[str]",
        default=None,
        description="Per-repository git glob pathspecs to exclude, merged with the global `ignore:` list.",
    ),
    ConfigEntry(
        key="ignore",
        type="list[str]",
        default=[],
        description="Global git glob pathspecs excluded from every repository's scan.",
    ),
    ConfigEntry(
        key="state_gate.enabled",
        type="bool",
        default=True,
        description="Whether the state gate blocks a scan on an unclean branch.",
    ),
    ConfigEntry(
        key="state_gate.branches",
        type="list[str]",
        default=["main"],
        description="Branches the state gate treats as clean-required. Empty list is valid and intentional.",
    ),
    # The workspace role override. Five wildcard entries rather than one per
    # role per field: `config_io.expand_wildcards` handles the
    # `roles.*.<field>` shape natively, so the catalog stays the size of the
    # field list and a new role costs nothing here.
    #
    # Every default is `None` on purpose. `expand_wildcards` yields only keys
    # actually present in the file, and a `None` default keeps an absent field
    # absent rather than materializing a value that would shadow the packaged
    # one. Partial override is the whole point of the layering.
    ConfigEntry(
        key="roles.*.model_id",
        type="str",
        default=None,
        description="Model id for one role, overriding the packaged catalog.",
    ),
    ConfigEntry(
        key="roles.*.backend",
        type="str",
        default=None,
        description="Provider backend for one role.",
        allowed=BACKENDS,
    ),
    ConfigEntry(
        key="roles.*.region",
        type="str",
        default=None,
        description="Provider region for one role. Absent means the provider's own default.",
    ),
    ConfigEntry(
        key="roles.*.max_tokens",
        type="int",
        default=None,
        description="Output token cap for one role.",
    ),
    ConfigEntry(
        key="roles.*.max_concurrency",
        type="int",
        default=None,
        description="Fan-out width for one role, read by the subagent pool's semaphore.",
    ),
    # The dispatch table override. Keyed on `variant` alone, not
    # `(stage, variant)`: the eight variants partition cleanly across the four
    # stages, so variant is already a total key -- and `expand_wildcards`
    # supports exactly one `*` segment, so a two-wildcard shape would not
    # resolve at all.
    #
    # Every default is `None`, for `roles.*`'s reason: an unset field must stay
    # absent rather than shadow the packaged value that `pipeline.py` layers
    # under it.
    ConfigEntry(
        key="workflow.pipeline.*.skill",
        type="str",
        default=None,
        description="Stage skill for one dispatch variant, overriding the packaged table.",
    ),
    ConfigEntry(
        key="workflow.pipeline.*.mode",
        type="str",
        default=None,
        description="Whether a worker for this variant expects a human in the room.",
        allowed=tuple(sorted(DISPATCH_MODES)),
    ),
    ConfigEntry(
        key="workflow.pipeline.*.prompt_tail",
        type="str",
        default=None,
        description=(
            "Extra prompt line for this variant. The one place a vendor command may appear: "
            "core assembles only vendor-neutral lines. "
            "`{slug}`, `{key}`, `{phase}`, `{workspace}` and `{merge_target}` are substituted."
        ),
    ),
    # The auto-drive shell's two scalars. Concrete rather than wildcard: the
    # `models` / `overrides` rules block underneath is a nested list-of-mappings
    # that no `ConfigEntry` type can express, so `run_orchestrate` reads it raw
    # and hands it to `subagents_io.routing.validate_rules`. These two are the
    # part that *is* expressible, and expressing them is what keeps
    # `gw config set workflow.auto_drive.max_parallel 3` on config-io's
    # validated, rollback-safe path.
    ConfigEntry(
        key="workflow.auto_drive.max_parallel",
        type="int",
        default=2,
        description="How many workers auto-drive may have in flight at once.",
    ),
    ConfigEntry(
        key="workflow.auto_drive.permission_mode",
        type="str",
        default="bypassPermissions",
        description="Permission mode a dispatched worker session runs under.",
    ),
    ConfigEntry(
        key="workflow.auto_drive.supervise_merges",
        type="bool",
        default=False,
        description=(
            "Mirror every finish-stage merge question to the human. "
            "Off by default: auto-drive answers `merge` itself for a non-root "
            "child, whose merge target is the epic's own integration branch."
        ),
    ),
    ConfigEntry(
        key="workspace.dir",
        type="str",
        default=None,
        description=f"Workspace directory override. Set {WORKSPACE_DIR_ENV} in the environment.",
        kind="env-only",
        env_var=WORKSPACE_DIR_ENV,
    ),
)


@dataclass(frozen=True, slots=True)
class Manifest:
    """One `workspace.yaml`, resolved. Every field is a value, never a path:
    turning the four overrides into resolved paths is `layout_for`'s job.

    `cache_dir` and `worktrees_dir` are `None` when the manifest does not
    override them — `layout_for` reads `None` as "derive from `config_dir`",
    the same absent-means-derive contract the catalog entries above declare.
    """

    version: int
    initialized_at: str
    topic: str | None
    bundle_dir: str
    config_dir: str
    cache_dir: str | None
    worktrees_dir: str | None


def defaults() -> Manifest:
    """What a workspace with no overrides behaves as."""
    return Manifest(
        version=MANIFEST_VERSION,
        initialized_at="",
        topic=None,
        bundle_dir=DEFAULT_BUNDLE_DIR,
        config_dir=DEFAULT_CONFIG_DIR,
        cache_dir=None,
        worktrees_dir=None,
    )


def _text(values: Mapping[str, object], key: str, fallback: str) -> str:
    value = values.get(key)
    return value if isinstance(value, str) and value.strip() else fallback


def _optional_text(values: Mapping[str, object], key: str) -> str | None:
    value = values.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _version(value: object, path: Path) -> int:
    # `bool` is an `int`: a manifest carrying `version: true` is a broken
    # manifest, not version 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkspaceError(f"{path}: `version` must be an integer, got {value!r}")
    if value != MANIFEST_VERSION:
        raise WorkspaceError(
            f"{path}: manifest version {value} is not supported — this package reads version "
            f"{MANIFEST_VERSION} only, and there is no migration path."
        )
    return value


def check_version(values: Mapping[str, object], path: Path) -> int:
    """Refuse a manifest this package does not read.

    Shared by `read` and by `roles.workspace_roles`, so the two can never
    disagree about what a readable manifest is. An absent `version` is
    `MANIFEST_VERSION`: that is what lets a caller hand this a raw
    `read_explicit()` mapping — `{}` for an absent or empty file — without an
    `exists()` branch of its own.
    """
    return _version(values.get("version", MANIFEST_VERSION), path)


def read(path: str | Path, *, environ: Mapping[str, str] | None = None) -> Manifest:
    """Read and resolve *path*.

    Raises `WorkspaceNotFound` when the file is absent — that is the sentence a
    caller who has not initialized a workspace needs — and `WorkspaceError` for
    a manifest that is not YAML, not a mapping at the top level, or at a
    version this package does not read.

    *environ* exists so the `env-only` entry resolves without reading the
    process environment on this package's own initiative, matching
    `config_io.resolve_all`'s required keyword. Nothing on `Manifest` comes
    from it: the one env-mapped key is the workspace directory, which
    `discovery` has already used by the time this is called.
    """
    path = Path(path)
    if not path.exists():
        raise WorkspaceNotFound(
            f"{path}: no {MANIFEST_FILENAME} here, so this is not a graph-works workspace. "
            "Initialize one with `graph_works_core.plan_init` / `apply_init`."
        )
    try:
        resolved = resolve_all(CATALOG, store=PlainYamlStore(path), environ={} if environ is None else environ)
    except StoreValidationError as exc:
        raise WorkspaceError(f"{path}: {exc}") from exc
    values: dict[str, object] = {item.key: item.value for item in resolved}
    topic = values.get("topic")
    return Manifest(
        version=check_version(values, path),
        initialized_at=_text(values, "initialized_at", ""),
        topic=topic if isinstance(topic, str) and topic.strip() else None,
        bundle_dir=_text(values, "layout.bundle_dir", DEFAULT_BUNDLE_DIR),
        config_dir=_text(values, "layout.config_dir", DEFAULT_CONFIG_DIR),
        cache_dir=_optional_text(values, "layout.cache_dir"),
        worktrees_dir=_optional_text(values, "layout.worktrees_dir"),
    )


def checked(resolved: Resolved, *, source: Path) -> object:
    """A manifest-stored value that matches its own catalog entry, or a refusal.

    `_routing_rules` in `commands/orchestrate.py` states the policy this
    generalizes: a hand-edited manifest bypasses config-io's set-time checks,
    so a reader that trusts a stored value trusts a file nothing validated.

    **Only `origin == "manifest"` is checked.** An env value stays fail-open —
    that is config-io's documented rule and this is not the place to
    relitigate it — and a default is catalog-authored, so trusted by
    construction.

    Raises:
        WorkspaceError: for a stored value whose shape contradicts the
            catalog's declared `type` or `allowed`.
    """
    if resolved.origin != "manifest":
        return resolved.value
    value = resolved.value
    entry = resolved.entry
    where = f"{source}: {resolved.key}"
    if entry.type == "int":
        # `bool` is an `int`: `max_parallel: true` is a broken manifest, not 1.
        # Without this carve-out the check passes the value it exists to catch.
        if isinstance(value, bool) or not isinstance(value, int):
            raise WorkspaceError(f"{where}: expects an integer, got {value!r}")
    elif entry.type == "bool":
        if not isinstance(value, bool):
            raise WorkspaceError(f"{where}: expects a boolean, got {value!r}")
    elif entry.type == "list[str]":
        if not isinstance(value, list) or not all(isinstance(member, str) for member in value):
            raise WorkspaceError(f"{where}: expects a list of strings, got {value!r}")
    else:
        if not isinstance(value, str):
            raise WorkspaceError(f"{where}: expects a string, got {value!r}")
        if entry.allowed and value not in entry.allowed:
            raise WorkspaceError(f"{where}: must be one of {sorted(entry.allowed)}, got {value!r}")
    return value


def _check_resolved(resolved: Resolved, *, explicit: Mapping[str, object], source: Path) -> Resolved:
    """Refuse a malformed stored value while preserving its resolution metadata."""
    # `resolve_key` treats a stored null as unset and returns the catalog
    # default, so the raw mapping is the only place a deliberate null remains
    # visible. Optional keys whose default is already None retain their normal
    # unset meaning; a null that hides a real default is the ambiguous case.
    if (
        resolved.entry.default is not None
        and dotted.has(explicit, resolved.key)
        and dotted.get(explicit, resolved.key) is None
    ):
        raise WorkspaceError(
            f"{source}: {resolved.key}: is explicitly null. This key has a real default, so a "
            "null here inherits it while reading as a deliberate setting — remove the line, or set a value."
        )
    checked(resolved, source=source)
    return resolved


def resolve_checked_key(layout: WorkspaceLayout, key: str, *, environ: Mapping[str, str]) -> Resolved:
    """Resolve one manifest key and reject malformed hand-edited values."""
    store = PlainYamlStore(layout.manifest_path)
    resolved = resolve_key(CATALOG, key, store=store, environ=environ)
    return _check_resolved(resolved, explicit=store.read_explicit(), source=layout.manifest_path)


def resolve_checked_all(layout: WorkspaceLayout, *, environ: Mapping[str, str]) -> list[Resolved]:
    """Resolve every manifest key and reject malformed hand-edited values."""
    store = PlainYamlStore(layout.manifest_path)
    explicit = store.read_explicit()
    return [
        _check_resolved(resolved, explicit=explicit, source=layout.manifest_path)
        for resolved in resolve_all(CATALOG, store=store, environ=environ)
    ]


def _checked_value(layout: WorkspaceLayout, key: str) -> object:
    """One concrete key's checked value for typed core consumers."""
    return resolve_checked_key(layout, key, environ={}).value


def checked_int(layout: WorkspaceLayout, key: str) -> int:
    """*key* from this workspace's manifest as an `int`, or a refusal."""
    value = _checked_value(layout, key)
    if not isinstance(value, int):  # pragma: no cover -- `checked` refuses every non-int stored value,
        # and a catalog default is authored at the declared type; duplicating the type test here would
        # put the catalog's declared type in two places
        raise WorkspaceError(f"{layout.manifest_path}: {key}: expects an integer, got {value!r}")
    return value


def checked_str(layout: WorkspaceLayout, key: str) -> str:
    """*key* from this workspace's manifest as a `str`, or a refusal."""
    value = _checked_value(layout, key)
    if not isinstance(value, str):  # pragma: no cover -- see `checked_int`
        raise WorkspaceError(f"{layout.manifest_path}: {key}: expects a string, got {value!r}")
    return value


def checked_bool(layout: WorkspaceLayout, key: str) -> bool:
    """*key* from this workspace's manifest as a `bool`, or a refusal.

    `bool` is tested before anything int-shaped -- `isinstance(True, int)` is
    true, so `checked()` carves bools out of the `int` branch (see line ~392)
    before either helper's own guard runs; that's why both `checked_int` and
    `checked_bool`'s own `isinstance` raises are unreachable and marked
    `# pragma: no cover`.
    """
    value = _checked_value(layout, key)
    if not isinstance(value, bool):  # pragma: no cover -- see `checked_int`
        raise WorkspaceError(f"{layout.manifest_path}: {key}: expects a boolean, got {value!r}")
    return value


def set_value(path: str | Path, key: str, raw_value: str) -> Resolved:
    """Write one key through `config_io`'s validated, rollback-safe path.

    Thin on purpose. The catalog is the only thing this package adds, and
    binding it here is what keeps every caller from having to import it —
    refusals, coercion and the persistence check are all `config-io`'s.
    """
    return set_key(CATALOG, key, raw_value, store=PlainYamlStore(Path(path)))


def render_initial(
    *,
    today: date,
    topic: str | None = None,
    relay_tail: str | None = None,
    repositories: Mapping[str, str] | None = None,
    ignore: Sequence[str] = (),
) -> str:
    """The manifest a fresh workspace is born with.

    Rendered by hand rather than dumped: the scalars and blocks here are not
    worth a YAML serializer this package does not otherwise declare, and
    `json.dumps` is the minimal correct YAML double-quoted scalar — the same
    call `code_wiki_okf.seed_files` makes, for the same reason.

    The four layout keys are deliberately absent: an unset override *is* the
    default, and writing them out would freeze today's defaults into every new
    workspace. `repositories`/`ignore` are the opposite case: they carry the
    bootstrap-time content itself (the repo this workspace was created for,
    the scanner excludes derived from the layout), not an override of a
    default, so they are always rendered — an empty `repositories: {}` when
    there is no repo root, same as `_repositories_text` used to write.

    **`relay_tail` is the exception among the *override* keys, and the
    distinction is the reason it is safe.** `workflow.pipeline.branch.prompt_tail`
    has **no packaged default**
    (`pipeline.PACKAGED_PIPELINE["branch"].prompt_tail` is `None`), so an unset
    key there is a *hole* rather than an inherited default — a `relay` worker
    dispatched without it falls into an interactive menu with nobody watching.
    The layout reasoning above applies to keys that have a real default to
    inherit; this one does not. Apply that test, not the rule, to the next key.
    """
    lines = [f"version: {MANIFEST_VERSION}", f"initialized_at: {json.dumps(today.isoformat())}"]
    if topic is not None and topic.strip():
        lines.append(f"topic: {json.dumps(topic)}")
    if relay_tail is not None and relay_tail.strip():
        lines.extend(
            [
                "workflow:",
                "  pipeline:",
                "    branch:",
                f"      prompt_tail: {json.dumps(relay_tail)}",
            ]
        )
    if repositories:
        lines.append("repositories:")
        for name, path in repositories.items():
            lines.append(f"  {json.dumps(name)}:")
            lines.append(f"    path: {json.dumps(path)}")
    else:
        lines.append("repositories: {}")
    if ignore:
        lines.append("ignore:")
        lines.extend(f"  - {json.dumps(pattern)}" for pattern in ignore)
    else:
        lines.append("ignore: []")
    return "\n".join(lines) + "\n"


__all__ = [
    "BACKENDS",
    "CATALOG",
    "MANIFEST_VERSION",
    "WORKSPACE_DIR_ENV",
    "Manifest",
    "check_version",
    "checked",
    "checked_bool",
    "checked_int",
    "checked_str",
    "defaults",
    "read",
    "render_initial",
    "resolve_checked_all",
    "resolve_checked_key",
    "set_value",
]
