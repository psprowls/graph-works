"""Where a new Diátaxis page goes, and what it says when it gets there.

A direct analogue of `code_wiki_okf/entities/pages.py`, the module this problem
was already solved in. `x-okf-directory` is read off the loaded `SchemaSet`, not
hardcoded: ADR-0012's annotation tells a writer where to *create*, and nothing
ever tells a reader or a validator where to *expect*.

Configuration errors raise, as they do in every loader in this workspace. A type
neither declaration set knows about is caller configuration, not bundle content.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_ext.schemas import SchemaSet
from okf_ext.sections import render_skeleton
from okf_ext.shape import SectionSet
from okf_io import parse

from doc_wiki_okf.reading import slugify

if TYPE_CHECKING:  # `classify` imports `default_concept_id` from here at runtime
    from doc_wiki_okf.diataxis.classify import Classification


def directory_for(schema_set: SchemaSet, type_name: str) -> str:
    """The `x-okf-directory` a type's schema declares, e.g. `"tutorials/"`.

    Raises `KeyError` for a type the set does not carry.
    """
    return str(schema_set.schemas[type_name]["x-okf-directory"])


def default_concept_id(schema_set: SchemaSet, *, type_name: str, title: str) -> str:
    """Where a **new** page of *type_name* titled *title* is created.

    Bundle-relative, no `.md` suffix -- matching `Bundle.concepts`' own keys.
    `reading.slugify` is the slugger, which is why C1 is a hard prerequisite.
    """
    return f"{directory_for(schema_set, type_name)}{slugify(title)}"


def new_page_text(
    *,
    schema_set: SchemaSet,
    section_set: SectionSet,
    classification: Classification,
    description: str = "",
) -> str:
    """The full text of a brand-new Diátaxis page.

    Base frontmatter (`type`, `title`, `description`) plus a body carrying every
    section `sections/<type>.yaml` declares. Built through `okf_io.parse("")`
    and `Document.set` rather than hand-assembled YAML, so each key lands where
    okf-io's `PREFERRED_KEY_ORDER` implies -- the move
    `work_tracker_okf.filing.apply` makes.

    Raises `KeyError` when either declaration set does not know the type. Both
    are checked: a page created from a section declaration the schema set does
    not carry would fail `schema_rule` the moment it lands.
    """
    type_name = classification.type_name
    if type_name not in schema_set.schemas:
        raise KeyError(f"{type_name}: no schema in `{schema_set.root.name}`")
    declaration = section_set.types[type_name]

    document = parse("")
    document.set("type", type_name)
    document.set("title", classification.title)
    document.set("description", description)
    document.set_body(render_skeleton(declaration))
    return document.serialize()


__all__ = ["default_concept_id", "directory_for", "new_page_text"]
