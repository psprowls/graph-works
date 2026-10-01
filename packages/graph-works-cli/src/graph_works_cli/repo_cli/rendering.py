"""Human text for `gw repo` results. JSON goes through `graph_works_wire.repo`."""

from __future__ import annotations

from graph_works_core.repositories.adopt import RepoAdoptResult
from graph_works_core.repositories.commands import RepoAddResult, RepoAdvanceResult, RepoRestoreResult


def add_text(result: RepoAddResult) -> str:
    verb = "would add" if result.dry_run else "added"
    lines = [
        f"{verb} {result.name}: {result.url} @ {(result.describe or result.commit or '')[:40]} "
        + (f"(managed; checkout {result.checkout})" if result.managed else f"(tracking {result.track})")
    ]
    lines += [f"  {path}" for path in result.paths]
    lines += [f"! {warning}" for warning in result.warnings]
    return "\n".join(lines)


def restore_text(result: RepoRestoreResult) -> str:
    if not result.outcomes:
        return "no repositories to restore"
    prefix = "would be " if result.dry_run else ""
    return "\n".join(
        f"{outcome.name}: refused ({outcome.refusal.code}) — {outcome.refusal.detail}"
        if outcome.refusal
        else f"{outcome.name}: {prefix}{outcome.outcome}"
        + (f", {outcome.checkout}" if outcome.checkout is not None else "")
        for outcome in result.outcomes
    )


def advance_text(result: RepoAdvanceResult) -> str:
    if result.outcome == "up-to-date":
        return f"{result.name}: up to date at {(result.commit or '')[:7]}"
    facts = result.range
    verb = "would advance" if result.dry_run else "advanced"
    head = f"{verb} {result.name}: {(result.previous or '')[:7]}..{(result.commit or '')[:7]}"
    if result.managed:
        scan_status = "scan planned" if result.dry_run else "rescanned"
        head += f", {result.commits} commit(s), {scan_status}"
        lines = [head]
        lines += [f"  {path}" for path in result.paths]
        lines += [f"! {warning}" for warning in result.warnings]
        return "\n".join(lines)
    if facts is not None:
        head += f", {facts.commits} commits, {facts.files_changed} files" + (
            ", rewritten upstream" if facts.rewritten else ""
        )
    lines = [head, f"{len(result.flagged)} page(s) flagged"]
    lines += [f"  {path}" for path in result.paths]
    lines += [f"  {page.page}" for page in result.flagged]
    lines += [f"  proposal: {path}" for path in result.proposals]
    lines += [f"  skipped: {skip}" for skip in result.skipped]
    lines += [f"! {warning}" for warning in result.warnings]
    return "\n".join(lines)


def adopt_text(result: RepoAdoptResult) -> str:
    verb = "would adopt" if result.dry_run else "adopted"
    lines = [
        f"{verb} {result.name}: {result.source} -> {result.clone} @ {(result.commit or '')[:7]}",
        f"checkout: {result.checkout} on {result.track} ({'created' if result.checkout_created else 'reused'})",
        f"repaired worktrees: {len(result.repaired)}",
    ]
    if result.commit_outcome is not None:
        lines.append(
            f"workspace commit: {result.commit_outcome.status} {(result.commit_outcome.sha or '')[:7]} "
            f"{result.commit_outcome.subject}".rstrip()
        )
    lines.extend(f"warning: {warning}" for warning in result.warnings)
    return "\n".join(lines)
