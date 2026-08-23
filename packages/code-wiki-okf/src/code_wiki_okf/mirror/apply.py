"""Apply one immutable mirror plan without rediscovering placement.

Every step after "moves" needs the previous one's effect on the bundle's
member set, which is exactly why this is a sequence of separate calls rather
than one plan applied atomically -- `okf_ext.generators.plan_regenerate`
cannot see a page this same run just created until the bundle backing it is
reloaded.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path, PurePosixPath

from okf_ext import moves
from okf_ext.bundle import SECTIONS_DIRNAME
from okf_ext.generators import Render, plan_regenerate
from okf_ext.generators import apply as apply_generators
from okf_ext.moves import MoveResult
from okf_ext.shape import load_sections
from okf_io import Bundle, load_bundle, update_index

from code_wiki_okf.mirror.create import write_new_page
from code_wiki_okf.mirror.model import MirrorPlan, MirrorResult, MirrorTarget
from code_wiki_okf.placement import (
    PlacementContext,
    PlacementError,
    affected_directories,
    canonical_member,
    context_from_resource,
    filesystem_member_identity,
)
from code_wiki_okf.resources import resource_index


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [_thaw(item) for item in sorted(value, key=repr)]
    return value


def _materialize_render(render: Render) -> Render:
    return Render(
        frontmatter={key: _thaw(value) for key, value in render.frontmatter.items()},
        sections=dict(render.sections),
    )


def _target_context(target: MirrorTarget) -> PlacementContext:
    context = context_from_resource("File", target.resource)
    canonical = canonical_member(context)
    if target.source_path != context.source_path:
        raise PlacementError(
            resource=target.resource,
            reason=f"planned source path {target.source_path!r} conflicts with resource identity",
            expected=context.source_path,
        )
    if target.member != canonical:
        raise PlacementError(
            resource=target.resource,
            reason=f"planned member {target.member} is not canonical",
            expected=canonical.removesuffix(".md"),
        )
    if PurePosixPath(canonical).name == "index.md":
        raise PlacementError(
            resource=target.resource,
            reason="File page would collide with a reserved directory index.md",
            expected=canonical.removesuffix(".md"),
        )
    return context


def _preflight_live(bundle_root: Path, bundle: Bundle, plan: MirrorPlan) -> None:
    """Refuse live target drift for the whole plan before its first write.

    `bundle` must reflect current on-disk state -- a caller holding a
    possibly-stale bundle should call `preflight_mirror_live`, which reloads.
    """
    index = resource_index(bundle)
    create_members = {plan.target_for(source_path).member for source_path in plan.creates}
    move_destinations = {move.dest for move in plan.moves.moves}
    must_be_absent = create_members | move_destinations
    must_exist = {f"{concept_id}.md" for concept_id in plan.updates} | {move.source for move in plan.moves.moves}

    for target in plan.targets:
        claimed_members = index.members_by_resource.get(target.resource, ())
        if len(claimed_members) > 1:
            raise PlacementError(
                resource=target.resource,
                reason=(
                    f"duplicate resource in {', '.join(sorted(claimed_members))}; delete duplicate pages and re-plan"
                ),
                expected=target.member.removesuffix(".md"),
            )
        if claimed_members and claimed_members[0] != target.member:
            raise PlacementError(
                resource=target.resource,
                reason=f"found at {claimed_members[0]}; delete the misplaced page and re-plan",
                expected=target.member.removesuffix(".md"),
            )

        concept_id = target.member.removesuffix(".md")
        document = bundle.concept(concept_id)
        target_exists = (bundle_root / target.member).exists()
        path_conflicts = index.filesystem_path_conflicts_for(target.member)
        if path_conflicts:
            raise PlacementError(
                resource=target.resource,
                reason=(
                    f"planned member {target.member} has a filesystem-equivalent path type conflict with occupied "
                    f"{', '.join(path_conflicts)}; re-plan"
                ),
                expected=concept_id,
            )
        equivalent_members = index.filesystem_members_for(target.member)
        if equivalent_members and equivalent_members != (target.member,):
            raise PlacementError(
                resource=target.resource,
                reason=(
                    f"planned member {target.member} is filesystem-equivalent to occupied "
                    f"{', '.join(equivalent_members)}; re-plan"
                ),
                expected=concept_id,
            )
        if target.member in must_be_absent and target_exists:
            raise PlacementError(
                resource=target.resource,
                reason=f"planned destination {target.member} is occupied; re-plan against the current bundle",
                expected=concept_id,
            )
        if document is None:
            if target_exists:
                raise PlacementError(
                    resource=target.resource,
                    reason=f"planned member {target.member} is occupied by an unreadable page; re-plan",
                    expected=concept_id,
                )
            if target.member in must_exist:
                raise PlacementError(
                    resource=target.resource,
                    reason=f"planned source {target.member} no longer exists; re-plan against the current bundle",
                    expected=concept_id,
                )
            continue
        if document.fm.resource != target.resource:
            occupant = document.fm.resource or "an unowned page"
            raise PlacementError(
                resource=target.resource,
                reason=f"planned member {target.member} is occupied by {occupant}; re-plan",
                expected=concept_id,
            )
        actual_type = document.fm.type or ""
        if actual_type != "File":
            raise PlacementError(
                resource=target.resource,
                reason=f"{target.member} declares type {actual_type or '(blank)'} instead of File; re-plan",
                expected=concept_id,
            )

    index_members: dict[str, str] = {}
    for target in plan.targets:
        context = _target_context(target)
        for directory in affected_directories(context):
            member = f"{directory}/index.md"
            index_members.setdefault(member, target.resource)
    for member, resource in index_members.items():
        path_conflicts = index.filesystem_path_conflicts_for(member)
        if path_conflicts:
            raise PlacementError(
                resource=resource,
                reason=(
                    f"derived index {member} has a filesystem-equivalent path type conflict with occupied "
                    f"{', '.join(path_conflicts)}; re-plan"
                ),
                expected=member.removesuffix(".md"),
            )
        equivalent_members = index.filesystem_members_for(member)
        if equivalent_members and equivalent_members != (member,):
            raise PlacementError(
                resource=resource,
                reason=(
                    f"derived index {member} is filesystem-equivalent to occupied "
                    f"{', '.join(equivalent_members)}; re-plan"
                ),
                expected=member.removesuffix(".md"),
            )


def preflight_mirror_live(bundle_root: Path, plan: MirrorPlan) -> None:
    """Refuse all live mirror mutations that would diverge from *plan*."""
    _preflight_live(bundle_root, load_bundle(bundle_root), plan)


def apply_mirror(
    bundle_root: Path,
    plan: MirrorPlan,
    *,
    today: date,
    declarations_dir: Path | None = None,
) -> MirrorResult:
    """Apply exactly *plan*'s canonical members against *bundle_root*."""
    _ = today
    contexts = tuple(_target_context(target) for target in plan.targets)
    members: dict[str, tuple[str, str]] = {}
    members_by_name: dict[str, str] = {}
    sources: dict[str, str] = {}
    for target, context in zip(plan.targets, contexts, strict=True):
        if context.repository != plan.repo:
            raise PlacementError(
                resource=target.resource,
                reason=f"planned repository {plan.repo!r} conflicts with resource identity",
                expected=context.repository,
            )
        prior_resource = sources.get(target.source_path)
        if prior_resource is not None and prior_resource != target.resource:
            raise PlacementError(
                resource=target.resource,
                reason=f"planned source path {target.source_path!r} also belongs to {prior_resource}",
                expected=target.member.removesuffix(".md"),
            )
        sources[target.source_path] = target.resource
        identity = filesystem_member_identity(target.member)
        prior = members.get(identity)
        if prior is not None and prior[1] != target.resource:
            raise PlacementError(
                resource=target.resource,
                reason=(
                    f"planned member {target.member} is filesystem-equivalent to {prior[0]}, "
                    f"which also belongs to {prior[1]}"
                ),
                expected=target.member.removesuffix(".md"),
            )
        members[identity] = (target.member, target.resource)
        members_by_name[target.member] = target.resource

    for source_path in (*plan.creates, *plan.deletions):
        if source_path not in sources:
            raise PlacementError(
                resource=f"file:{plan.repo}/{source_path}",
                reason="planned action has no canonical mirror target",
            )
    for concept_id in plan.updates:
        if f"{concept_id}.md" not in members_by_name:
            raise PlacementError(
                resource=f"file:{plan.repo}/{concept_id}",
                reason=f"planned update {concept_id} has no canonical mirror target",
            )
    for move in plan.moves.moves:
        if move.source not in members_by_name or move.dest not in members_by_name:
            raise PlacementError(
                resource=f"file:{plan.repo}",
                reason=f"planned move {move.source} -> {move.dest} leaves canonical mirror targets",
            )

    bundle: Bundle = load_bundle(bundle_root)
    _preflight_live(bundle_root, bundle, plan)
    declarations_root = bundle_root if declarations_dir is None else declarations_dir
    section_set = load_sections(declarations_root / SECTIONS_DIRNAME)
    move_result: MoveResult = MoveResult(moved=(), written=(), failed=(), pruned=())
    if not plan.moves.is_empty:
        move_result = moves.apply(bundle, plan.moves)

    created: list[str] = []
    created_ids: set[str] = set()
    for rel_path, (frontmatter, _render) in plan.creates.items():
        target = plan.target_for(rel_path)
        write_new_page(
            bundle_root,
            target.member,
            {key: _thaw(value) for key, value in frontmatter.items()},
            section_set=section_set,
        )
        created.append(rel_path)
        created_ids.add(target.member.removesuffix(".md"))

    # Reload: moves and creates both changed the member set the regeneration
    # pass and the index reconciliation below both need to see.
    reloaded = load_bundle(bundle_root)

    renders = {concept_id: _materialize_render(render) for concept_id, render in plan.updates.items()}
    for rel_path, (_frontmatter, render) in plan.creates.items():
        concept_id = plan.target_for(rel_path).member.removesuffix(".md")
        if render.sections:  # a minimal create has an empty Render -- nothing to regenerate
            renders[concept_id] = _materialize_render(render)

    regenerated: tuple[str, ...] = ()
    generator_failed: tuple[str, ...] = ()
    if renders:
        regeneration_plan = plan_regenerate(reloaded, section_set, renders)
        generator_result = apply_generators(reloaded, regeneration_plan)
        regenerated = tuple(
            member.removesuffix(".md")
            for member in generator_result.written
            if member.removesuffix(".md") not in created_ids
        )
        generator_failed = tuple(
            f"{failure.path}: {failure.kind}: {failure.error}" for failure in generator_result.failed
        ) + tuple(f"{item.path}: {item.reason}" for item in generator_result.skipped)

    deleted: list[str] = []
    for rel_path in plan.deletions:
        target_path = bundle_root / plan.target_for(rel_path).member
        target_path.unlink(missing_ok=True)
        deleted.append(rel_path)

    # `apply_generators` commits each regenerated `Document` in place (same
    # object `reloaded.concepts` already holds), so `reloaded` is current for
    # creates and regenerations without a third walk. A deletion is the one
    # thing it cannot self-correct for: `Bundle.concepts` is a
    # `MappingProxyType` built once by the walk, so the deleted concept would
    # still read as a member -- `update_index()` would then neither prune its
    # entry nor notice the file backing it is gone. Only that case pays for
    # the extra reload.
    final_bundle = load_bundle(bundle_root) if plan.deletions else reloaded
    directories = tuple(dict.fromkeys(directory for context in contexts for directory in affected_directories(context)))
    index_updates = (
        update_index(final_bundle, directories=directories, create_missing=True, dry_run=False) if directories else ()
    )

    return MirrorResult(
        repo=plan.repo,
        moved=move_result.moved,
        created=tuple(created),
        regenerated=regenerated,
        deleted=tuple(deleted),
        declined_deletions=plan.declined_deletions,
        index_updates=index_updates,
        failed=(
            tuple(f"{failure.path}: {failure.kind}: {failure.error}" for failure in move_result.failed)
            + generator_failed
        ),
    )


__all__ = ["apply_mirror", "preflight_mirror_live"]
