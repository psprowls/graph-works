"""Two codes over the page-to-lane correspondence `sync` assumes and never
checks.

    validate(bundle, today=..., extra_rules=[lane_rule(schema_set)])

Both default to `error`, unlike every other house rule in this workspace,
because neither finding is drift a later run reconciles away. `prune_lane`
(`entities/delete.py`) scopes deletion by lane prefix, so a misplaced page is
outside its reach forever while `sync_entities` keeps writing the page the
type calls for -- a guaranteed duplicate no sync run can resolve. And a
duplicate-`resource:` loser is dropped by find-by-resource, so nothing can
reach it by resource again.

`code_wiki_okf.lane` (this) sits beside `code_wiki_okf.entities.lanes` (the
sync driver): near-identical names for different things, the same deliberate
collision okf-io already carries between `okf_io._rules.links` and
`okf_io.links`, and it falls out of the same invariant -- the module name is
the code prefix.
"""

from __future__ import annotations

from collections.abc import Iterable

from okf_ext.schemas import SchemaSet
from okf_io import Document, Finding, Rule, RuleContext, Severity

TOPIC = "lane"

CODES = (
    "lane.directory-mismatch",  # the page's directory disagrees with its type's `x-okf-directory`
    "lane.duplicate-resource",  # two or more pages claim the same `resource:` value
)

_CODE_DIRECTORY, _CODE_DUPLICATE = CODES

#: Not an OKF section number. A bundle whose `Package` page sits in
#: `dependencies/` is a perfectly conformant OKF v0.2 bundle; the thing that
#: says otherwise is this module. `okf_ext.health` and `okf_ext.render` cite
#: themselves the same way rather than invent a citation that outlives
#: whoever wrote it.
_SPEC_CITATION = "code_wiki_okf.lane"

#: The two types `x-okf-directory` alone cannot place, because both declare
#: `repositories/`: the Repository entity page lives directly under it,
#: every mirror File page lives below that. Same distinction
#: `entities/delete.py`'s `exact_depth=` and
#: `sync/snapshot.py:_is_entity_repository_page` already make; a property of
#: this check, not a third code.
_DEPTH: dict[str, str] = {"Repository": "exact", "File": "nested"}


def _declared_directory(schema_set: SchemaSet, type_name: str) -> str | None:
    """The lane *type_name*'s schema declares, or `None` when it declares none.

    `entities/pages.py:directory_for` subscripts the key -- it is creating a
    page and has nowhere else to go. A rule must never raise for content, and
    a hand-authored schema without the annotation is content: no annotation
    means no declared lane, which means nothing here to check.
    """
    schema = schema_set.schemas.get(type_name)
    if schema is None:
        return None
    directory = schema.get("x-okf-directory")
    if not isinstance(directory, str) or not directory.strip():
        return None
    return directory


def _placement_error(concept_id: str, directory: str, type_name: str) -> str | None:
    """`None` when the page is where its type belongs; otherwise why not."""
    rest = concept_id.removeprefix(directory)
    if rest == concept_id:
        return f"`{type_name}` pages belong under `{directory}`"
    depth = _DEPTH.get(type_name)
    if depth == "exact" and "/" in rest:
        return f"`{type_name}` pages live directly under `{directory}`, and anything deeper is the `File` mirror lane"
    if depth == "nested" and "/" not in rest:
        return f"`{type_name}` pages live below `{directory}<repo>/`, and depth 1 is the `Repository` page's own slot"
    return None


def _directory_finding(
    *, concept_id: str, document: Document, schema_set: SchemaSet, severity: Severity
) -> Finding | None:
    type_name = (document.fm.type or "").strip()
    if not type_name:
        return None
    directory = _declared_directory(schema_set, type_name)
    if directory is None:
        # A type with no schema at all is already `schemas.no-schema-for-type`
        # from tier 2; a second code for the same fact is the duplication this
        # module exists to avoid.
        return None
    detail = _placement_error(concept_id, directory, type_name)
    if detail is None:
        return None
    return Finding(
        code=_CODE_DIRECTORY,
        severity=severity,
        message=(
            f"`{concept_id}.md` is outside its declared lane: {detail}. Sync would write the page its type "
            f"calls for at the correct path, and deletion is scoped by lane prefix, so this page can never "
            f"be reconciled away."
        ),
        spec=_SPEC_CITATION,
        path=f"{concept_id}.md",
        line=document.frontmatter_line("type"),
    )


def lane_rule(schema_set: SchemaSet, *, severity: Severity = "error") -> Rule:
    """Build an `okf_io.Rule` checking lane placement against *schema_set*.

    `SchemaSet` is the only injection. The check is driven entirely by what
    each type's own schema declares -- the same door
    `entities/pages.py:directory_for` reads `x-okf-directory` through -- so a
    lane added to `ENTITY_LANES` without a schema is not this rule's finding,
    and a type whose schema declares a directory outside `ENTITY_LANES` is
    still checked against that directory. Taking both sources would give the
    module two answers to "where does this type live" and no rule for which
    wins; the one place the schema is insufficient is resolved by `_DEPTH`.

    `severity` defaults to **`error`**, unlike every `okf_ext` factory: see
    the module docstring. Because it does, `cli.py:validate` needs no
    `has_lane_finding` counterpart to its `has_sync_finding` special case --
    `report.ok` is already false.

    One pass over `sorted(bundle.concepts)` answers both codes. `Bundle`
    already sorts that mapping (`okf_io/bundle.py`), so the page this names as
    keeping a contested resource is the same one `resource_index` and
    `prune_lane` keep. Documents okf-io could not parse are skipped rather
    than re-reported, matching `schema_rule` and `section_rule`.
    """

    def rule(context: RuleContext) -> Iterable[Finding]:
        first_claim: dict[str, str] = {}
        for concept_id in sorted(context.bundle.concepts):
            document = context.bundle.concepts[concept_id]
            if document.parse_error is not None:
                continue

            finding = _directory_finding(
                concept_id=concept_id, document=document, schema_set=schema_set, severity=severity
            )
            if finding is not None:
                yield finding

            resource = document.fm.resource
            if resource is None:
                continue
            winner = first_claim.setdefault(resource, concept_id)
            if winner == concept_id:
                continue
            yield Finding(
                code=_CODE_DUPLICATE,
                severity=severity,
                message=(
                    f"`{resource}` is already claimed by `{winner}.md`. Find-by-resource keeps the first page "
                    f"in bundle order, so this one is unreachable by resource and invisible to deletion."
                ),
                spec=_SPEC_CITATION,
                path=f"{concept_id}.md",
                line=document.frontmatter_line("resource"),
            )

    return rule


__all__ = ["CODES", "TOPIC", "lane_rule"]
