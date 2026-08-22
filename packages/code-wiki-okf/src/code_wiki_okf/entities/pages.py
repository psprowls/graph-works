"""New-page creation: everything needed to place a fresh entity page on disk
before `okf_ext.generators.plan_regenerate`/`apply` can touch it — that
capability only edits documents already loaded into `bundle.concepts`, it
creates nothing (`okf_ext.generators.plan.plan_regenerate`'s docstring:
a target with no matching document becomes a `Skipped(reason="unreadable")`,
never a new file).

`x-okf-directory` is read from the loaded `SchemaSet`, not hardcoded here —
one source of truth, matching epic E-E ("the annotation tells a writer
where to create; nothing ever tells a validator where to expect").
"""

from __future__ import annotations

from okf_ext.schemas import SchemaSet
from okf_ext.sections import SectionSet, render_skeleton


def slug(name: str) -> str:
    """Filesystem-safe form of a graph entity name.

    `/` -> `__` (`@babel/core` -> `@babel__core`) is the one unsafe
    character an entity name can carry in practice (npm scoped packages).
    Flat, one rule, no nested scope directories — matches every lane's flat
    kebab-plural convention (epic E-E)."""
    return name.replace("/", "__")


def directory_for(schema_set: SchemaSet, type_name: str) -> str:
    """The `x-okf-directory` a type's schema declares, e.g. `"packages/"`."""
    return str(schema_set.schemas[type_name]["x-okf-directory"])


def default_concept_id(schema_set: SchemaSet, *, type_name: str, name: str, repo_name: str | None = None) -> str:
    """Where a **new** page of `type_name` named `name` is created.

    Bundle-relative, no `.md` suffix (matching `Bundle.concepts`' own keys —
    see `resource_index`'s `concept_id`). `repo_name=None` (the default)
    keeps a type at the bundle-root lane its schema declares -- right for
    Repository (one page per repo, already unique) and Dependency
    (ecosystem-wide by design). A repo-scoped type (Package, App, TestSuite,
    AgentPlugin) passes its owning repo's name, nesting the page under
    `repositories/<repo_name>/` so two repos declaring a same-named entity
    never compute the same path.
    """
    directory = directory_for(schema_set, type_name)
    if repo_name is None:
        return f"{directory}{slug(name)}"
    return f"repositories/{repo_name}/{directory}{slug(name)}"


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
