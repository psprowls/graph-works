"""Frozen values the schemas capability returns.

Frozen, slotted and `MappingProxyType`-backed, matching `okf_ext.tags.model`
and the core. Every mapping field survives `json.dumps` with no encoder.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SchemaError(ValueError):
    """A schema set the caller got wrong.

    Caller **configuration** is always an exception; bundle **content** never
    is. A malformed schema raised here once, at load, beats one confusing
    complaint against every concept that uses it.

    Subclasses `ValueError` so a caller catching either works, and mirrors
    `okf_ext.tags.VocabularyError`.
    """


@dataclass(frozen=True, slots=True)
class SchemaSet:
    """A directory of JSONSchema documents, read once.

    `schemas` is the type dispatch table; `sources` answers "what says so" so a
    `Finding` can cite `metric.schema.yaml` by name, the same move
    `Vocabulary.source` makes. `documents` is every file that was read, keyed
    by filename and **including `_`-prefixed ones** — it is what a
    `referencing.Registry` is rebuilt from, so a `$ref` into a shared
    `_base.schema.yaml` still resolves for a caller holding only this value.
    """

    schemas: Mapping[str, Mapping[str, Any]]  # type name -> schema document
    sources: Mapping[str, str]  # type name -> filename
    documents: Mapping[str, Mapping[str, Any]]  # filename -> schema document
    root: Path  # the directory this was read from

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(sorted(self.schemas))
