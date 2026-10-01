"""The `repository.*` lint rules: a clone stays detached, clean, faithful to its `url`, and free of worktrees under
the bundle; a managed repository declares a checkout on its `track` and is not left behind it (design §5.4).

The module is named for the code prefix, the catalog convention. The rules are pure over `RepositoryFacts`, which
`gather` reads with the git runner. graph-works-core gathers once per lint and passes the facts in by closure, the
idiom of its deferred `sync` rule.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from okf_io import Document, Finding, Rule, RuleContext
from okf_io.validate import Severity

from repositories_okf.git import (
    Git,
    GitFailure,
    common_dir,
    current_branch,
    head_state,
    is_clean,
    origin_url,
    rev_count,
    worktree_list,
    worktree_root,
)
from repositories_okf.lifecycle import MANAGED_TYPE, clone_path, page_path
from repositories_okf.pin import read_pin

_SPEC = "repositories_okf.repository"

CODES: tuple[str, ...] = (
    "repository.not-detached",
    "repository.dirty",
    "repository.url-mismatch",
    "repository.worktree-in-bundle",
    "repository.checkout-undeclared",
    "repository.checkout-invalid",
    "repository.unmaterialized",
    "repository.behind-track",
)


@dataclass(frozen=True, slots=True)
class RepositoryFacts:
    name: str
    type: str
    url: str | None
    track: str | None
    pin: str | None
    clone_present: bool
    declared: bool
    detached: bool | None = None
    clean: bool | None = None
    origin: str | None = None
    origin_probe_failed: bool = False
    worktrees_in_bundle: tuple[str, ...] = ()
    checkout: str | None = None
    checkout_linked: bool | None = None
    checkout_branch: str | None = None
    ahead: int | None = None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def gather(
    git: Git, *, bundle_dir: Path, name: str, page: Document, declared: bool, checkout: Path | None
) -> RepositoryFacts:
    data = page.fm_data(dates="iso")
    pin = read_pin(page)
    facts = RepositoryFacts(
        name=name,
        type=str(data.get("type") or ""),
        url=_text(data.get("url")),
        track=_text(data.get("track")),
        pin=pin.commit if pin else None,
        clone_present=(bundle_dir / clone_path(name)).is_dir(),
        declared=declared,
        checkout=checkout.as_posix() if checkout is not None else None,
    )
    if not facts.clone_present:
        return facts
    clone = bundle_dir / clone_path(name)
    head, clean, origin, listed = (
        head_state(git, clone),
        is_clean(git, clone),
        origin_url(git, clone),
        worktree_list(git, clone),
    )
    root, clone_root = bundle_dir.resolve(), clone.resolve()
    linked = (
        ()
        if isinstance(listed, GitFailure)
        else tuple(entry for entry in listed if Path(entry.path).resolve() != clone_root)
    )
    in_bundle = tuple(
        sorted(
            Path(entry.path).resolve().relative_to(root).as_posix()
            for entry in linked
            if Path(entry.path).resolve().is_relative_to(root)
        )
    )
    match = next(
        (entry for entry in linked if checkout is not None and Path(entry.path).resolve() == checkout.resolve()), None
    )
    checkout_linked: bool | None = None
    checkout_branch: str | None = None
    if checkout is not None and not isinstance(listed, GitFailure):
        checkout_linked = False
        if match is not None and checkout.is_dir() and not checkout.is_symlink() and (checkout / ".git").exists():
            actual_root = worktree_root(git, checkout)
            actual_common = common_dir(git, checkout)
            clone_common = common_dir(git, clone)
            branch = current_branch(git, checkout)
            probes = (actual_root, actual_common, clone_common, branch)
            if any(isinstance(probe, GitFailure) for probe in probes):
                checkout_linked = None
            else:
                checkout_linked = actual_root == checkout.resolve() and actual_common == clone_common
                checkout_branch = branch if isinstance(branch, str) else None
    ahead = None
    if facts.type == MANAGED_TYPE and facts.pin and facts.track:
        counted = rev_count(git, clone, facts.pin, f"refs/heads/{facts.track}")
        ahead = counted if isinstance(counted, int) else None
    return replace(
        facts,
        detached=None if isinstance(head, GitFailure) else head.detached,
        clean=None if isinstance(clean, GitFailure) else clean,
        origin=None if isinstance(origin, GitFailure) else origin,
        origin_probe_failed=isinstance(origin, GitFailure),
        worktrees_in_bundle=in_bundle,
        checkout_linked=checkout_linked,
        checkout_branch=checkout_branch,
        ahead=ahead,
    )


def _finding(code: str, severity: Severity, name: str, message: str) -> Finding:
    return Finding(code=code, severity=severity, message=message, spec=_SPEC, path=page_path(name))


def _broken_into(ctx: RuleContext, name: str) -> int:
    prefix = clone_path(name)
    return sum(
        1
        for link in ctx.links.broken
        if link.target is not None and (link.target == prefix or link.target.startswith(prefix + "/"))
    )


def _check(facts: RepositoryFacts, ctx: RuleContext) -> Iterable[Finding]:
    name, managed = facts.name, facts.type == MANAGED_TYPE
    if facts.clone_present:
        if facts.detached is False:
            yield _finding(
                "repository.not-detached",
                "error",
                name,
                f"{clone_path(name)} has a branch checked out; run `gw repo restore {name}`",
            )
        if facts.clean is False:
            yield _finding(
                "repository.dirty", "error", name, f"{clone_path(name)} has tracked changes or untracked files"
            )
        if not facts.origin_probe_failed and facts.origin != facts.url:
            yield _finding(
                "repository.url-mismatch",
                "error",
                name,
                f"the clone's origin is {facts.origin!r}, the page's url is {facts.url!r}",
            )
    else:
        broken = _broken_into(ctx, name)
        yield _finding(
            "repository.unmaterialized",
            "warn",
            name,
            f"{clone_path(name)} is absent ({broken} broken link(s) into it); run `gw repo restore {name}`",
        )
    for path in facts.worktrees_in_bundle:
        yield _finding(
            "repository.worktree-in-bundle",
            "error",
            name,
            f"a worktree of {clone_path(name)} is linked at {path}, inside the bundle",
        )
    if not managed:
        return
    if facts.checkout is None:
        why = "declares it without a checkout" if facts.declared else "has no repositories entry for it"
        yield _finding(
            "repository.checkout-undeclared", "error", name, f"the manifest {why}; add repositories.{name}.checkout"
        )
    elif facts.checkout_linked is False or (facts.checkout_linked and facts.checkout_branch != facts.track):
        state = (
            "is not a worktree of the clone"
            if facts.checkout_linked is False
            else f"is on {facts.checkout_branch!r}, not {facts.track!r}"
        )
        yield _finding("repository.checkout-invalid", "error", name, f"{facts.checkout} {state}")
    if facts.ahead:
        yield _finding(
            "repository.behind-track",
            "warn",
            name,
            f"{facts.track} is {facts.ahead} commit(s) ahead of the pin; run `gw repo advance {name}`",
        )


def repository_rule(facts: Sequence[RepositoryFacts], *, codes: frozenset[str] | None = None) -> Rule:
    """One rule over pre-gathered facts. *codes* narrows it (`gw work lint` wants only `behind-track`)."""

    def rule(ctx: RuleContext) -> Iterable[Finding]:
        for fact in facts:
            for finding in _check(fact, ctx):
                if codes is None or finding.code in codes:
                    yield finding

    return rule


__all__ = ["CODES", "RepositoryFacts", "gather", "repository_rule"]
