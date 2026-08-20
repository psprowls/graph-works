"""The ordered write: moves, then creates, then a bundle reload, then the
regeneration pass (updates and freshly-created pages together), then
guarded deletions, then `update_index()` scoped to this repo's own mirror
subtree.

Every step after "moves" needs the previous one's effect on the bundle's
member set, which is exactly why this is a sequence of separate calls rather
than one plan applied atomically -- `okf_ext.generators.plan_regenerate`
cannot see a page this same run just created until the bundle backing it is
reloaded.
"""

from __future__ import annotations

from okf_ext import moves
from okf_ext.generators import apply as apply_generators
from okf_ext.generators import plan_regenerate
from okf_ext.moves import MoveResult
from okf_ext.shape import SectionSet
from okf_io import Bundle, load_bundle, update_index

from code_wiki_okf.config import RepoConfig
from code_wiki_okf.mirror.create import write_new_page
from code_wiki_okf.mirror.model import MirrorPlan, MirrorResult
from code_wiki_okf.mirror.paths import mirror_concept_id, mirror_page_path


def _mirror_directories(bundle: Bundle, repo_name: str) -> tuple[str, ...]:
    """Directory ids under `repositories/<repo_name>` this repo's mirror
    touched -- not the bundle-wide `repositories/` directory itself, which
    is a sibling lane's (entity lane's) territory to reconcile, not this
    one's.

    Deliberately anchored at the repo root, not at the mirror root
    (`mirror/paths.mirror_prefix`): from here the walk yields
    `repositories/<repo>`, `repositories/<repo>/fs` and everything below,
    so both directories get an `index.md`. Anchored one level down it would
    silently stop creating `repositories/<repo>/index.md`.
    """
    prefix = f"repositories/{repo_name}"
    found: set[str] = set()
    for concept_id in bundle.concepts:
        if concept_id != prefix and not concept_id.startswith(f"{prefix}/"):
            continue
        parts = concept_id.split("/")[:-1]
        for depth in range(len(parts)):
            candidate = "/".join(parts[: depth + 1])
            if candidate == prefix or candidate.startswith(f"{prefix}/"):
                found.add(candidate)
    return tuple(sorted(found))


def apply_mirror(bundle: Bundle, plan: MirrorPlan, repo: RepoConfig, *, section_set: SectionSet) -> MirrorResult:
    """Write *plan* against *bundle*. Returns what landed."""
    move_result: MoveResult = MoveResult(moved=(), written=(), failed=(), pruned=())
    if not plan.moves.is_empty:
        move_result = moves.apply(bundle, plan.moves)

    created: list[str] = []
    for rel_path, (frontmatter, _render) in plan.creates.items():
        write_new_page(bundle.root, repo, rel_path, frontmatter, section_set=section_set)
        created.append(rel_path)

    # Reload: moves and creates both changed the member set the regeneration
    # pass and the index reconciliation below both need to see.
    reloaded = load_bundle(bundle.root)

    renders = dict(plan.updates)
    for rel_path, (_frontmatter, render) in plan.creates.items():
        concept_id = mirror_concept_id(repo.name, rel_path)
        if render.sections:  # a minimal create has an empty Render -- nothing to regenerate
            renders[concept_id] = render

    regenerated: tuple[str, ...] = ()
    if renders:
        regeneration_plan = plan_regenerate(reloaded, section_set, renders)
        apply_generators(reloaded, regeneration_plan)
        regenerated = regeneration_plan.concept_ids

    deleted: list[str] = []
    for rel_path in plan.deletions:
        target = mirror_page_path(reloaded.root, repo.name, rel_path)
        target.unlink(missing_ok=True)
        deleted.append(rel_path)

    # `apply_generators` commits each regenerated `Document` in place (same
    # object `reloaded.concepts` already holds), so `reloaded` is current for
    # creates and regenerations without a third walk. A deletion is the one
    # thing it cannot self-correct for: `Bundle.concepts` is a
    # `MappingProxyType` built once by the walk, so the deleted concept would
    # still read as a member -- `update_index()` would then neither prune its
    # entry nor notice the file backing it is gone. Only that case pays for
    # the extra reload.
    final_bundle = load_bundle(bundle.root) if plan.deletions else reloaded
    directories = _mirror_directories(final_bundle, repo.name)
    index_updates = (
        update_index(final_bundle, directories=directories, create_missing=True, dry_run=False) if directories else ()
    )

    return MirrorResult(
        repo=repo.name,
        moved=move_result.moved,
        created=tuple(created),
        regenerated=regenerated,
        deleted=tuple(deleted),
        declined_deletions=plan.declined_deletions,
        index_updates=index_updates,
    )


__all__ = ["apply_mirror"]
