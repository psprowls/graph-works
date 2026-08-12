"""The rule that turns a `{type: directory}` declaration into `Finding`s.

    validate(bundle, today=..., extra_rules=[placement_rule(directories)])

Two codes answered by one pass over the bundle's concepts, and the only
`Finding` source in this capability. Nothing raises for content: a misplaced
page is a `Finding`, never an exception.

**No vocabulary ships here.** `directories` and `depth` are both the caller's,
so this module names no lane, no type and no taxonomy -- which is what lets a
check that began as one package's house rule sit in tier 2 at all. A bundle
that declares nothing acquires no findings.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from okf_io import Document, Finding, Rule, RuleContext, Severity

#: The topic prefix this rule set claims. `validate()` raises the moment an
#: external rule emits a built-in prefix, and `placement` collides with none of
#: the eight. The module name is the prefix, as in okf-io.
TOPIC = "placement"

CODES = (
    "placement.directory-mismatch",  # the page's directory disagrees with its type's declared directory
    "placement.duplicate-resource",  # two or more pages claim the same `resource:` value
)

#: Unpacked from `CODES` rather than re-typed, so a code-string edit to one
#: cannot silently drift from the other.
_CODE_DIRECTORY, _CODE_DUPLICATE = CODES

#: Not an OKF section number. A bundle whose pages sit anywhere at all is a
#: perfectly conformant OKF v0.2 bundle, and the thing that says otherwise is
#: this module. `okf_ext.health` and `okf_ext.render` cite themselves the same
#: way.
_SPEC = "okf_ext.placement"

#: The default `depth`: every type placed by a plain prefix comparison.
_NO_DEPTH: Mapping[str, str] = MappingProxyType({})


def _placement_error(concept_id: str, directory: str, type_name: str, depth: str | None) -> str | None:
    """`None` when the page is where its type belongs; otherwise why not."""
    rest = concept_id.removeprefix(directory)
    if rest == concept_id:
        return f"`{type_name}` pages belong under `{directory}`"
    if depth == "exact" and "/" in rest:
        return f"`{type_name}` pages live directly under `{directory}`, and anything deeper belongs to another type"
    if depth == "nested" and "/" not in rest:
        return f"`{type_name}` pages live below `{directory}<name>/`, and depth 1 belongs to another type"
    return None


def _directory_finding(
    *,
    concept_id: str,
    document: Document,
    directories: Mapping[str, str],
    depth: Mapping[str, str],
    severity: Severity,
) -> Finding | None:
    type_name = (document.fm.type or "").strip()
    if not type_name:
        return None
    directory = directories.get(type_name)
    if directory is None or not directory.strip():
        # A type the caller declared no directory for is not checked. That is
        # how "a hand-authored schema with no annotation is content, not a
        # violation" survives as a property of the map rather than a branch.
        return None
    detail = _placement_error(concept_id, directory, type_name, depth.get(type_name))
    if detail is None:
        return None
    return Finding(
        code=_CODE_DIRECTORY,
        severity=severity,
        message=(
            f"`{concept_id}.md` is outside its declared directory: {detail}. A generator writing pages from "
            f"their type would write this one elsewhere, and a reconciler scoped by directory prefix cannot "
            f"reach it here."
        ),
        spec=_SPEC,
        path=f"{concept_id}.md",
        line=document.frontmatter_line("type"),
    )


def placement_rule(
    directories: Mapping[str, str],
    *,
    depth: Mapping[str, str] = _NO_DEPTH,
    severity: Severity = "warn",
) -> Rule:
    """Build an `okf_io.Rule` checking page placement against *directories*.

    *directories* maps a type name to the directory that type belongs in. A
    type absent from it -- or mapped to a blank directory -- is not checked.
    The map is the whole vocabulary: nothing here knows what a lane is called,
    and a caller holding a `SchemaSet` gets one from
    `okf_ext.schemas.declared_directories`.

    *depth* refines the comparison for the case one directory cannot resolve:
    two types declaring the same directory, one sitting directly under it and
    one below that. `"exact"` means directly under, `"nested"` means below;
    a type absent from the map gets a plain prefix comparison. It is separate
    from *directories* because it is a fact about the caller's types that no
    schema annotation can express.

    *severity* defaults to **`warn`**, the tier-2 house rule. `Report.ok` is a
    claim about OKF v0.2 conformance, and a page sitting somewhere unexpected
    is a conformant page -- the writer-side directory hint is a hint. A caller
    whose own reconciler cannot recover from either finding passes `"error"`
    and makes that argument in the package that can.

    One pass over `sorted(bundle.concepts)` answers both codes. `Bundle`
    already sorts that mapping, so the page this names as keeping a contested
    resource is the same one a find-by-resource index would keep. Documents
    okf-io could not parse are skipped rather than re-reported, matching
    `schema_rule` and `section_rule`.
    """

    def rule(context: RuleContext) -> Iterable[Finding]:
        first_claim: dict[str, str] = {}
        for concept_id in sorted(context.bundle.concepts):
            document = context.bundle.concepts[concept_id]
            if document.parse_error is not None:
                continue

            finding = _directory_finding(
                concept_id=concept_id,
                document=document,
                directories=directories,
                depth=depth,
                severity=severity,
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
                    f"in bundle order, so this one is unreachable by resource."
                ),
                spec=_SPEC,
                path=f"{concept_id}.md",
                line=document.frontmatter_line("resource"),
            )

    return rule


__all__ = ["CODES", "TOPIC", "placement_rule"]
