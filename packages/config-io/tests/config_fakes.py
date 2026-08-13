"""Stores that exercise contract branches PlainYamlStore cannot reach.

PlainYamlStore is lossless and always readable, which is exactly why it cannot
reach two branches of registry.set_key that are the only places a user's write
can be silently discarded or a store left invalid. These fakes are the third
and fourth implementations of the Protocol, which is also how the Protocol
itself stays honest.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping

from config_io import dotted
from config_io.errors import StoreValidationError
from config_io.store import Fingerprint


class DictStore:
    """An in-memory, lossless ConfigStore. The baseline the others extend.

    Every read and write deep-copies, so a caller mutating a returned dict
    cannot reach back into the store — the same isolation a real serialising
    store gets for free, and the reason a rollback assertion here means
    something.
    """

    def __init__(self, data: Mapping[str, object] | None = None) -> None:
        self.data: dict[str, object] = copy.deepcopy(dict(data or {}))

    def read(self):
        return copy.deepcopy(self.data)

    def read_explicit(self):
        return copy.deepcopy(self.data)

    def write(self, data):
        self.data = copy.deepcopy(dict(data))

    def snapshot(self):
        # JSON rather than pickle: the token is opaque to the package, and the
        # test data is deliberately JSON-shaped so a failure here is a real
        # rollback bug rather than a serialisation surprise.
        return json.dumps(self.data).encode("utf-8")

    def restore(self, snapshot):
        self.data = {} if snapshot is None else json.loads(snapshot.decode("utf-8"))

    def fingerprint(self):
        return Fingerprint(mtime=1.0, sha256=64 * "0")


class LossyStore(DictStore):
    """Drops any top-level key outside `allowed` on write.

    This is the shape registry.set_key's persistence check exists for: a
    serialiser with a fixed allowlist makes a catalog key outside it vanish
    without an error.
    """

    def __init__(self, allowed: Iterable[str], data: Mapping[str, object] | None = None) -> None:
        super().__init__(data)
        self.allowed = set(allowed)

    def write(self, data):
        # Top-level keys only — a nested key vanishes with its whole block.
        self.data = copy.deepcopy({k: v for k, v in dict(data).items() if k in self.allowed})


class InvalidAfterWriteStore(DictStore):
    """read() raises StoreValidationError once a forbidden value has landed.

    The shape set_key's validate-and-restore exists for: a coerced value that
    is individually well-formed but violates the store's own invariants.
    """

    def __init__(
        self,
        forbidden_key: str,
        forbidden_value: object,
        data: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(data)
        self.forbidden_key = forbidden_key
        self.forbidden_value = forbidden_value

    def read(self):
        if dotted.get(self.data, self.forbidden_key) == self.forbidden_value:
            raise StoreValidationError(f"{self.forbidden_key} may not be {self.forbidden_value!r}")
        return super().read()
