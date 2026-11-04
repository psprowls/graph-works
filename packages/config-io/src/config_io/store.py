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

import copy
import functools
import hashlib
import io
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.util import load_yaml_guess_indent

from config_io import dotted
from config_io.errors import StoreValidationError

# YAML 1.2 is the workspace's dialect (D-001). Construct readers and writers
# per call: ruamel's YAML instances hold mutable parser/serializer state that
# cannot be shared by concurrent watcher and request threads.


def _scalar_width(value: object) -> int | None:
    """Rendered width of a single-line scalar, or None when it is not one."""
    if isinstance(value, (Mapping, list)) or value is None:
        return None
    buffer = io.StringIO()
    writer = YAML(typ="rt")
    writer.preserve_quotes = True
    writer.dump(value, buffer)
    rendered = buffer.getvalue().removesuffix("...\n").rstrip("\n")
    return None if "\n" in rendered else len(rendered)


def _keep_comment_gap(
    container: CommentedMap | CommentedSeq, key: object, old: object, new: object, lines: list[str]
) -> None:
    """Keep the gap between a replaced scalar and its trailing comment.

    ruamel pins an inline comment to an absolute column, so a value of a
    different width moves the comment's gap. Move the column by the width
    delta instead. Never raises: anything unreadable keeps ruamel's default.
    """
    try:
        entry = container.ca.items.get(key)
        if not entry:
            return
        token = entry[2] if isinstance(container, CommentedMap) else entry[0]
        old_width, new_width = _scalar_width(old), _scalar_width(new)
        if token is None or old_width is None or new_width is None:
            return
        line = lines[token.start_mark.line]
        column = token.start_mark.column
        gap = column - len(line[:column].rstrip())
        if gap < 1:
            return
        value_start = column - gap - old_width
        if value_start < 0:
            return
        token.column = value_start + new_width + gap
    except (AttributeError, IndexError, TypeError):
        return


def _merge_mapping(tree: CommentedMap, data: Mapping[str, object], lines: list[str]) -> None:
    for key in [k for k in tree if k not in data]:
        del tree[key]
    for key, value in data.items():
        if key not in tree:
            tree[key] = value
            continue
        old = tree[key]
        merged = _merge_node(old, value, lines)
        tree[key] = merged
        if merged is not old:
            _keep_comment_gap(tree, key, old, tree[key], lines)


def _merge_node(node: object, value: object, lines: list[str]) -> object:
    if isinstance(node, CommentedMap) and isinstance(value, Mapping):
        _merge_mapping(node, value, lines)
        return node
    if isinstance(node, CommentedSeq) and isinstance(value, list):
        # Resize in place: replacing the node would drop its flow flag and item comments.
        del node[len(value) :]
        for index, item in enumerate(value):
            if index >= len(node):
                node.append(item)
                continue
            old = node[index]
            merged = _merge_node(old, item, lines)
            node[index] = merged
            if merged is not old:
                _keep_comment_gap(node, index, old, node[index], lines)
        return node
    # An equal value keeps its node, so its quote style and block form survive.
    if type(node).__mro__[-2] is type(value).__mro__[-2] and node == value:
        return node
    return value


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


@functools.lru_cache(maxsize=128)
def _safe_load(raw: bytes) -> object:
    """Parse YAML bytes; memoized on content, so errors are never cached."""
    return YAML(typ="safe").load(raw)


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
            loaded = _safe_load(raw)
        except YAMLError as exc:
            raise StoreValidationError(f"{self.path} is not valid YAML: {exc}") from exc
        if loaded is None:
            return {}
        if not isinstance(loaded, dict):
            raise StoreValidationError(f"{self.path} must hold a mapping at the top level, got {type(loaded).__name__}")
        return cast("dict[str, object]", copy.deepcopy(loaded))

    def read(self) -> dict[str, object]:
        return self._load()

    def read_explicit(self) -> dict[str, object]:
        return self._load()

    def write(self, data: Mapping[str, object]) -> None:
        """Write `data`, keeping the existing file's comments and formatting.

        When the file already holds a mapping, it is loaded in ruamel's
        round-trip mode and `data` is merged into that tree, so only the
        changed keys' lines move. A sequence whose length changed is resized
        in place, so its flow style and the comments of surviving items are
        kept. A missing, empty or
        unloadable file gets a plain safe dump: the value contract outranks
        the form.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = self._round_trip(data)
        if text is None:
            buffer = io.StringIO()
            writer = YAML(typ="safe")
            writer.default_flow_style = False
            # Preserve insertion order (D-003); the safe representer sorts by default.
            writer.representer.sort_base_mapping_type_on_output = False
            # Keep ruamel's default width to preserve the existing output formatting.
            writer.dump(dict(data), buffer)
            text = buffer.getvalue()
        self.path.write_text(text, encoding="utf-8", newline="")

    def _round_trip(self, data: Mapping[str, object]) -> str | None:
        if not self.path.exists():
            return None
        raw = self.path.read_bytes()
        if not raw.strip():
            return None
        try:
            _, sequence_indent, block_seq_indent = load_yaml_guess_indent(raw)
            writer = YAML(typ="rt")
            writer.preserve_quotes = True
            writer.default_flow_style = False
            writer.indent(mapping=2, sequence=sequence_indent or 2, offset=block_seq_indent or 0)
            tree = writer.load(raw)
            if not isinstance(tree, CommentedMap):
                return None
            _merge_mapping(tree, data, raw.decode("utf-8").splitlines())
            buffer = io.StringIO()
            writer.dump(tree, buffer)
        except YAMLError:
            return None
        return buffer.getvalue()

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
