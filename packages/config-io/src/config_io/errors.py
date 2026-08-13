"""The refusal taxonomy for scoped config writes, plus the store-contract signal.

`RegistryError` is the base for every refusal and its message is user-facing.
The five write-policy refusals (`EnvOnlyKeyError`, `SecretKeyError`,
`LinkFileKeyError`, `ProvenanceKeyError`, `ReadOnlyKeyError`) never name a
file: the generic sentence comes from the write policy, and any remediation
wording comes from the catalog's own `write_hint`. `UnknownKeyError` and
`InvalidValueError` do name the offending key — that is the whole point of
those two messages — but still never a file or a variable name.

`StoreValidationError` is deliberately *not* a `RegistryError`. It is not a
refusal — it is how a `ConfigStore` implementation reports that its own stored
content is invalid, travelling downward from the caller's store into this
package, so `except RegistryError` never swallows it.
"""

from __future__ import annotations


class RegistryError(ValueError):
    """Base for registry refusals; the message is user-facing."""


class UnknownKeyError(RegistryError):
    """The key is in no catalog entry, exact or wildcard."""


class EnvOnlyKeyError(RegistryError):
    """The entry is `kind="env-only"` and is never stored."""


class SecretKeyError(RegistryError):
    """The entry is marked secret and is never stored."""


class LinkFileKeyError(RegistryError):
    """The entry's write policy is `link-file` — hand-edited, machine-local."""


class ProvenanceKeyError(RegistryError):
    """The entry's write policy is `provenance` — tooling-stamped."""


class ReadOnlyKeyError(RegistryError):
    """The entry's write policy is `read-only` — documented but hand-edit only."""


class InvalidValueError(RegistryError):
    """The raw value does not coerce, or the coerced value invalidates the store."""


class StoreValidationError(Exception):
    """A `ConfigStore` reports that its stored content is invalid.

    Raised by `ConfigStore.read()`. `registry.set_key` catches it after a write
    and rolls the store back, so a coerced value can never brick a workspace.
    """
