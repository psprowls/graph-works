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

`LayeredYamlStore` is the second shipped implementation: a read-only merged
view over two of those, overlay winning. It composes rather than parses — the
`yaml` import above is still the only one in the package — and it satisfies
`LayeredStore`, the Protocol the read side uses to report which layer a value
came from.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from config_io import dotted
from config_io.errors import StoreValidationError

#: YAML 1.2 is the workspace's dialect (D-001). One reader and one writer,
#: built once: `YAML()` carries no per-document state across calls, and
#: nothing in this workspace uses threads, so two module-level instances are
#: safe. If threads are ever introduced, construct them per call instead.
_READER = YAML(typ="safe")

#: `sort_base_mapping_type_on_output` is load-bearing (D-003): ruamel's safe
#: representer sorts mapping keys by default, where PyYAML's did so only when
#: asked, so without this the first `gw config set` reorders the whole file.
#: `width` deliberately stays at ruamel's default -- raising it makes a re-dump
#: of the live `workspace.yaml` stop being byte-identical to the file on disk.
_WRITER = YAML(typ="safe")
_WRITER.default_flow_style = False
_WRITER.representer.sort_base_mapping_type_on_output = False


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
            loaded = _READER.load(raw)
        except YAMLError as exc:
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
        buffer = io.StringIO()
        _WRITER.dump(dict(data), buffer)
        self.path.write_text(buffer.getvalue(), encoding="utf-8", newline="")

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


#: The one sentence every refused write on a layered store reports. A write
#: names its layer; the merged view is read-only by construction (D-002).
_LAYERED_READ_ONLY = "LayeredYamlStore is read-only; write through .base or .overlay"


@runtime_checkable
class LayeredStore(Protocol):
    """A `ConfigStore` whose explicit view is a merge of two layers.

    Structural, like `ConfigStore`. `registry.resolve_key` and
    `projection.write_projection` use `isinstance` against this to report the
    extra provenance a layered store carries, and behave exactly as before
    against every store that does not satisfy it.
    """

    def read_base_explicit(self) -> dict[str, object]:
        """Raw stored values from the lower layer alone."""
        ...

    def read_overlay_explicit(self) -> dict[str, object]:
        """Raw stored values from the upper layer alone."""
        ...

    def overlay_fingerprint(self) -> Fingerprint | None:
        """Provenance for the upper layer, or None when nothing is stored there."""
        ...


@dataclass(frozen=True, slots=True)
class LayeredYamlStore:
    """A read-only merged view over two `PlainYamlStore`s; the overlay wins.

    Lossless in the same sense `PlainYamlStore` is — `read` and
    `read_explicit` agree and nothing is injected — over
    `dotted.merge(base, overlay)`. A `StoreValidationError` from either layer
    propagates naming that layer's own path, because the plain store already
    puts the path in the message.

    `fingerprint()` is deliberately the **base's**, so a consumer reading
    `_meta.source_*` keeps its existing meaning: "the lower, committed layer".
    The upper layer is reported separately by `overlay_fingerprint()`.

    Writes are refused rather than routed. `registry.set_key` is
    read-mutate-write over `store.read()`; against a merged mapping that would
    copy every overlay value into the lower layer's file. A caller writes by
    naming a layer — `store.base` or `store.overlay` — and passing that plain
    store to `set_key` / `unset_key` (D-002). The three write methods exist
    only so this class satisfies `ConfigStore` structurally for the read-side
    functions.
    """

    base: PlainYamlStore
    overlay: PlainYamlStore

    def read(self) -> dict[str, object]:
        return dotted.merge(self.base.read_explicit(), self.overlay.read_explicit())

    def read_explicit(self) -> dict[str, object]:
        return dotted.merge(self.base.read_explicit(), self.overlay.read_explicit())

    def read_base_explicit(self) -> dict[str, object]:
        return self.base.read_explicit()

    def read_overlay_explicit(self) -> dict[str, object]:
        return self.overlay.read_explicit()

    def write(self, data: Mapping[str, object]) -> None:
        raise TypeError(_LAYERED_READ_ONLY)

    def snapshot(self) -> bytes | None:
        raise TypeError(_LAYERED_READ_ONLY)

    def restore(self, snapshot: bytes | None) -> None:
        raise TypeError(_LAYERED_READ_ONLY)

    def fingerprint(self) -> Fingerprint | None:
        return self.base.fingerprint()

    def overlay_fingerprint(self) -> Fingerprint | None:
        return self.overlay.fingerprint()
