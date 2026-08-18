"""The `EntityMatcher` the ingest brief takes, implemented over the code graph.

`doc_wiki_okf.ingest.seams` declares the protocol, and
adr-sibling-capabilities-as-injected-protocols places the implementation in
`code-wiki-okf`. **It is not there** -- a grep for `def match_entity` across
every package in this workspace returns nothing -- so it lives here, with its
only consumer. If a second consumer appears, moving this module into
`code-wiki-okf` is the follow-up, and the ADR is the reason.

Importing `code_graph_io` and `code_wiki_okf.entities.pages` from here is
composition, not a sibling reach: `graph-works-core` is band 3, above both
`doc-wiki-okf` and `code-wiki-okf`, and the ADR governs two packages at the
*same* tier.

**The page id is not re-derived.** `code_wiki_okf.entities.pages.slug` is the
scanner's own naming rule and `default_concept_id` composes it with the type's
`x-okf-directory`; anything else here would produce a forward link resolving to
no page.
"""

from __future__ import annotations

import logging
from pathlib import Path

from code_graph_io import GraphReader
from code_wiki_okf.entities.pages import default_concept_id
from doc_wiki_okf.ingest.seams import NO_ENTITY, EntityMatch, EntityMatcher
from okf_ext.schemas import SchemaSet

logger = logging.getLogger(__name__)

#: Node kinds worth a name-fallback match; file names are too noisy to be one.
ENTITY_KINDS: tuple[str, ...] = ("class", "function", "method", "package")

#: Graph node kind -> the schema type whose `x-okf-directory` places its page.
#: Only `package` has one. A `cls:` / `fn:` / `method:` URI names no entity
#: page: the URI is still recorded, the forward link is not written.
PAGE_TYPES: dict[str, str] = {"package": "Package"}


def lookup_by_path(reader: GraphReader, repo: Path, source: Path) -> tuple[str, str, str] | None:
    """`(uri, name, kind)` for the package **containing** *source*, or `None`.

    `None` when *source* is outside *repo* or no package contains it.
    `package_for_file` already applies the falsy-uri guard.
    """
    try:
        relative = source.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None
    hit = reader.package_for_file(path=relative)
    if hit is None:
        return None
    name, uri = hit
    return str(uri), str(name), "package"


def lookup_by_name(reader: GraphReader, name: str) -> tuple[str, str, str] | None:
    """`(uri, name, kind)` for the **unique** entity-kind node named *name*.

    Ambiguity is a miss. Legacy wrote a line to stderr; a library logs it --
    the caller decides whether anyone sees it.
    """
    if not name:
        return None
    rows = reader.entity_by_name(name=name, kinds=ENTITY_KINDS)
    if not rows:
        return None
    if len(rows) > 1:
        logger.warning("entity name %r matches %d graph nodes; no entity link written", name, len(rows))
        return None
    matched_name, matched_uri, kind = rows[0]
    return str(matched_uri), str(matched_name), str(kind)


def page_id_for(schema_set: SchemaSet, *, name: str, kind: str) -> str | None:
    """The entity page id for a graph hit, or `None` when its kind has no page.

    A `SchemaSet` that does not declare the type is a bundle whose entity lane
    was never installed. That is not this function's error to raise: the URI
    still rides into the brief, and the forward link is simply not written.
    """
    type_name = PAGE_TYPES.get(kind)
    if type_name is None:
        return None
    try:
        return default_concept_id(schema_set, type_name=type_name, name=name)
    except KeyError:
        logger.warning("no %s schema in this bundle; no entity link written for %r", type_name, name)
        return None


def entity_matcher(reader: GraphReader, schema_set: SchemaSet) -> EntityMatcher:
    """Bind *reader* and *schema_set* into the `(repo, source, title, /)` seam.

    The lookup order is legacy's, unchanged: by containing package path first,
    then by name. The first is precise, the second is a guess, and running the
    guess first would let a coincidental name beat a real containment.
    """

    def match_entity(repo: Path, source: Path, title: str, /) -> EntityMatch:
        hit = lookup_by_path(reader, repo, source)
        if hit is None:
            hit = lookup_by_name(reader, title)
        if hit is None:
            return NO_ENTITY
        uri, name, kind = hit
        return EntityMatch(uri=uri, entity_filename=page_id_for(schema_set, name=name, kind=kind))

    return match_entity


__all__ = ["ENTITY_KINDS", "PAGE_TYPES", "entity_matcher", "lookup_by_name", "lookup_by_path", "page_id_for"]
