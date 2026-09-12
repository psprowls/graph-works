"""Scoped config resolution, listing, and the validated write path.

Mechanism only. The catalog is supplied by the caller, the store is supplied by
the caller, the environment mapping is supplied by the caller, and so is the
projection target. This module knows no key name, no file name, and no
variable name.

Precedence for reads: env var counterpart > local explicit value > base
explicit value > entry default. The two explicit tiers collapse into one for
any store that is not a `LayeredStore`, which is every store this package
resolves against unless the caller supplies a layered one. Reads are
fail-open — a malformed env value resolves to its raw string rather than
raising, and the consumer surfaces the problem.

`environ` is a **required** keyword. The seed defaulted it to `os.environ`,
which read the live process environment on the package's own initiative; here
`import os` is absent from this module entirely, so "config-io reads only the
variables the caller named, from a mapping the caller handed it" is structural
rather than a promise. `tests/test_boundaries.py` holds it that way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from config_io import dotted
from config_io.entries import ConfigEntry, Resolved, coerce, find_entry, unknown_key_error
from config_io.errors import (
    EnvOnlyKeyError,
    InvalidValueError,
    LinkFileKeyError,
    ProvenanceKeyError,
    ReadOnlyKeyError,
    RegistryError,
    SecretKeyError,
    StoreValidationError,
)
from config_io.projection import write_projection
from config_io.store import ConfigStore, LayeredStore


def resolve_key(
    catalog: Sequence[ConfigEntry],
    key: str,
    *,
    store: ConfigStore,
    environ: Mapping[str, str],
) -> Resolved:
    """Resolve one key across env > local explicit > base explicit > default."""
    entry = find_entry(catalog, key)
    if entry is None:
        raise unknown_key_error(catalog, key)
    if entry.env_var and str(environ.get(entry.env_var, "")).strip():
        raw_env = environ[entry.env_var]
        # Coerce so Resolved.value is typed like the other tiers, but stay
        # fail-open: a malformed env value resolves to the raw string.
        try:
            value = coerce(entry, raw_env)
        except RegistryError:
            value = raw_env
        return Resolved(key, value, "env", entry, _explicit(entry, key, store))
    if entry.kind == "manifest":
        explicit = dotted.get(store.read_explicit(), key)
        if explicit is not None:
            # A layered store's explicit view is already merged, so `explicit`
            # is the effective value either way. Asking the upper layer
            # separately is only how the *origin* is decided — and the lower
            # layer's value is what the caller wants to see as shadowed.
            if isinstance(store, LayeredStore) and dotted.get(store.read_overlay_explicit(), key) is not None:
                return Resolved(key, explicit, "local", entry, dotted.get(store.read_base_explicit(), key))
            return Resolved(key, explicit, "manifest", entry)
    return Resolved(key, entry.default, "default", entry)


def _explicit(entry: ConfigEntry, key: str, store: ConfigStore) -> object | None:
    """The stored explicit value hidden behind an env override, if any."""
    if entry.kind != "manifest":
        return None
    return dotted.get(store.read_explicit(), key)


def expand_wildcards(catalog: Sequence[ConfigEntry], *, store: ConfigStore) -> list[str]:
    """Concrete stored keys matching a wildcard catalog entry.

    Handles both wildcard shapes: `roles.*.<field>` (head plus a trailing
    field) and `plugin.backend_overrides.*` (head, no field).
    """
    raw = store.read_explicit()
    concrete: list[str] = []
    for entry in catalog:
        if "*" not in entry.key:
            continue
        head, _, tail = entry.key.partition(".*")
        block = dotted.get(raw, head)
        if not isinstance(block, dict):
            continue
        field = tail.lstrip(".")
        for name, fields in block.items():
            if not field:
                concrete.append(f"{head}.{name}")
            elif isinstance(fields, dict) and field in fields:
                concrete.append(f"{head}.{name}.{field}")
    return concrete


def resolve_all(
    catalog: Sequence[ConfigEntry],
    *,
    store: ConfigStore,
    environ: Mapping[str, str],
) -> list[Resolved]:
    """One `Resolved` per concrete catalog key, plus every wildcard expansion."""
    keys = [entry.key for entry in catalog if "*" not in entry.key] + expand_wildcards(catalog, store=store)
    return [resolve_key(catalog, key, store=store, environ=environ) for key in keys]


# --- the write path ---------------------------------------------------------
#
# Refusal selection is a table from write_policy to error class. There are no
# key names in it and no file names in the messages: the generic sentence comes
# from the policy, and any remediation wording comes from the catalog's own
# `write_hint`.
#
# Order: unknown-key, then policy, then secret, then env-only. Unknown
# necessarily comes first because the policy now lives on the entry — the seed
# could check link-file/provenance by name ahead of the catalog only because
# those keys were undeclared. Declaring them is the migration obligation this
# design places on the caller, and it makes the two orders equivalent.

_POLICY_REFUSALS: dict[str, type[RegistryError]] = {
    "link-file": LinkFileKeyError,
    "provenance": ProvenanceKeyError,
    "read-only": ReadOnlyKeyError,
}

_POLICY_MESSAGES: dict[str, str] = {
    "link-file": "'{key}' is a hand-edited link-file key and is not written programmatically.",
    "provenance": "'{key}' is tooling-stamped provenance and is not writable.",
    "read-only": "'{key}' is declared read-only in the catalog and is hand-edit only.",
}


def _refusal(error: type[RegistryError], message: str, entry: ConfigEntry) -> RegistryError:
    """Attach the catalog's own remediation sentence, when it supplies one."""
    return error(f"{message} {entry.write_hint}" if entry.write_hint else message)


