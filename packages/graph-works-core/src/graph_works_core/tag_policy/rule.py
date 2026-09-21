"""The five-part retention rule, four parts of which are computable.

**Why this is tier 3 and not `okf_ext`.** Test 3 reads `code_wiki_okf`'s
entity-lane convention and test 4 reads `work_tracker_okf`'s field
vocabulary. `okf_ext` importing either would invert ADR 2026-08-28-extension-layer-tier's tier model,
and `just contracts` would say so. Tests 1 and 2 are pure counts over a
`TagInventory` and could live anywhere; splitting them from their three
siblings would put half a rule in each package, which is worse than putting
all of it in the package that can hold all of it.

**No clock.** `draft` takes a required `generated=` date, matching
`okf_io.validate(today=...)`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from code_wiki_okf.placement import is_entity_lane_page
from okf_ext.tags import TagInventory
from okf_io import Bundle
from work_tracker_okf.vocabulary import (
    BLAST_RADII,
    CONTRIBUTED_TAGS,
    PHASES,
    SLUG_PREFIXES,
    WORK_STATUSES,
)

from graph_works_core.tag_policy.model import Disposition, Reason, TagVerdict, Verdict

#: Test 1. Below this a tag is a note, not a category.
DEFAULT_FLOOR = 5

#: Test 2, as a fraction of *tagged* pages -- untagged pages are not the
#: denominator, since a tag cannot discriminate among pages that carry none.
#: Exclusive: a tag on exactly 40% is above the ceiling.
DEFAULT_CEILING = 0.40


def entity_names(bundle: Bundle) -> frozenset[str]:
    """The names of every entity that already has its own page.

    Structural, via `code_wiki_okf.placement.is_entity_lane_page` -- the
    convention's own predicate, not a path pattern retyped here. The name is
    the segment that identifies the entity: the repository name for a
    `repositories/<repo>/repository` page, and the last segment for both a
    repo lane page and a `dependencies/<ecosystem>/<name>` page.

    `files/` pages are deliberately not entities. `is_entity_lane_page`
    already excludes them (it is documented as the *non-File* entity shape),
    and a tag named after a source file is a cross-cutting concern far more
    often than a duplicate of a page nobody links.

    The repository-page shape is identified by **position**
    (`repositories/<repo>/repository`, exactly three segments), not by the
    last segment's spelling: matching on `parts[-1] == "repository"` would
    also fire for any other kind's own entity literally named `repository`
    (e.g. `dependencies/<eco>/repository` or
    `repositories/<repo>/packages/repository`), mis-deriving the ecosystem
    or package name as the repo name instead.
    """
    names: set[str] = set()
    for concept_id in bundle.concepts:
        if not is_entity_lane_page(concept_id):
            continue
        parts = concept_id.split("/")
        is_repository_page = len(parts) == 3 and parts[0] == "repositories" and parts[-1] == "repository"
        names.add(parts[1] if is_repository_page else parts[-1])
    return frozenset(names)


def field_values() -> frozenset[str]:
    """Every value a `type:`, `phase:`, `work_status:` or `blast-radius:`
    can hold, in the spelling a tag would use.

    `SLUG_PREFIXES` supplies the kebab-case fold of `TYPES` (`TechDebt` ->
    `tech-debt`) rather than a local slugifier: work-tracker already owns
    that mapping for its own filenames, and a second opinion is a second
    thing to keep in sync. The other three vocabularies are already lowercase
    kebab.
    """
    return frozenset(SLUG_PREFIXES.values()) | PHASES | WORK_STATUSES | BLAST_RADII


def _judge(
    tag: str,
    uses: int,
    *,
    tagged_pages: int,
    floor: int,
    ceiling: float,
    exempt: frozenset[str],
    entities: frozenset[str],
    fields: frozenset[str],
) -> tuple[Verdict, Reason]:
    """Tests 1-4, in order. The order is the specification, not an accident.

    Floor first, so a tag that is both a field duplicate and used once is
    reported as the long-tail entry it is rather than as a policy collision.
    Ceiling second, so the workspace-name tag (on 61% of pages, and also an
    entity) is reported as the thing that discriminates nothing. Entity
    before field only because the two are disjoint in practice; nothing
    depends on it.

    An installer-contributed tag short-circuits every test. The package that
    owns it declares it, and `merge._verdict` would re-add it on the next
    install anyway -- deleting it would produce a file that silently grows a
    line back.
    """
    if tag in exempt:
        return "keep", "contributed"
    if uses < floor:
        return "strip", "below-floor"
    if tagged_pages and uses / tagged_pages >= ceiling:
        return "strip", "above-ceiling"
    if tag in entities:
        return "strip", "entity-dup"
    if tag in fields:
        return "strip", "field-dup"
    return "keep", "survivor"


def draft(
    inventory: TagInventory,
    bundle: Bundle,
    *,
    generated: date,
    floor: int = DEFAULT_FLOOR,
    ceiling: float = DEFAULT_CEILING,
) -> Disposition:
    """Judge every tag the bundle carries, plus every contributed one.

    Declared and undeclared tags are pooled and judged alike: the vocabulary
    becomes a description of what the vault says, not a record of what was
    once intended. `inventory` is therefore the only source of counts; the
    existing `tags.yaml` is not read here at all.

    **Never proposes a merge.** Retention test 5 -- "names a concern spanning
    pages, not the subject of one" -- is the judgment the artifact exists to
    capture, and no counter can make it. The output is keep/strip only; a
    human turns a `keep` into a `merge` by editing the file, and
    `disposition.load` validates what they wrote.

    A contributed tag with zero uses is still listed, as a `keep` with reason
    `contributed`, so the file accounts for the whole vocabulary and not only
    the used part of it.
    """
    counts: Mapping[str, int] = inventory.counts
    tagged_pages = len({c for ids in inventory.concepts.values() for c in ids})
    exempt = frozenset(definition.name for definition in CONTRIBUTED_TAGS)
    entities = entity_names(bundle)
    fields = field_values()

    verdicts: list[TagVerdict] = []
    for tag in sorted(set(counts) | exempt):
        uses = counts.get(tag, 0)
        verdict, reason = _judge(
            tag,
            uses,
            tagged_pages=tagged_pages,
            floor=floor,
            ceiling=ceiling,
            exempt=exempt,
            entities=entities,
            fields=fields,
        )
        verdicts.append(TagVerdict(tag=tag, uses=uses, verdict=verdict, reason=reason))

    return Disposition(
        generated=generated,
        total_tags=len(verdicts),
        tagged_pages=tagged_pages,
        verdicts=tuple(verdicts),
    )


__all__ = ["DEFAULT_CEILING", "DEFAULT_FLOOR", "draft", "entity_names", "field_values"]
