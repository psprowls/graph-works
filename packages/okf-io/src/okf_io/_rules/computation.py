"""Rules for the Attested Computation contract (OKF v0.2 §10.2, §10.3, §6.2)."""

from __future__ import annotations

from collections.abc import Iterable

from okf_io._md import code_blocks_under
from okf_io._rules._common import concepts, member_path
from okf_io.document import Document
from okf_io.links import is_external, resolve_reference
from okf_io.validate import Finding, Rule, RuleContext

CODES: tuple[str, ...] = (
    "computation.runtime-missing",
    "computation.missing",
    "computation.duplicate",
    "computation.parameter-incomplete",
    "computation.path-unresolved",
)

#: §10.2's type name, compared case-insensitively and with internal whitespace
#: normalized to single spaces. A stray double space would otherwise exempt a
#: concept from the contract entirely, a silent failure rather than a loud one.
_TYPE = "attested computation"

#: §10.3: the heading an inline computation lives under.
_HEADING = "computation"


def _is_attested_computation(document: Document) -> bool:
    """Check if a document is an Attested Computation.

    Compares type case-insensitively and with whitespace normalization: both
    leading/trailing and internal runs collapse to single spaces. This ensures
    a typo like 'Attested  Computation' does not silently exempt the concept
    from contract validation.
    """
    value = (document.fm.type or "").strip()
    normalized = " ".join(value.split()).casefold()
    return normalized == _TYPE


def _has_inline_block(ctx: RuleContext, concept_id: str) -> bool:
    """Whether the body carries a computation block under `# Computation`.

    Fenced *or* four-space indented. §10.3's prose says "a single fenced code
    block", but §10.2's own worked example is indented, and a conformance
    checker that rejects the spec's example is disagreeing with the document it
    implements.
    """
    return bool(code_blocks_under(ctx.links.bodies[concept_id], _HEADING))


def contract_fields(ctx: RuleContext) -> Iterable[Finding]:
    """§10.2 and §10.3, **type-scoped**.

    These two assert what an Attested Computation must carry, so they fire only
    for that type. A producer-defined type is never held to another type's
    contract -- §11 forbids rejecting a concept for an unknown `type`.
    """
    for concept_id, document in concepts(ctx):
        if not _is_attested_computation(document):
            continue
        path = member_path(concept_id)
        if not (document.fm.runtime or "").strip():
            yield Finding(
                "computation.runtime-missing",
                "error",
                "An Attested Computation has no `runtime`",
                "§10.2",
                path,
            )
        has_path = bool((document.fm.computation or "").strip())
        if not _has_inline_block(ctx, concept_id) and not has_path:
            yield Finding(
                "computation.missing",
                "error",
                "An Attested Computation has neither an inline `# Computation` block nor a `computation:` path",
                "§10.3",
                path,
            )


def inline_and_path(ctx: RuleContext) -> Iterable[Finding]:
    """§10.3's exactly-once rule, **field-scoped**.

    Fires wherever both forms are present, whatever the type says: if you wrote
    a `computation:` path *and* an inline block, one of them is not the
    computation, and no reader can tell which.
    """
    for concept_id, document in concepts(ctx):
        has_path = bool((document.fm.computation or "").strip())
        if has_path and _has_inline_block(ctx, concept_id):
            yield Finding(
                "computation.duplicate",
                "error",
                "Both an inline `# Computation` block and a `computation:` path are present",
                "§10.3",
                member_path(concept_id),
            )


def parameters(ctx: RuleContext) -> Iterable[Finding]:
    """§10.2: each `parameters[]` entry is `{ name, type, required }`.

    Field-scoped. If you wrote `parameters:`, it must be well-formed.
    """
    for concept_id, document in concepts(ctx):
        path = member_path(concept_id)
        for position, parameter in enumerate(document.fm.parameters):
            missing = [
                field
                for field, value in (("name", parameter.name), ("type", parameter.type))
                if not (value or "").strip()
            ]
            if missing:
                names = " and ".join(f"`{field}`" for field in missing)
                yield Finding(
                    "computation.parameter-incomplete",
                    "warn",
                    f"`parameters[{position}]` is missing {names}",
                    "§10.2",
                    path,
                )


def path_fields(ctx: RuleContext) -> Iterable[Finding]:
    """§6.2: a path-valued field naming a bundle path that is not a member.

    Covers `computation`, `executor.resource` and `attester.resource` only.
    `sources[].resource` may be a scope descriptor (§5.1) and the top-level
    `resource` may be a table URI, so neither is a path this can check.
    Resolution goes through ``links``, so a field and a markdown link resolve
    by exactly the same rules.
    """
    for concept_id, document in concepts(ctx):
        path = member_path(concept_id)
        frontmatter = document.fm
        candidates: list[tuple[str, str | None]] = [("computation", frontmatter.computation)]
        if frontmatter.executor is not None:
            candidates.append(("executor.resource", frontmatter.executor.resource))
        if frontmatter.attester is not None:
            candidates.append(("attester.resource", frontmatter.attester.resource))

        for field, value in candidates:
            destination = (value or "").strip()
            if not destination or is_external(destination):
                continue
            target = resolve_reference(destination, source_id=concept_id)
            if target is not None and ctx.bundle.has_member(target):
                continue
            hint = ""
            if not destination.startswith("/"):
                rooted = resolve_reference(f"/{destination}", source_id=concept_id)
                if rooted is not None and ctx.bundle.has_member(rooted):
                    hint = f" (did you mean `/{rooted}`?)"
            yield Finding(
                "computation.path-unresolved",
                "warn",
                f"`{field}` `{destination}` is not a bundle member{hint}",
                "§6.2",
                path,
            )


RULES: tuple[Rule, ...] = (contract_fields, inline_and_path, parameters, path_fields)