def _writable_entry(catalog: Sequence[ConfigEntry], key: str) -> ConfigEntry:
    """The entry for `key`, or the refusal that says why it cannot be written."""
    entry = find_entry(catalog, key)
    if entry is None:
        raise unknown_key_error(catalog, key)
    if entry.write_policy != "writable":
        raise _refusal(
            _POLICY_REFUSALS[entry.write_policy],
            _POLICY_MESSAGES[entry.write_policy].format(key=key),
            entry,
        )
    if entry.secret:
        raise _refusal(
            SecretKeyError,
            f"'{key}' is a secret and is never stored; set {entry.env_var or key} in the environment instead.",
            entry,
        )
    if entry.kind == "env-only":
        raise _refusal(
            EnvOnlyKeyError,
            f"'{key}' is env-only and is never stored; set {entry.env_var or key} in the environment instead.",
            entry,
        )
    return entry


def set_key(
    catalog: Sequence[ConfigEntry],
    key: str,
    raw_value: str,
    *,
    store: ConfigStore,
    projection: Path | None = None,
) -> Resolved:
    """Write `key` through the validated, rollback-safe path.

    Pass `projection=` to regenerate the JSON projection on success. Omitting
    it leaves any existing projection stale — that is documentation, not a
    type, and it is the accepted cost of keeping these as plain functions.
    """
    entry = _writable_entry(catalog, key)
    value = coerce(entry, raw_value)
    data = store.read()
    dotted.set_in(data, key, value)
    snapshot = store.snapshot()
    store.write(data)
    # Validate: a coerced value can be individually well-formed and still
    # violate the store's own invariants. Restore rather than leave it broken.
    try:
        store.read()
    except StoreValidationError as exc:
        store.restore(snapshot)
        raise InvalidValueError(f"'{key}': value {value!r} leaves the store invalid: {exc}") from exc
    # Verify persistence: any lossy store must not silently drop a written key.
    # A key absent *because* its value equals the entry default is fine — a
    # store may legitimately omit that, and it is equivalent to unset.
    persisted = dotted.get(store.read_explicit(), key)
    if persisted != value and not (persisted is None and value == entry.default):
        store.restore(snapshot)
        raise RegistryError(
            f"'{key}' did not survive the write — the store dropped it. "
            "The catalog entry and the store's serialiser are out of sync."
        )
    if projection is not None:
        write_projection(store, projection)
    return Resolved(key, value, "manifest", entry)


def unset_key(
    catalog: Sequence[ConfigEntry],
    key: str,
    *,
    store: ConfigStore,
    projection: Path | None = None,
) -> bool:
    """Remove `key`'s explicit stored value; return whether anything changed."""
    _writable_entry(catalog, key)
    data = store.read()
    removed = dotted.unset_in(data, key)
    if removed:
        store.write(data)
        if projection is not None:
            write_projection(store, projection)
    return removed
