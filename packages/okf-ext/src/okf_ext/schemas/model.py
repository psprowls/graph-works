"""Frozen values the schemas capability returns.

Frozen, slotted and `MappingProxyType`-backed, matching `okf_ext.tags.model`
and the core. Every mapping field survives `json.dumps` with no encoder.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
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
class AboutMandate:
    """What a type's `x-okf-about` annotation declares.

    Presence of the annotation means `about:` is required on that type;
    `entries` optionally names the list a live page must hold at least one
    entry in. The mandate is data here and a rule elsewhere
    (`code_wiki_okf.about_rule`) -- the same split `x-okf-directory` makes.
    """

    entries: str | None = None


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


@dataclass(frozen=True, slots=True)
class ProposalGuidance:
    """What a type's `x-okf-proposal-guidance` tells a model choosing it.

    `summary` is the one line both proposal prompts list; the rest is the
    selection rubric the reasoner reads. Data only: no prompt text lives here.
    """

    summary: str
    question: str
    signals: tuple[str, ...] = ()
    anti_signals: tuple[str, ...] = ()
    title_pattern: str | None = None


@dataclass(frozen=True, slots=True)
class ProposalPromotion:
    """What a type's `x-okf-proposal-promotion` adds when a proposal becomes a page.

    `dated` prefixes the promoted filename with the promotion date.
    `frontmatter` values are strings; `{on}` is the one token, the promotion date.
    """

    dated: bool = False
    frontmatter: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def frontmatter_on(self, on: date) -> dict[str, str]:
        """`frontmatter` with every `{on}` replaced by *on* in ISO form."""
        stamp = on.isoformat()
        return {key: value.replace("{on}", stamp) for key, value in self.frontmatter.items()}


@dataclass(frozen=True, slots=True)
class ProposableType:
    """One type in the proposal pool: where it lands, how it is chosen, how it is promoted."""

    name: str
    directory: str
    guidance: ProposalGuidance
    promotion: ProposalPromotion | None


@dataclass(frozen=True, slots=True)
class Proposables:
    """The pool a schema set declares.

    `types` are the usable `true` types, in type-name order; `locked` the
    `false` ones; `refused` every `true` type excluded, with its reason.
    """

    types: tuple[ProposableType, ...]
    locked: tuple[str, ...]
    refused: tuple[tuple[str, str], ...]
