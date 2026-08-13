"""The catalog entry schema, the write-policy vocabulary, and value coercion.

Mechanism only. The catalog of concrete entries is supplied by the caller;
nothing in this module knows a key name, a file name, or a variable name.

`write_policy` replaces the seed's `writable: bool`. It maps 1:1 onto the three
policy refusals, which is what lets `registry` select a refusal from a table
with no key literals in it. `SecretKeyError` and `EnvOnlyKeyError` stay derived
from `secret` and `kind` — encoding them in `write_policy` too would let a
catalog declare `secret=True` alongside `write_policy="writable"` and mean two
contradictory things at once.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from config_io.errors import InvalidValueError, UnknownKeyError

WritePolicy = Literal["writable", "link-file", "provenance", "read-only"]

#: The closed vocabulary, as data — `__post_init__` validates against it, and
#: `registry` maps it onto error classes.
WRITE_POLICIES: tuple[WritePolicy, ...] = ("writable", "link-file", "provenance", "read-only")

_TRUTHY = frozenset({"true", "1", "yes", "on"})
_FALSY = frozenset({"false", "0", "no", "off"})


@dataclass(frozen=True, slots=True)
class ConfigEntry:
    """One declared config key. A `*` segment in `key` matches any one segment."""

    key: str
    type: str  # "str" | "bool" | "int" | "list[str]"
    default: object
    description: str
    kind: str = "manifest"  # "manifest" (consults the store) | "env-only"
    env_var: str | None = None
    allowed: tuple[str, ...] = ()
    secret: bool = False
    write_policy: WritePolicy = "writable"
    #: The caller's remediation sentence, appended verbatim to any refusal for
    #: this key. This is where "edit <file> by hand" lives — never in this
    #: package.
    write_hint: str | None = None

    def __post_init__(self) -> None:
        # `allowed` is only checked for str entries (see coerce); reject it on
        # other types at construction so catalog authors don't silently ship
        # enum constraints that are never enforced.
        if self.allowed and self.type != "str":
            raise ValueError(f"ConfigEntry {self.key!r}: 'allowed' is only supported for type='str', not {self.type!r}")
        if self.write_policy not in WRITE_POLICIES:
            raise ValueError(
                f"ConfigEntry {self.key!r}: write_policy must be one of {list(WRITE_POLICIES)}, "
                f"got {self.write_policy!r}"
            )


@dataclass(frozen=True, slots=True)
class Resolved:
    """One key's resolved value, with the tier it came from."""

    key: str
    value: object
    origin: str  # "env" | "manifest" | "default"
    entry: ConfigEntry
    #: The explicit stored value hidden behind an env override. Populated only
    #: when `origin == "env"` and the entry consults the store.
    shadowed: object | None = None


def _matches(pattern: str, key: str) -> bool:
    p, k = pattern.split("."), key.split(".")
    return len(p) == len(k) and all(ps in ("*", ks) for ps, ks in zip(p, k, strict=True))


def find_entry(catalog: Sequence[ConfigEntry], key: str) -> ConfigEntry | None:
    """Two passes: exact match first, so a concrete entry always beats a `*`
    entry regardless of catalog order."""
    for entry in catalog:
        if entry.key == key:
            return entry
    for entry in catalog:
        if "*" in entry.key and _matches(entry.key, key):
            return entry
    return None


def unknown_key_error(catalog: Sequence[ConfigEntry], key: str) -> UnknownKeyError:
    """The catalog is the whole key namespace, so the near-miss suggestion is
    computable here with no caller knowledge."""
    near = difflib.get_close_matches(key, [e.key for e in catalog], n=3, cutoff=0.4)
    hint = f" — did you mean: {', '.join(near)}?" if near else ""
    return UnknownKeyError(f"unknown config key '{key}'{hint}")


def coerce(entry: ConfigEntry, raw: str) -> object:
    """Turn a raw string into the entry's declared type, or refuse."""
    if entry.type == "bool":
        low = raw.strip().lower()
        if low in _TRUTHY:
            return True
        if low in _FALSY:
            return False
        raise InvalidValueError(f"'{entry.key}' expects a boolean (true/false), got {raw!r}")
    if entry.type == "int":
        try:
            return int(raw)
        except ValueError:
            raise InvalidValueError(f"'{entry.key}' expects an integer, got {raw!r}") from None
    if entry.type == "list[str]":
        return [s.strip() for s in raw.split(",") if s.strip()]
    value = raw
    if entry.allowed and value not in entry.allowed:
        raise InvalidValueError(f"'{entry.key}' must be one of {sorted(entry.allowed)}, got {value!r}")
    return value
