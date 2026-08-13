"""The store seam: where `config-io` reads config from and writes it back to.

`ConfigStore` is a Protocol, so this package never learns a file name, a
directory name, or a serialisation format. Every method exists because the
ported write path already does that job against a file directly:

- `read` — normalised/validated content, defaults injected. Raises
  `StoreValidationError` when the stored content is invalid.
- `read_explicit` — raw stored values, nothing injected. Load-bearing twice
  over: origin reporting must distinguish "explicitly set" from "default", and
  the persistence check must see what actually landed.
- `snapshot` / `restore` — rollback, made storage-agnostic. `restore(None)`
  means "there was nothing there; remove it".
- `fingerprint` — one object rather than two fields, so the projection's
  `_meta` is internally consistent by construction: both values are real or
  both are absent, never a mix.

`PlainYamlStore` is the shipped implementation. It is lossless — `read` and
`read_explicit` return the same content and no defaults are injected — so
`registry.set_key`'s persistence check can never fire against it. It exists so
the package is usable and fully testable with no caller, and so this package's
own test suite is not written entirely against a fake.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from config_io.errors import StoreValidationError


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """Provenance for whatever a store is backed by."""

    mtime: float
    sha256: str


class ConfigStore(Protocol):
    """What `config-io` needs from a config store. Structural, never inherited."""

    def read(self) -> dict[str, object]:
        """Normalised, validated content with defaults injected."""
        ...

    def read_explicit(self) -> dict[str, object]:
        """Raw stored values, nothing injected."""
        ...

    def write(self, data: Mapping[str, object]) -> None:
        """Persist `data`, by whatever serialisation the store owns."""
        ...

    def snapshot(self) -> bytes | None:
        """Opaque rollback token, or None when nothing is stored."""
        ...

    def restore(self, snapshot: bytes | None) -> None:
        """Put a snapshot back; None means remove whatever is stored."""
        ...

    def fingerprint(self) -> Fingerprint | None:
        """Provenance for the projection's `_meta`, or None when nothing is stored."""
        ...


@dataclass(frozen=True, slots=True)
class PlainYamlStore:
    """A lossless `ConfigStore` over a single YAML file."""

    path: Path

    def _load(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        raw = self.path.read_bytes()
        if not raw.strip():
            return {}
        try:
            loaded = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise StoreValidationError(f"{self.path} is not valid YAML: {exc}") from exc
        if loaded is None:
            return {}
        if not isinstance(loaded, dict):
            raise StoreValidationError(f"{self.path} must hold a mapping at the top level, got {type(loaded).__name__}")
        return dict(loaded)

    def read(self) -> dict[str, object]:
        return self._load()

    def read_explicit(self) -> dict[str, object]:
        return self._load()

    def write(self, data: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rendered = yaml.safe_dump(dict(data), sort_keys=False, default_flow_style=False)
        self.path.write_text(rendered, encoding="utf-8")

    def snapshot(self) -> bytes | None:
        return self.path.read_bytes() if self.path.exists() else None

    def restore(self, snapshot: bytes | None) -> None:
        if snapshot is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.write_bytes(snapshot)

    def fingerprint(self) -> Fingerprint | None:
        # Single existence check, then stat + read off it, so the two fields
        # cannot disagree about whether anything is stored.
        if not self.path.exists():
            return None
        raw = self.path.read_bytes()
        return Fingerprint(mtime=self.path.stat().st_mtime, sha256=hashlib.sha256(raw).hexdigest())
