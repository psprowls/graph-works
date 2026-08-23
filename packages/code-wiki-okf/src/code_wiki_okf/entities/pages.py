"""New-page creation: everything needed to place a fresh entity page on disk
before `okf_ext.generators.plan_regenerate`/`apply` can touch it — that
capability only edits documents already loaded into `bundle.concepts`, it
creates nothing (`okf_ext.generators.plan.plan_regenerate`'s docstring:
a target with no matching document becomes a `Skipped(reason="unreadable")`,
never a new file).

Placement is delegated to :mod:`code_wiki_okf.placement`; schema annotations
declare lane segments for tooling and are not an alternate placement policy.
"""

from __future__ import annotations

from okf_ext.schemas import SchemaSet
from okf_ext.sections import SectionSet, render_skeleton

from code_wiki_okf.placement import canonical_concept_id, context_from_resource


def slug(name: str) -> str:
    """Flat display-link form of a graph entity name.

    `/` -> `__` (`@babel/core` -> `@babel__core`) is the one unsafe
    character an entity name can carry in practice (npm scoped packages).
    This helper does not authorize a member path; the placement policy applies
    the complete safety gate before returning one."""
    return name.replace("/", "__")


def default_concept_id(*, type_name: str, resource: str) -> str:
    """Return the canonical ID for a new page from its graph resource.

    Requiring the resource prevents a caller from inventing incomplete
    repository or ecosystem context and removes schema directory annotations
    as a second, weaker source of placement truth.
    """
    return canonical_concept_id(context_from_resource(type_name, resource))


def new_page_text(
    *,
    schema_set: SchemaSet,
    section_set: SectionSet,
    type_name: str,
    title: str,
    resource: str,
    description: str = "",
) -> str:
    """The full text of a brand-new entity page: universal frontmatter
    (`type`, `title`, `resource`, `description`) plus a body skeleton
    carrying every section `sections/<type_name>.yaml` declares.

    `description` is undeclared in every type's `FrontmatterOwnership` (it
    is the human's, per okf-ext's ownership model), so it is seeded once
    here and never touched again by `sync.py`'s regenerate pass.
    """
    declaration = section_set.types[type_name]
    body = render_skeleton(declaration)
    frontmatter = (
        f'---\ntype: {type_name}\ntitle: "{title}"\nresource: "{resource}"\ndescription: "{description}"\n---\n\n'
    )
    return frontmatter + body
